"""Load Director media references through ComfyUI's native media contracts."""

from __future__ import annotations

import math

import torch

from .contracts import PromptDocumentError, model_references


TARGET_FPS = 24
MIN_REFERENCE_SECONDS = 2.0
MAX_REFERENCE_SECONDS = 15.0
MAX_REFERENCE_TOTAL_SECONDS = 15.0


def _load_image(path):
    if not path:
        raise PromptDocumentError("Image reference has no uploaded input path")
    import nodes

    image, _mask = nodes.LoadImage().load_image(path)
    return image[:1]


def _video_components(path):
    if not path:
        raise PromptDocumentError("Video reference has no uploaded input path")
    from comfy_extras.nodes_video import LoadVideo

    try:
        output = LoadVideo.execute(file=path).result
    except TypeError:
        # Compatibility with ComfyUI builds whose legacy LoadVideo input was
        # still named `video` rather than `file`.
        output = LoadVideo.execute(video=path).result
    if not output:
        raise PromptDocumentError(f"ComfyUI could not load video reference '{path}'")
    components = output[0].get_components()
    return components.images, components.audio, float(components.frame_rate)


def _trim_and_resample_video(frames, source_fps, trim_start=0.0, trim_end=None):
    if frames is None or len(frames) == 0:
        raise PromptDocumentError("Video reference contains no frames")
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise PromptDocumentError("Video reference has an invalid frame rate")
    source_duration = len(frames) / source_fps
    start = max(0.0, float(trim_start or 0.0))
    end = source_duration if trim_end is None else min(source_duration, float(trim_end))
    duration = end - start
    if duration < MIN_REFERENCE_SECONDS or duration > MAX_REFERENCE_SECONDS:
        raise PromptDocumentError(
            f"Video references must be between 2 and 15 seconds after trimming (got {duration:.2f}s)"
        )
    target_count = max(1, int(math.floor(duration * TARGET_FPS)))
    timestamps = start + torch.arange(target_count, dtype=torch.float64) / TARGET_FPS
    indices = torch.clamp((timestamps * source_fps).round().long(), 0, len(frames) - 1)
    return frames[indices], duration, start, end


def _trim_audio(audio, trim_start=0.0, trim_end=None):
    if not audio:
        return None
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate") or 0)
    if waveform is None or sample_rate <= 0:
        raise PromptDocumentError("Audio reference is invalid")
    total = waveform.shape[-1] / sample_rate
    start = max(0.0, float(trim_start or 0.0))
    end = total if trim_end is None else min(total, float(trim_end))
    if end <= start:
        raise PromptDocumentError("Audio trim range is empty")
    return {
        "waveform": waveform[..., int(start * sample_rate):int(end * sample_rate)],
        "sample_rate": sample_rate,
    }


def _load_audio(path):
    if not path:
        raise PromptDocumentError("Audio reference has no uploaded input path")
    from comfy_extras.nodes_audio import LoadAudio

    output = LoadAudio.execute(audio=path).result
    if not output:
        raise PromptDocumentError(f"ComfyUI could not load audio reference '{path}'")
    return output[0]


def anchor_images(document):
    first = last = None
    for reference in model_references(document):
        if reference["kind"] != "image":
            continue
        roles = set(reference["roles"])
        if "first_frame" in roles:
            if first is not None:
                raise PromptDocumentError("Only one first-frame reference is allowed")
            first = _load_image(reference["path"])
        if "last_frame" in roles:
            if last is not None:
                raise PromptDocumentError("Only one last-frame reference is allowed")
            last = _load_image(reference["path"])
    return first, last


def reference_inputs(document):
    """Return native MiniMax ref dictionaries in deterministic presentation order."""
    images = {}
    videos = {}
    video_audios = {}
    audios = {}
    video_total = 0.0
    audio_total = 0.0

    for reference in model_references(document):
        kind = reference["kind"]
        if kind == "image":
            images[f"ref_image_{len(images) + 1}"] = _load_image(reference["path"])
            continue
        if kind == "video":
            frames, embedded_audio, source_fps = _video_components(reference["path"])
            frames, duration, start, end = _trim_and_resample_video(
                frames,
                source_fps,
                reference["trim_start"],
                reference["trim_end"],
            )
            video_total += duration
            key = f"ref_video_{len(videos) + 1}"
            videos[key] = frames
            if reference["use_embedded_audio"] and embedded_audio:
                trimmed = _trim_audio(embedded_audio, start, end)
                video_audios[f"ref_video_audio_{len(videos)}"] = trimmed
                audio_total += trimmed["waveform"].shape[-1] / trimmed["sample_rate"]
            continue
        audio = _trim_audio(
            _load_audio(reference["path"]),
            reference["trim_start"],
            reference["trim_end"],
        )
        duration = audio["waveform"].shape[-1] / audio["sample_rate"]
        if duration < MIN_REFERENCE_SECONDS or duration > MAX_REFERENCE_SECONDS:
            raise PromptDocumentError(
                f"Audio references must be between 2 and 15 seconds after trimming (got {duration:.2f}s)"
            )
        audio_total += duration
        audios[f"ref_audio_{len(audios) + 1}"] = audio

    audio_count = len(audios) + len(video_audios)
    if len(images) > 9 or len(videos) > 3 or audio_count > 3:
        raise PromptDocumentError("REF2VA supports at most 9 images, 3 videos, and 3 audio tracks")
    if len(images) + len(videos) + audio_count > 12:
        raise PromptDocumentError("REF2VA supports at most 12 active reference items")
    if audio_count and not images and not videos:
        raise PromptDocumentError("REF2VA audio requires an image or video reference")
    if video_total > MAX_REFERENCE_TOTAL_SECONDS:
        raise PromptDocumentError("REF2VA video reference duration must not exceed 15 seconds total")
    if audio_total > MAX_REFERENCE_TOTAL_SECONDS:
        raise PromptDocumentError("REF2VA audio reference duration must not exceed 15 seconds total")
    return images, videos, video_audios, audios
