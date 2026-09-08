"""Prompt Studio-owned MiniMax H3 audiovisual continuation primitives.

Current ComfyUI owns nested video/audio denoise masks for MiniMax H3. Video
Studio saves an exact compact AV latent tail, copies its 39-frame / 65-audio-step
run into the child target, protects the picture exactly, and releases only the
last eight audio ticks with the maintained Soft AV half-cosine recipe.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import tempfile

import torch
from safetensors import safe_open, SafetensorError
from safetensors.torch import save_file


FPS = 24.0
AUDIO_LATENT_HZ = 40.0
FRAME_RESCALE = 5.0 / 3.0
FRAME_SPANS = (1, 4, 4, 4, 4)
DEFAULT_CONTEXT_FRAMES = 39
CONTEXT_FRAME_OPTIONS = (5, 22, 39, 56)
SOFT_AV_AUDIO_FEATHER_STEPS = 8
SOFT_AV_FORMAT = "promptstudio_h3_av_tail_v3"

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


def native_masks_available():
    """Return whether ComfyUI owns MiniMax H3 nested fractional masks."""
    try:
        import comfy.model_base as model_base
        import comfy.nested_tensor as nested_tensor
        import comfy.ldm.minimax.model as minimax_model
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide

        parameters = inspect.signature(minimax_model.PackedLayout.__init__).parameters
    except (ImportError, TypeError, ValueError):
        return False
    return (
        MiniMaxH3AddGuide is not None
        and "keyframes" in parameters
        and "frame_count" not in parameters
        and getattr(nested_tensor, "NestedTensor", None) is not None
        and callable(getattr(getattr(model_base, "MiniMaxH3", None), "_denoise_mask_values", None))
        and callable(getattr(getattr(model_base, "MiniMaxH3", None), "scale_latent_inpaint", None))
    )


def native_guides_available():
    """Compatibility name retained for routes and older focused tests."""
    return native_masks_available()


def require_native_masks():
    if not native_masks_available():
        raise RuntimeError(
            "Prompt Studio Soft AV continuation requires current ComfyUI native MiniMax H3 mask support. "
            "Update ComfyUI master and restart it before continuing a video."
        )


def require_native_guides():
    """Compatibility alias for persisted workflows created before Soft AV."""
    return require_native_masks()


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


def _drop_prefix_guides(conditioning, context_frames):
    """Remove native guides that conflict with the protected target prefix."""
    output = []
    for embedding, extra in conditioning:
        metadata = extra.copy()
        retained = []
        for guide in metadata.get("minimax_keyframes") or []:
            position = float(guide.get("resolved_frame_index", guide.get("frame_index", 0)))
            if not 0 <= position < int(context_frames):
                retained.append(guide)
        if "minimax_keyframes" in metadata:
            metadata["minimax_keyframes"] = retained
        output.append([embedding, metadata])
    return output


def _existing_mask_streams(latent, video, audio):
    mask = latent.get("noise_mask") if isinstance(latent, dict) else None
    if mask is None:
        return (
            torch.ones(
                (1, 1, int(video.shape[2]), int(video.shape[3]), int(video.shape[4])),
                device=video.device,
                dtype=torch.float32,
            ),
            torch.ones(
                (1, 1, int(audio.shape[2]), int(audio.shape[3])),
                device=audio.device,
                dtype=torch.float32,
            ),
        )
    if hasattr(mask, "unbind"):
        parts = list(mask.unbind())
    elif isinstance(mask, (list, tuple)):
        parts = list(mask)
    else:
        raise ValueError("Existing H3 noise mask is not a nested video/audio mask")
    if len(parts) < 2:
        raise ValueError("Existing H3 noise mask is missing its audio stream")
    video_shape = (1, 1, int(video.shape[2]), int(video.shape[3]), int(video.shape[4]))
    audio_shape = (1, 1, int(audio.shape[2]), int(audio.shape[3]))
    try:
        return (
            torch.broadcast_to(parts[0], video_shape).clone().float(),
            torch.broadcast_to(parts[1], audio_shape).clone().float(),
        )
    except RuntimeError as exc:
        raise ValueError("Existing H3 video/audio masks do not match the continuation target") from exc


def soft_av_masks(latent, video_steps, audio_steps):
    """Compose the maintained exact-picture, eight-tick audio-release masks."""
    video, audio = _streams(latent)
    video_mask, audio_mask = _existing_mask_streams(latent, video, audio)
    video_steps = int(video_steps)
    audio_steps = int(audio_steps)
    video_mask[:, :, :video_steps] = 0.0
    feather = min(SOFT_AV_AUDIO_FEATHER_STEPS, audio_steps)
    hard_steps = audio_steps - feather
    audio_mask[..., :hard_steps] = 0.0
    if feather:
        indices = torch.arange(
            1,
            feather + 1,
            device=audio_mask.device,
            dtype=audio_mask.dtype,
        )
        ramp = 0.5 - 0.5 * torch.cos(torch.pi * indices / float(feather))
        audio_mask[..., hard_steps:audio_steps] = torch.minimum(
            audio_mask[..., hard_steps:audio_steps],
            ramp.view(1, 1, 1, feather),
        )
    return video_mask, audio_mask


def _load_saved_tail(relative_path):
    path = _output_path(relative_path, must_exist=True)
    if os.path.getsize(path) > 128 * 1024 * 1024:
        raise ValueError("Saved context exceeds the 128 MiB file budget")
    try:
        with safe_open(path, framework="pt", device="cpu") as handle:
            metadata = dict(handle.metadata() or {})
            if set(handle.keys()) != {"video", "audio"}:
                raise ValueError("Saved context must contain only its video and audio streams")
            if metadata.get("format") not in {"promptstudio_h3_av_tail_v2", SOFT_AV_FORMAT}:
                raise ValueError("Saved context has an unsupported format")
            shapes = {key: handle.get_slice(key).get_shape() for key in ("video", "audio")}
            video_shape, audio_shape = shapes["video"], shapes["audio"]
            if (len(video_shape) != 5 or len(audio_shape) != 4 or
                any(int(n) <= 0 for shape in shapes.values() for n in shape) or
                video_shape[0] != 1 or audio_shape[0] != 1 or video_shape[1] != 24 or
                audio_shape[1:3] != [32, 2] or video_shape[2] > 17 or audio_shape[-1] > 94):
                raise ValueError("Saved context exceeds the compact H3 AV shape budget")
            import math
            if sum(math.prod(shape) * 4 for shape in shapes.values()) > 128 * 1024 * 1024:
                raise ValueError("Saved context exceeds the 128 MiB latent budget")
            frames = int(metadata.get("context_frames", 0))
            if (frames not in CONTEXT_FRAME_OPTIONS or steps_for_frames(frames) != video_shape[2] or
                round(frames / FPS * AUDIO_LATENT_HZ) != audio_shape[-1]):
                raise ValueError("Saved context metadata does not match its tensor clock")
            source_frames = int(metadata.get("source_video_frames", 0))
            source_audio = int(metadata.get("source_audio_steps", 0))
            overhang = float(metadata.get("audio_overhang_steps", "nan"))
            if (not 0 < source_frames <= 86400 or source_audio <= 0 or
                not math.isfinite(overhang) or abs(overhang) >= .500001 or
                abs(source_audio - source_frames * FRAME_RESCALE - overhang) > 1e-6):
                raise ValueError("Saved context source-clock metadata is inconsistent")
            for key, shape in shapes.items():
                if metadata.get(key + "_shape") != "x".join(str(n) for n in shape):
                    raise ValueError("Saved context shape metadata is inconsistent")
                if handle.get_slice(key).get_dtype() not in {"F16", "BF16", "F32"}:
                    raise ValueError("Saved context must use a supported floating-point dtype")
            # Header checks precede tensor materialization; load through the same handle.
            values = {key: handle.get_tensor(key) for key in shapes}
    except SafetensorError as exc:
        raise ValueError("Saved context has an invalid safetensors header") from exc
    return {"samples": [values["video"], values["audio"]], "metadata": metadata}


def _fallback_tail(video_path, video_vae, audio_vae, target_video, frame_count):
    import folder_paths
    try:
        from ..video.media_budget import video_range, audio_array, geometry
        from ..video.continuation import probe_video
    except ImportError:
        from video.media_budget import video_range, audio_array, geometry
        from video.continuation import probe_video
    path = folder_paths.get_annotated_filepath(video_path)
    info = probe_video(path)
    start = info["duration"] - frame_count / FPS
    if start < 0:
        raise ValueError("The parent video is too short for continuation context")
    tail, _, _ = video_range(path, start, info["duration"], FPS)
    target_height = int(target_video.shape[3]) * 16
    target_width = int(target_video.shape[4]) * 16
    geometry(target_width, target_height, frame_count)
    if int(tail.shape[1]) != target_height or int(tail.shape[2]) != target_width:
        import comfy.utils

        tail = comfy.utils.common_upscale(
            tail[..., :3].movedim(-1, 1), target_width, target_height, "lanczos", "center"
        ).movedim(1, -1)
    video = video_vae.encode(tail)
    target_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
    waveform = torch.from_numpy(audio_array(path, target_rate, start, info["duration"]))[None]
    if waveform.shape[1] == 1:
        waveform = waveform.repeat(1, 2, 1)
    elif waveform.shape[1] > 2:
        waveform = waveform[:, :2]
    audio = audio_vae.encode(waveform[:1].movedim(1, -1))
    wanted_audio_steps = round(frame_count / FPS * AUDIO_LATENT_HZ)
    if int(audio.shape[-1]) < wanted_audio_steps:
        raise ValueError(
            f"The fallback audio encoded to {audio.shape[-1]} H3 steps; "
            f"the {frame_count}-frame Soft AV prefix requires {wanted_audio_steps}"
        )
    audio = audio[:1, ..., -wanted_audio_steps:].contiguous()
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
        require_native_masks()
        context_frames = int(context_frames)
        target_video, target_audio = _streams(latent)
        if int(target_video.shape[0]) != 1 or int(target_audio.shape[0]) != 1:
            raise ValueError("Prompt Studio Soft AV continuation supports H3 batch size 1")
        steps = steps_for_frames(context_frames)
        if context_frames != DEFAULT_CONTEXT_FRAMES or steps is None:
            raise ValueError("Prompt Studio Soft AV continuation requires exactly 39 context frames")
        target_frames = pixel_frames_for_steps(target_video.shape[2])
        if context_frames >= target_frames:
            raise ValueError("Continuation context must be shorter than the sampled extension")
        expected_target_audio = round(target_frames / FPS * AUDIO_LATENT_HZ)
        if int(target_audio.shape[-1]) != expected_target_audio:
            raise ValueError(
                f"H3 target has {target_audio.shape[-1]} audio steps for {target_frames} frames; "
                f"Soft AV requires the exact {expected_target_audio}-step shared clock"
            )

        context = None
        source = ""
        try:
            context = _load_saved_tail(context_latent_path)
            source = "saved latent"
            saved_video, saved_audio = _streams(context)
            if (context.get("metadata") or {}).get("format") not in {
                "promptstudio_h3_av_tail_v2",
                SOFT_AV_FORMAT,
            }:
                raise ValueError("Saved context predates exact H3 audio-clock metadata")
            if saved_video.shape[2] < steps or saved_audio.shape[-1] < round(
                context_frames / FPS * AUDIO_LATENT_HZ
            ):
                raise ValueError("Saved context does not contain the 39-frame Soft AV window")
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
        if int(source_video.shape[0]) != 1 or int(source_audio.shape[0]) != 1:
            raise ValueError("Parent continuation context must use H3 batch size 1")
        if source_video.shape[2] < steps:
            raise ValueError("Parent context does not contain a complete H3 continuation window")
        start = int(source_video.shape[2]) - steps
        if start % len(FRAME_SPANS):
            raise ValueError("Parent context is off the native H3 temporal phase grid")
        video_tail = source_video[:1, :, start:].to(
            device=target_video.device, dtype=target_video.dtype
        )
        audio_steps = max(1, round(context_frames / FPS * AUDIO_LATENT_HZ))
        if source_audio.shape[-1] < audio_steps:
            raise ValueError("Parent context does not contain the matching H3 audio window")
        audio_tail = source_audio[:1, ..., -audio_steps:].to(
            device=target_audio.device, dtype=target_audio.dtype
        )
        out_video = target_video.clone()
        out_audio = target_audio.clone()
        out_video[:, :, :steps] = video_tail
        out_audio[..., :audio_steps] = audio_tail
        out_latent = latent.copy()
        import comfy.nested_tensor

        out_latent["samples"] = comfy.nested_tensor.NestedTensor((out_video, out_audio))
        video_mask, audio_mask = soft_av_masks(out_latent, steps, audio_steps)
        out_latent["noise_mask"] = comfy.nested_tensor.NestedTensor((video_mask, audio_mask))
        output = _drop_prefix_guides(conditioning, context_frames)
        _LOG.info(
            "Prompt Studio Soft AV continuation: exact %d-frame picture prefix, "
            "%d video steps / %d audio steps, final %d audio ticks half-cosine released from %s",
            context_frames,
            steps,
            audio_steps,
            SOFT_AV_AUDIO_FEATHER_STEPS,
            source,
        )
        return output, out_latent, context_frames


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
                    "format": SOFT_AV_FORMAT,
                    "transition_recipe": "soft_av_39_exact_video_half_cosine_audio_8",
                    "video_shape": "x".join(str(int(value)) for value in video.shape),
                    "audio_shape": "x".join(str(int(value)) for value in audio.shape),
                    "dtype": str(video.dtype),
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

    RETURN_TYPES = ("IMAGE", "AUDIO", "IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio", "assembly_images", "assembly_audio")
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
        full_wanted = round(total / float(fps) * sample_rate)
        if waveform.shape[-1] > full_wanted:
            assembly_waveform = waveform[..., :full_wanted]
        elif waveform.shape[-1] < full_wanted:
            assembly_waveform = torch.nn.functional.pad(
                waveform, (0, full_wanted - waveform.shape[-1])
            )
        else:
            assembly_waveform = waveform
        cut = round(count / float(fps) * sample_rate)
        if cut >= assembly_waveform.shape[-1]:
            raise ValueError("Continuation audio is shorter than its repeated context head")
        waveform = assembly_waveform[..., cut:]
        wanted = round(len(output_images) / float(fps) * sample_rate)
        if waveform.shape[-1] > wanted:
            waveform = waveform[..., :wanted]
        elif waveform.shape[-1] < wanted:
            waveform = torch.nn.functional.pad(waveform, (0, wanted - waveform.shape[-1]))
        return (
            output_images,
            {"waveform": waveform, "sample_rate": sample_rate},
            images,
            {"waveform": assembly_waveform, "sample_rate": sample_rate},
        )


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
