"""Prompt Studio-owned MiniMax H3 audiovisual continuation primitives.

The stock H3 layout can re-inject keyframe latents at every sampling step,
but currently exposes only first/last anchors.  Video Studio continuation uses
a guarded, opt-in layout extension so a compact tail from the parent render can
occupy the head of the child timeline.  The repeated head is removed after
decode; the stored segment therefore begins immediately after its parent.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading

import torch
from safetensors.torch import load_file, save_file


FPS = 24.0
AUDIO_LATENT_HZ = 40.0
FRAME_RESCALE = 5.0 / 3.0
FRAME_SPANS = (1, 4, 4, 4, 4)
DEFAULT_CONTEXT_FRAMES = 22
CONTEXT_FRAME_OPTIONS = (5, 22, 39, 56)
VIDEO_MARKER = "psv_context_frame_index"
AUDIO_MARKER = "psv_context_audio_end_frame"
LAYOUT_PATCH_MARKER = "_psv_h3_context_layout_patch"
PAYLOAD_PATCH_MARKER = "_psv_h3_context_payload_patch"

_LOG = logging.getLogger("promptstudio_video.motion_context")
_PATCH_LOCK = threading.Lock()
_PATCHED = False
_ORIGINAL_LAYOUT_INIT = None
_ORIGINAL_EXTRA_CONDS = None


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


def _reference_segments(layout, references):
    actual = [(a, b, kind) for a, b, kind in layout.segments if kind in {"ref_img", "ref_audio"}]
    expected = []
    for index, reference in enumerate(references or []):
        kind = reference.get("kind")
        if kind == "image":
            expected.append((index, "ref_img"))
        elif kind == "audio":
            if int(reference.get("ref_audio_t") or 0) > 0:
                expected.append((index, "ref_audio"))
        elif kind in {"video", "video_audio"}:
            if int(reference.get("ref_audio_t") or 0) > 0:
                expected.append((index, "ref_audio"))
            expected.append((index, "ref_img"))
        else:
            raise RuntimeError(f"Unknown H3 reference block {kind!r}")
    if len(expected) != len(actual):
        raise RuntimeError("ComfyUI's H3 reference layout changed; continuation was not applied")
    mapped = {}
    for (index, expected_kind), (start, stop, actual_kind) in zip(expected, actual):
        if expected_kind != actual_kind:
            raise RuntimeError("ComfyUI's H3 reference segment order changed")
        mapped.setdefault(index, {})[actual_kind] = (start, stop)
    return mapped


def _target_origin(layout):
    start, stop, kind = layout.segments[-1]
    if kind != "video" or stop <= start:
        raise RuntimeError("ComfyUI's H3 target video is no longer the final packed segment")
    return float(layout.position_ids[start, 0])


def _layout_init_with_context(
    self, text_len, latent_t, latent_h, latent_w, audio_t,
    keyframes=None, refs=None, frame_count=None,
):
    marked_video = bool(keyframes) and any(VIDEO_MARKER in item for item in keyframes)
    marked_audio = bool(refs) and any(AUDIO_MARKER in item for item in refs)
    if not marked_video and not marked_audio:
        return _ORIGINAL_LAYOUT_INIT(
            self, text_len, latent_t, latent_h, latent_w, audio_t,
            keyframes=keyframes, refs=refs, frame_count=frame_count,
        )

    legal_keyframes = [
        ({**item, "resolved_frame_index": 0} if VIDEO_MARKER in item else item)
        for item in (keyframes or [])
    ]
    _ORIGINAL_LAYOUT_INIT(
        self, text_len, latent_t, latent_h, latent_w, audio_t,
        keyframes=legal_keyframes, refs=refs, frame_count=frame_count,
    )
    origin = _target_origin(self)
    condition_segments = [(a, b) for a, b, kind in self.segments if kind == "cond"]
    if len(condition_segments) != len(keyframes or []):
        raise RuntimeError("ComfyUI's H3 condition segment count changed")
    for (start, stop), keyframe in zip(condition_segments, keyframes or []):
        if VIDEO_MARKER in keyframe:
            index = float(keyframe[VIDEO_MARKER])
            if index < 0 or (frame_count is not None and index >= frame_count):
                raise ValueError("Continuation frame anchor is outside the target video")
            self.position_ids[start:stop, 0] = origin + FRAME_RESCALE * index

    marked = [index for index, item in enumerate(refs or []) if AUDIO_MARKER in item]
    if marked_audio:
        if len(marked) != 1:
            raise RuntimeError("Prompt Studio continuation requires exactly one marked audio context")
        reference = refs[marked[0]]
        if reference.get("kind") != "audio":
            raise RuntimeError("Prompt Studio audio context must be an audio reference block")
        segment = _reference_segments(self, refs)[marked[0]].get("ref_audio")
        if segment is None:
            raise RuntimeError("Prompt Studio audio context produced no packed audio rows")
        start, stop = segment
        steps = int(reference.get("ref_audio_t") or 0)
        if stop - start != steps * 2:
            raise RuntimeError("ComfyUI's H3 packed audio row count changed")
        desired_end = origin + FRAME_RESCALE * float(reference[AUDIO_MARKER])
        current_start = float(self.position_ids[start, 0])
        desired_start = desired_end - steps
        self.position_ids[start:stop, 0] += desired_start - current_start


setattr(_layout_init_with_context, LAYOUT_PATCH_MARKER, True)


def _extra_conds_with_context(self, **kwargs):
    output = _ORIGINAL_EXTRA_CONDS(self, **kwargs)
    keyframes = kwargs.get("minimax_keyframes") or []
    references = kwargs.get("minimax_refs") or []
    marked = any(VIDEO_MARKER in item for item in keyframes) or any(
        AUDIO_MARKER in item for item in references
    )
    if not marked:
        return output
    constant = output.get("minimax_payload")
    payload = getattr(constant, "cond", None)
    if not isinstance(payload, dict):
        raise RuntimeError("ComfyUI's H3 conditioning payload is unavailable")
    payload["keyframes"] = keyframes
    payload["refs"] = references
    payload["cond_video_latents"] = [
        item["latent"] for item in [*keyframes, *references] if "latent" in item
    ]
    payload["cond_audio_latents"] = [
        item["audio_latent"] for item in references if item.get("audio_latent") is not None
    ]
    if kwargs.get("minimax_frame_count") is not None:
        payload["frame_count"] = kwargs["minimax_frame_count"]
    return output


setattr(_extra_conds_with_context, PAYLOAD_PATCH_MARKER, True)


def _validate_layout_patch(layout_class):
    base = dict(text_len=7, latent_t=7, latent_h=8, latent_w=12, audio_t=37, frame_count=22)
    stock = layout_class.__new__(layout_class)
    _ORIGINAL_LAYOUT_INIT(stock, keyframes=[{"resolved_frame_index": 0}], refs=None, **base)
    ours = layout_class.__new__(layout_class)
    _layout_init_with_context(
        ours,
        keyframes=[{"resolved_frame_index": 0, VIDEO_MARKER: 0}],
        refs=None,
        **base,
    )
    if not torch.equal(stock.position_ids, ours.position_ids):
        raise RuntimeError("Prompt Studio H3 context self-test failed for the first-frame anchor")
    run = layout_class.__new__(layout_class)
    anchors = [
        {"resolved_frame_index": 0, VIDEO_MARKER: offset}
        for offset in step_offsets(steps_for_frames(DEFAULT_CONTEXT_FRAMES))
    ]
    _layout_init_with_context(run, keyframes=anchors, refs=None, **base)
    times = [float(run.position_ids[a, 0]) for a, _b, kind in run.segments if kind == "cond"]
    if times != sorted(set(times)) or len(times) != len(anchors):
        raise RuntimeError("Prompt Studio H3 context self-test failed for interior anchors")
    audio_run = layout_class.__new__(layout_class)
    audio_ref = {
        "kind": "audio",
        "ref_audio_t": 37,
        AUDIO_MARKER: 37 / FRAME_RESCALE,
    }
    _layout_init_with_context(audio_run, keyframes=None, refs=[audio_ref], **base)
    audio_start, audio_stop, _kind = next(
        segment for segment in audio_run.segments if segment[2] == "ref_audio"
    )
    target_origin = _target_origin(audio_run)
    audio_times = audio_run.position_ids[audio_start:audio_stop, 0]
    if float(audio_times.min()) != target_origin or float(audio_times.max()) != target_origin + 36:
        raise RuntimeError("Prompt Studio H3 context self-test failed for audio alignment")


def ensure_context_patches():
    global _PATCHED, _ORIGINAL_LAYOUT_INIT, _ORIGINAL_EXTRA_CONDS
    if _PATCHED:
        return
    with _PATCH_LOCK:
        if _PATCHED:
            return
        import comfy.ldm.minimax.model as minimax_model
        import comfy.model_base as model_base

        layout_class = minimax_model.PackedLayout
        model_class = model_base.MiniMaxH3
        current_layout = layout_class.__init__
        current_payload = model_class.extra_conds
        if getattr(current_layout, LAYOUT_PATCH_MARKER, False) and getattr(
            current_payload, PAYLOAD_PATCH_MARKER, False
        ):
            _PATCHED = True
            return
        if current_layout.__module__ != minimax_model.__name__ or current_payload.__module__ != model_base.__name__:
            raise RuntimeError(
                "Another extension already modifies MiniMax H3 continuation internals. "
                "Prompt Studio will not stack an incompatible runtime patch."
            )
        _ORIGINAL_LAYOUT_INIT = current_layout
        _ORIGINAL_EXTRA_CONDS = current_payload
        _validate_layout_patch(layout_class)
        layout_class.__init__ = _layout_init_with_context
        model_class.extra_conds = _extra_conds_with_context
        _PATCHED = True
        _LOG.info("Prompt Studio native H3 motion/audio context enabled")


def _load_saved_tail(relative_path):
    path = _output_path(relative_path, must_exist=True)
    values = load_file(path)
    if "video" not in values or "audio" not in values:
        raise ValueError("Saved Prompt Studio context is missing its video or audio stream")
    return {"samples": [values["video"], values["audio"]]}


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
        return {"samples": [video, audio_vae.encode(waveform.movedim(1, -1))]}
    waveform = audio_value["waveform"]
    sample_rate = int(audio_value["sample_rate"])
    if sample_rate != target_rate:
        import torchaudio

        waveform = torchaudio.functional.resample(waveform, sample_rate, target_rate)
    waveform = waveform[..., -samples:]
    audio = audio_vae.encode(waveform[:1].movedim(1, -1))
    return {"samples": [video, audio]}


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
        import node_helpers

        ensure_context_patches()
        context_frames = int(context_frames)
        target_video, _target_audio = _streams(latent)
        try:
            context = _load_saved_tail(context_latent_path)
            source = "saved latent"
        except FileNotFoundError:
            if not context_video:
                raise ValueError("The parent generation has neither saved latent context nor a fallback video")
            context = _fallback_tail(
                context_video, video_vae, audio_vae, target_video, context_frames
            )
            source = "decoded video fallback"
        source_video, source_audio = _streams(context)
        if source_video.shape[1] != target_video.shape[1] or source_video.shape[3:] != target_video.shape[3:]:
            raise ValueError("Parent and extension H3 latents use different channels or resolution")
        steps = steps_for_frames(context_frames)
        if steps is None or source_video.shape[2] < steps:
            raise ValueError("Parent context does not contain a complete H3 continuation window")
        start = int(source_video.shape[2]) - steps
        if start % len(FRAME_SPANS):
            raise ValueError("Parent context is off the native H3 temporal phase grid")
        blocks = [source_video[:1, :, start + index:start + index + 1].clone() for index in range(steps)]
        anchors = [
            {"resolved_frame_index": 0, VIDEO_MARKER: offset, "latent": block}
            for offset, block in zip(step_offsets(steps), blocks)
        ]
        target_frames = pixel_frames_for_steps(target_video.shape[2])
        if context_frames >= target_frames:
            raise ValueError("Continuation context must be shorter than the sampled extension")
        values = {"minimax_keyframes": anchors, "minimax_frame_count": target_frames}
        output = node_helpers.conditioning_set_values(conditioning, values)

        audio_steps = min(source_audio.shape[-1], max(1, round(context_frames / FPS * AUDIO_LATENT_HZ)))
        audio_tail = source_audio[:1, ..., -audio_steps:].clone()
        overhang = float(audio_steps) - FRAME_RESCALE * context_frames
        if not 0 <= overhang < 1:
            overhang = 0.0
        end_coordinate = round(FRAME_RESCALE * context_frames + overhang)
        audio_end_frame = end_coordinate / FRAME_RESCALE
        audio_reference = {
            "kind": "audio",
            "ref_audio_t": int(audio_steps),
            "audio_latent": audio_tail,
            AUDIO_MARKER: audio_end_frame,
        }
        output = node_helpers.conditioning_set_values(
            output, {"minimax_refs": [audio_reference]}, append=True
        )
        _LOG.info(
            "Prompt Studio continuation pinned %d frames (%d video steps, %d audio steps) from %s",
            context_frames,
            steps,
            audio_steps,
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
        descriptor, temporary = tempfile.mkstemp(
            prefix=os.path.basename(target) + ".", suffix=".tmp.safetensors", dir=os.path.dirname(target)
        )
        os.close(descriptor)
        try:
            save_file(
                {"video": video, "audio": audio},
                temporary,
                metadata={
                    "format": "promptstudio_h3_av_tail_v1",
                    "context_frames": str(int(context_frames)),
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
