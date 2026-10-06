"""Load Director media references through ComfyUI's native media contracts."""

from __future__ import annotations

import math

import torch

from .contracts import PromptDocumentError, model_references
from .media_budget import video_range, audio_array, trim_range, geometry, MAX_TENSOR_BYTES, bounded_media
from .audio_mix import _input_path, probe_input_audio
from .continuation import probe_video


TARGET_FPS = 24
MIN_REFERENCE_SECONDS = 2.0
MAX_REFERENCE_SECONDS = 15.0
MAX_REFERENCE_TOTAL_SECONDS = 15.0


def _load_image(path):
    if not path:
        raise PromptDocumentError("Image reference has no uploaded input path")
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(_input_path(path)) as source:
        geometry(source.width, source.height)
        # References consume one still, including animated image containers.
        value = ImageOps.exif_transpose(source).convert("RGB")
        return torch.from_numpy(np.asarray(value).astype(np.float32) / np.float32(255))[None]



def _video_components(path, trim_start=0, trim_end=None, embedded_audio=True, *, minimum=2):
    path = _input_path(path)
    frames, start, end = video_range(path, trim_start, trim_end, minimum=minimum)
    audio = None
    if embedded_audio and probe_video(path)["has_audio"]:
        audio = {"waveform": torch.from_numpy(audio_array(path, 48000, start, end))[None], "sample_rate": 48000}
    return frames, audio, TARGET_FPS


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


def _load_audio(path, trim_start=0, trim_end=None, *, minimum=2):
    metadata = probe_input_audio(path)
    start, end = trim_range(metadata["duration_seconds"], trim_start, trim_end, 15, minimum)
    rate = int(metadata["sample_rate"])
    return {"waveform": torch.from_numpy(audio_array(_input_path(path), rate, start, end))[None], "sample_rate": rate}


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


@bounded_media
def reference_inputs(document):
    """Return native MiniMax ref dictionaries in deterministic presentation order."""
    images = {}
    videos = {}
    video_audios = {}
    audios = {}
    video_total = 0.0
    audio_total = 0.0

    references = model_references(document)
    counts = {kind: sum(ref["kind"] == kind for ref in references) for kind in ("image", "video", "audio")}
    audio_count = counts["audio"] + sum(ref["kind"] == "video" and ref["use_embedded_audio"] for ref in references)
    if counts["image"] > 9 or counts["video"] > 3 or audio_count > 3 or sum(counts.values()) + audio_count - counts["audio"] > 12:
        raise PromptDocumentError("REF2VA supports at most 9 images, 3 videos, 3 audio tracks and 12 items")
    if audio_count and not counts["image"] and not counts["video"]:
        raise PromptDocumentError("REF2VA audio requires an image or video reference")
    # Probe every trim and aggregate footprint before decoding any reference.
    video_seconds = audio_seconds = tensor_bytes = 0
    for ref in references:
        if ref["kind"] == "image":
            from PIL import Image
            with Image.open(_input_path(ref["path"])) as image:
                geometry(image.width, image.height)
                tensor_bytes += image.width * image.height * 12
            continue
        info = probe_video(_input_path(ref["path"])) if ref["kind"] == "video" else probe_input_audio(ref["path"])
        start, end = trim_range(info.get("duration", info.get("duration_seconds")), ref["trim_start"], ref["trim_end"], 15, 2)
        seconds = end - start
        if ref["kind"] == "video":
            frame_count = int(seconds * TARGET_FPS)
            geometry(info["width"], info["height"], frame_count)
            tensor_bytes += info["width"] * info["height"] * frame_count * 12
            video_seconds += seconds
            if ref["use_embedded_audio"] and info["has_audio"]:
                audio_seconds += seconds
        else:
            audio_seconds += seconds
    if video_seconds > 15 or audio_seconds > 15 or tensor_bytes > MAX_TENSOR_BYTES:
        raise PromptDocumentError("Reference totals exceed the 15-second or 512 MiB decode budget")

    for reference in references:
        kind = reference["kind"]
        if kind == "image":
            images[f"ref_image_{len(images) + 1}"] = _load_image(reference["path"])
            continue
        if kind == "video":
            frames, embedded_audio, source_fps = _video_components(reference["path"], reference["trim_start"], reference["trim_end"], reference["use_embedded_audio"])
            duration = len(frames) / TARGET_FPS
            video_total += duration
            key = f"ref_video_{len(videos) + 1}"
            videos[key] = frames
            if reference["use_embedded_audio"] and embedded_audio:
                trimmed = embedded_audio
                video_audios[f"ref_video_audio_{len(videos)}"] = trimmed
                audio_total += trimmed["waveform"].shape[-1] / trimmed["sample_rate"]
            continue
        audio = _load_audio(reference["path"], reference["trim_start"], reference["trim_end"])
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
