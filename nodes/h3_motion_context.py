"""Prompt Studio-owned MiniMax H3 audiovisual continuation primitives.

Current ComfyUI owns arbitrary-position MiniMax H3 guides. Video Studio saves
an exact compact AV latent tail, places the intact video and audio runs at the
head of the child timeline through that native contract, and trims the repeated
head after decode so the stored segment begins immediately after its parent.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import tempfile

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file


FPS = 24.0
AUDIO_LATENT_HZ = 40.0
FRAME_RESCALE = 5.0 / 3.0
FRAME_SPANS = (1, 4, 4, 4, 4)
DEFAULT_CONTEXT_FRAMES = 22
CONTEXT_FRAME_OPTIONS = (5, 22, 39, 56)

_LOG = logging.getLogger("promptstudio_video.motion_context")


def pixel_frames_for_steps(step_count):
    return sum(FRAME_SPANS[index % len(FRAME_SPANS)] for index in range(int(step_count)))


def step_offsets(step_count):
    offsets = []
    position = 0
    for index in range(int(step_count)):
        offsets.append(position)
        position += FRAME_SPANS[index % len(FRAME_SPANS)]
    return offsets


def steps_for_frames(frame_count):
    covered = 0
    steps = 0
    while covered < int(frame_count):
        covered += FRAME_SPANS[steps % len(FRAME_SPANS)]
        steps += 1
    return steps if covered == int(frame_count) else None


def _streams(latent):
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("Prompt Studio continuation expected an H3 AV latent")
    samples = latent["samples"]
    if hasattr(samples, "unbind"):
        values = list(samples.unbind())
    elif isinstance(samples, (list, tuple)):
        values = list(samples)
    else:
        raise ValueError(f"Prompt Studio continuation cannot unpack latent type {type(samples)!r}")
    if len(values) < 2:
        raise ValueError("Prompt Studio continuation requires both H3 video and audio latent streams")
    video, audio = values[:2]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if audio.ndim == 3:
        audio = audio.unsqueeze(0)
    if video.ndim != 5 or audio.ndim != 4:
        raise ValueError(
            "Prompt Studio continuation received invalid H3 latent shapes "
            f"{tuple(video.shape)} and {tuple(audio.shape)}"
        )
    return video, audio


def compact_tail(latent, frame_count=DEFAULT_CONTEXT_FRAMES):
    frame_count = int(frame_count)
    steps = steps_for_frames(frame_count)
    if frame_count not in CONTEXT_FRAME_OPTIONS or steps is None:
        raise ValueError("H3 context must use 5, 22, 39, or 56 frames")
    video, audio = _streams(latent)
    if steps > video.shape[2]:
        raise ValueError("The generated video latent is shorter than the requested continuation context")
    start = int(video.shape[2]) - steps
    if start % len(FRAME_SPANS):
        raise ValueError("The H3 latent tail is off the native temporal phase grid")
    audio_steps = max(1, round(frame_count / FPS * AUDIO_LATENT_HZ))
    if audio_steps > audio.shape[-1]:
        raise ValueError("The generated audio latent is shorter than the requested continuation context")
    return (
        video[:1, :, start:].detach().cpu().contiguous(),
        audio[:1, ..., -audio_steps:].detach().cpu().contiguous(),
    )


def _safe_identifier(value, label):
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError(f"{label} is invalid")
    return value


def context_relative_path(project_id, generation_id):
    project_id = _safe_identifier(project_id, "Project identifier")
    generation_id = _safe_identifier(generation_id, "Generation identifier")
    return f"video/PromptStudio_Video/latents/{project_id}/{generation_id}.safetensors"


def _output_path(relative_path, must_exist=False):
    import folder_paths

    root = os.path.abspath(folder_paths.get_output_directory())
    relative = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    path = os.path.abspath(os.path.join(root, *[part for part in relative.split("/") if part]))
    try:
        inside = os.path.commonpath((root, path)) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("Continuation latent path escapes the ComfyUI output directory")
    if must_exist and not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path


def native_guides_available():
    """Return whether ComfyUI owns arbitrary-position MiniMax H3 guides."""
    try:
        import comfy.ldm.minimax.model as minimax_model
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide

        parameters = inspect.signature(minimax_model.PackedLayout.__init__).parameters
    except (ImportError, TypeError, ValueError):
        return False
    return (
        MiniMaxH3AddGuide is not None
        and "keyframes" in parameters
        and "frame_count" not in parameters
    )


def require_native_guides():
    if not native_guides_available():
        raise RuntimeError(
            "Prompt Studio continuation requires current ComfyUI native MiniMax H3 Add Guide support. "
            "Update ComfyUI master and restart it before continuing a video."
        )


def av_clock_metadata(latent):
    video, audio = _streams(latent)
    frames = pixel_frames_for_steps(int(video.shape[2]))
    audio_steps = int(audio.shape[-1])
    overhang = float(audio_steps) - FRAME_RESCALE * float(frames)
    if not -0.500001 < overhang < 0.500001:
        raise ValueError(
            f"The H3 audio/video clocks are inconsistent ({audio_steps} audio steps for {frames} frames)"
        )
    return {
        "source_video_frames": frames,
        "source_audio_steps": audio_steps,
        "audio_overhang_steps": overhang,
    }


def _audio_start_offset_steps(context_frames, audio_steps, source_overhang_steps):
    return (
        float(source_overhang_steps)
        + FRAME_RESCALE * int(context_frames)
        - int(audio_steps)
    )


def _continuation_guides(video_tail, audio_tail, audio_start_offset_steps):
    guides = [{"resolved_frame_index": 0.0, "latent": video_tail}]
    audio_start = float(audio_start_offset_steps) / FRAME_RESCALE
    if abs(audio_start) < 1e-9:
        guides[0]["audio_latent"] = audio_tail
    else:
        guides.append({"resolved_frame_index": audio_start, "audio_latent": audio_tail})
    return guides


def _apply_native_guides(conditioning, guides, context_frames):
    output = []
    for embedding, extra in conditioning:
        metadata = extra.copy()
        retained = []
        for guide in metadata.get("minimax_keyframes") or []:
            position = float(guide.get("resolved_frame_index", 0))
            if position >= int(context_frames):
                retained.append(guide)
        metadata["minimax_keyframes"] = retained + guides
        output.append([embedding, metadata])
    return output


def _load_saved_tail(relative_path):
    path = _output_path(relative_path, must_exist=True)
    with safe_open(path, framework="pt", device="cpu") as handle:
        metadata = dict(handle.metadata() or {})
    values = load_file(path)
    if "video" not in values or "audio" not in values:
        raise ValueError("Saved Prompt Studio context is missing its video or audio stream")
    return {"samples": [values["video"], values["audio"]], "metadata": metadata}


def _fallback_tail(video_path, video_vae, audio_vae, target_video, frame_count):
    from comfy_extras.nodes_video import LoadVideo

    try:
        result = LoadVideo.execute(file=video_path).result
    except TypeError:
        result = LoadVideo.execute(video=video_path).result
    if not result:
        raise ValueError("ComfyUI could not load the parent video for continuation")
    components = result[0].get_components()
    images = components.images
    source_fps = float(components.frame_rate)
    if images is None or len(images) < frame_count or source_fps <= 0:
        raise ValueError("The parent video is too short for continuation context")
    source_duration = len(images) / source_fps
    start = source_duration - frame_count / FPS
    indices = torch.clamp(
        ((start + torch.arange(frame_count, dtype=torch.float64) / FPS) * source_fps).round().long(),
        0,
        len(images) - 1,
    )
    tail = images[indices]
    target_height = int(target_video.shape[3]) * 16
    target_width = int(target_video.shape[4]) * 16
    if int(tail.shape[1]) != target_height or int(tail.shape[2]) != target_width:
        import comfy.utils

        tail = comfy.utils.common_upscale(
            tail[..., :3].movedim(-1, 1), target_width, target_height, "lanczos", "center"
        ).movedim(1, -1)
    video = video_vae.encode(tail)
    audio_value = components.audio
    target_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
    samples = max(1, round(frame_count / FPS * target_rate))
    if not audio_value:
        waveform = torch.zeros((1, 2, samples), dtype=torch.float32)
    else:
        waveform = audio_value["waveform"]
        sample_rate = int(audio_value["sample_rate"])
        if sample_rate != target_rate:
            import torchaudio

            waveform = torchaudio.functional.resample(waveform, sample_rate, target_rate)
        waveform = waveform[..., -samples:]
        if waveform.shape[-1] < samples:
            waveform = torch.nn.functional.pad(waveform, (samples - waveform.shape[-1], 0))
    audio = audio_vae.encode(waveform[:1].movedim(1, -1))
    context = {"samples": [video, audio]}
    context["metadata"] = av_clock_metadata(context)
    return context


class PromptStudioH3MotionContext:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "latent": ("LATENT",),
                "video_vae": ("VAE",),
                "audio_vae": ("VAE",),
                "context_latent_path": ("STRING", {"default": ""}),
                "context_video": ("STRING", {"default": ""}),
                "context_frames": ("INT", {"default": DEFAULT_CONTEXT_FRAMES, "min": 5, "max": 56}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "INT")
    RETURN_NAMES = ("conditioning", "latent", "trim_frames")
    FUNCTION = "apply"
    CATEGORY = "Prompt Studio/Video"

    def apply(
        self, conditioning, latent, video_vae, audio_vae,
        context_latent_path, context_video, context_frames=DEFAULT_CONTEXT_FRAMES,
    ):
        require_native_guides()
        context_frames = int(context_frames)
        target_video, target_audio = _streams(latent)
        steps = steps_for_frames(context_frames)
        if context_frames not in CONTEXT_FRAME_OPTIONS or steps is None:
            raise ValueError("H3 context must use 5, 22, 39, or 56 frames")
        target_frames = pixel_frames_for_steps(target_video.shape[2])
        if context_frames >= target_frames:
            raise ValueError("Continuation context must be shorter than the sampled extension")

        context = None
        source = ""
        try:
            context = _load_saved_tail(context_latent_path)
            source = "saved latent"
            saved_video, saved_audio = _streams(context)
            if (context.get("metadata") or {}).get("format") != "promptstudio_h3_av_tail_v2":
                raise ValueError("Saved context predates exact H3 audio-clock metadata")
            if saved_video.shape[2] < steps or saved_audio.shape[-1] < round(
                context_frames / FPS * AUDIO_LATENT_HZ
            ):
                raise ValueError("Saved context predates the current continuation window")
        except (FileNotFoundError, ValueError) as saved_error:
            if not context_video:
                raise ValueError(
                    "The parent generation has no usable saved context or fallback video"
                ) from saved_error
            context = _fallback_tail(
                context_video, video_vae, audio_vae, target_video, context_frames
            )
            source = "decoded video fallback"
        source_video, source_audio = _streams(context)
        if source_video.shape[1] != target_video.shape[1] or source_video.shape[3:] != target_video.shape[3:]:
            raise ValueError("Parent and extension H3 latents use different channels or resolution")
        if source_audio.shape[1:3] != target_audio.shape[1:3]:
            raise ValueError("Parent and extension H3 audio latents are incompatible")
        if source_video.shape[2] < steps:
            raise ValueError("Parent context does not contain a complete H3 continuation window")
        start = int(source_video.shape[2]) - steps
        if start % len(FRAME_SPANS):
            raise ValueError("Parent context is off the native H3 temporal phase grid")
        video_tail = source_video[:1, :, start:].clone()
        audio_steps = max(1, round(context_frames / FPS * AUDIO_LATENT_HZ))
        if source_audio.shape[-1] < audio_steps:
            raise ValueError("Parent context does not contain the matching H3 audio window")
        audio_tail = source_audio[:1, ..., -audio_steps:].clone()
        raw_overhang = (context.get("metadata") or {}).get("audio_overhang_steps", 0)
        try:
            overhang = float(raw_overhang)
        except (TypeError, ValueError):
            overhang = 0.0
        if not -0.500001 < overhang < 0.500001:
            raise ValueError("Saved continuation audio-clock metadata is invalid")
        audio_start_offset = _audio_start_offset_steps(
            context_frames, audio_steps, overhang
        )
        guides = _continuation_guides(video_tail, audio_tail, audio_start_offset)
        output = _apply_native_guides(conditioning, guides, context_frames)
        _LOG.info(
            "Prompt Studio native continuation guides: %d frames, %d video steps, "
            "%d audio steps, audio start offset %.3f from %s",
            context_frames,
            steps,
            audio_steps,
            audio_start_offset,
            source,
        )
        return output, latent, context_frames


class PromptStudioH3SaveContext:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT",),
                "project_id": ("STRING", {"default": "project"}),
                "generation_id": ("STRING", {"default": "generation"}),
                "context_frames": ("INT", {"default": DEFAULT_CONTEXT_FRAMES, "min": 5, "max": 56}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("context_path",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "Prompt Studio/Video"

    def save(self, latent, project_id, generation_id, context_frames=DEFAULT_CONTEXT_FRAMES):
        relative = context_relative_path(project_id, generation_id)
        target = _output_path(relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        video, audio = compact_tail(latent, int(context_frames))
        clock = av_clock_metadata(latent)
        descriptor, temporary = tempfile.mkstemp(
            prefix=os.path.basename(target) + ".", suffix=".tmp.safetensors", dir=os.path.dirname(target)
        )
        os.close(descriptor)
        try:
            save_file(
                {"video": video, "audio": audio},
                temporary,
                metadata={
                    "format": "promptstudio_h3_av_tail_v2",
                    "context_frames": str(int(context_frames)),
                    "source_video_frames": str(clock["source_video_frames"]),
                    "source_audio_steps": str(clock["source_audio_steps"]),
                    "audio_overhang_steps": format(clock["audio_overhang_steps"], ".17g"),
                },
            )
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return (relative,)


class PromptStudioH3TrimContext:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "audio": ("AUDIO",),
                "trim_frames": ("INT", {"default": DEFAULT_CONTEXT_FRAMES, "min": 0, "max": 56}),
                "fps": ("FLOAT", {"default": FPS, "min": 1.0, "max": 120.0}),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "trim"
    CATEGORY = "Prompt Studio/Video"

    def trim(self, images, audio, trim_frames=DEFAULT_CONTEXT_FRAMES, fps=FPS):
        count = int(trim_frames)
        total = int(images.shape[0])
        if count < 0 or count >= total:
            raise ValueError(f"Cannot trim {count} context frames from a {total}-frame video")
        output_images = images[count:]
        waveform = audio["waveform"]
        sample_rate = int(audio["sample_rate"])
        cut = round(count / float(fps) * sample_rate)
        if cut >= waveform.shape[-1]:
            raise ValueError("Continuation audio is shorter than its repeated context head")
        waveform = waveform[..., cut:]
        wanted = round(len(output_images) / float(fps) * sample_rate)
        if waveform.shape[-1] > wanted:
            waveform = waveform[..., :wanted]
        elif waveform.shape[-1] < wanted:
            waveform = torch.nn.functional.pad(waveform, (0, wanted - waveform.shape[-1]))
        return output_images, {"waveform": waveform, "sample_rate": sample_rate}


NODE_CLASS_MAPPINGS = {
    "PSV_H3MotionContext": PromptStudioH3MotionContext,
    "PSV_H3SaveContext": PromptStudioH3SaveContext,
    "PSV_H3TrimContext": PromptStudioH3TrimContext,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PSV_H3MotionContext": "Prompt Studio H3 Motion Context",
    "PSV_H3SaveContext": "Prompt Studio Save H3 Context",
    "PSV_H3TrimContext": "Prompt Studio Trim H3 Context",
}
