"""Sample-accurate post-generation mixing for Shot Editor exact-audio clips."""

from __future__ import annotations

from fractions import Fraction
import math
import ntpath
import os
import re
import tempfile

import av
import numpy as np

from .continuation import resolve_output_path
from .contracts import is_exact_audio_reference, normalize_document
from .media_budget import audio_array, checkpoint, duration as check_duration, MAX_AUDIO_SECONDS, bounded_media, assembly_job


MIX_SAMPLE_RATE = 48_000
MIX_CHANNELS = 2


def _safe_component(value, fallback):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-")
    return (value or fallback)[:80]


def _input_path(relative_path):
    import folder_paths

    relative_path = str(relative_path or "").replace("\\", "/")
    if relative_path.startswith("/") or ntpath.splitdrive(relative_path)[0]:
        raise ValueError("Exact audio media path must be relative to ComfyUI input storage")
    normalized = os.path.normpath(relative_path.replace("/", os.sep))
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise ValueError("Exact audio media path escapes ComfyUI input storage")
    root = os.path.realpath(folder_paths.get_input_directory())
    path = os.path.realpath(os.path.join(root, normalized))
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("Exact audio media path escapes ComfyUI input storage")
    if not os.path.isfile(path):
        raise ValueError("Exact audio media is missing from ComfyUI input storage")
    return path


def _resampled_frames(resampler, frame):
    result = resampler.resample(frame)
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


def decode_audio(path, sample_rate=MIX_SAMPLE_RATE, start=0, end=None):
    return audio_array(path, sample_rate, start, end)


def probe_input_audio(relative_path):
    """Return authoritative stream metadata for an uploaded audio asset."""
    path = _input_path(relative_path)
    with av.open(path, mode="r") as container:
        if not container.streams.audio:
            raise ValueError("The selected media has no audio stream")
        stream = container.streams.audio[0]
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration is not None and stream.time_base is not None
            else (float(container.duration / av.time_base) if container.duration else 0.0)
        )
        if duration <= 0:
            raise ValueError('Audio has no usable duration metadata; remux it before importing')
        codec = getattr(stream.codec_context, "name", "") or ""
        sample_rate = int(getattr(stream.codec_context, "sample_rate", 0) or 0)
        channels = int(getattr(stream.codec_context, "channels", 0) or 0)
    check_duration(duration)
    if not 8000 <= sample_rate <= 192000 or not 1 <= channels <= 8:
        raise ValueError("Audio sample rate or channel count exceeds the media budget")
    return {
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "codec": codec,
    }


def _fit_audio(value, samples):
    if value.shape == (MIX_CHANNELS, samples):
        return value
    output = np.zeros((MIX_CHANNELS, samples), dtype=np.float32)
    count = min(samples, value.shape[1])
    if count:
        output[:, :count] = value[:, :count]
    return output


def _clip_envelope(samples, fade_in, fade_out, sample_rate):
    envelope = np.ones(samples, dtype=np.float32)
    fade_in_samples = min(samples, max(0, round(float(fade_in) * sample_rate)))
    fade_out_samples = min(samples, max(0, round(float(fade_out) * sample_rate)))
    if fade_in_samples:
        envelope[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples, dtype=np.float32)
    if fade_out_samples:
        envelope[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples, dtype=np.float32)
    return envelope


@bounded_media
def mix_document_audio(source_path, document, sample_rate=MIX_SAMPLE_RATE):
    """Return the final stereo mix and whether any exact clips were applied."""
    normalized = normalize_document(document)
    references = {
        item["id"]: item for item in normalized["references"]
        if is_exact_audio_reference(item)
    }
    clips = []
    for shot in normalized["shots"]:
        for clip in shot.get("audio_clips") or []:
            clips.append((float(shot["start"]) + float(clip["start"]), clip))
    if not clips:
        return np.zeros((MIX_CHANNELS, 0), dtype=np.float32), False

    with av.open(source_path, mode="r") as container:
        if not container.streams.video:
            raise ValueError("The generated output has no video stream")
        stream = container.streams.video[0]
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration is not None and stream.time_base is not None
            else (float(container.duration / av.time_base) if container.duration else 0.0)
        )
    check_duration(duration, MAX_AUDIO_SECONDS)
    if len(clips) > 1000:
        raise ValueError('Exact audio supports at most 1000 clips per assembly')
    total_samples = max(1, round(duration * sample_rate))
    mix = _fit_audio(decode_audio(source_path, sample_rate, end=duration), total_samples)

    for absolute_start, clip in sorted(clips, key=lambda item: (item[0], item[1]["id"])):
        reference = references.get(clip["reference_id"])
        if reference is None:
            raise ValueError("An exact audio clip references unavailable media")
        path = _input_path(reference["path"])
        checkpoint()
        wanted = max(1, round((float(clip["end"]) - float(clip["start"])) * sample_rate))
        source_start = round(float(clip["source_start"]) * sample_rate) / sample_rate
        source_end = source_start + wanted / sample_rate
        if clip["source_end"] is not None:
            source_end = min(source_end, round(float(clip["source_end"]) * sample_rate) / sample_rate)
        audio = _fit_audio(decode_audio(path, sample_rate, source_start, source_end), wanted)
        audio *= math.pow(10.0, float(clip["gain_db"]) / 20.0)
        audio *= _clip_envelope(
            wanted, clip["fade_in"], clip["fade_out"], sample_rate
        )[None, :]
        target_start = max(0, round(absolute_start * sample_rate))
        target_end = min(total_samples, target_start + wanted)
        if target_end <= target_start:
            continue
        count = target_end - target_start
        if clip["mix_mode"] == "replace":
            mix[:, target_start:target_end] = 0.0
        mix[:, target_start:target_end] += audio[:, :count]
    return np.clip(mix, -1.0, 1.0, out=mix), True


def mux_video_with_audio(source_path, target_path, audio, sample_rate=MIX_SAMPLE_RATE):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=os.path.basename(target_path) + ".", suffix=".tmp.mp4", dir=os.path.dirname(target_path))
    os.close(descriptor)
    try:
        with av.open(source_path, mode="r") as source, av.open(temporary, mode="w", format="mp4") as output:
            if not source.streams.video:
                raise ValueError("The generated output has no video stream")
            source_video = source.streams.video[0]
            output_video = output.add_stream_from_template(source_video, opaque=True)
            output_audio = output.add_stream("aac", rate=sample_rate)
            output_audio.layout = "stereo"
            output_audio.bit_rate = 192_000
            output.metadata.update(source.metadata or {})

            for packet in source.demux(source_video):
                checkpoint()
                if packet.dts is None:
                    continue
                packet.stream = output_video
                output.mux(packet)

            block = 1024
            for start in range(0, audio.shape[1], block):
                checkpoint(start, audio.shape[1])
                chunk = np.ascontiguousarray(audio[:, start:start + block], dtype=np.float32)
                frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                frame.sample_rate = sample_rate
                frame.pts = start
                frame.time_base = Fraction(1, sample_rate)
                for packet in output_audio.encode(frame):
                    output.mux(packet)
            for packet in output_audio.encode(None):
                output.mux(packet)
        os.replace(temporary, target_path)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)


@assembly_job
def assemble_exact_audio(source_descriptor, document, project_id, generation_id):
    import folder_paths

    source_path, _source = resolve_output_path(source_descriptor)
    audio, applied = mix_document_audio(source_path, document)
    if not applied:
        return source_descriptor
    subfolder = os.path.join("PromptStudioVideo", "AudioMix")
    filename = (
        f"{_safe_component(project_id, 'project')}-"
        f"{_safe_component(generation_id, 'generation')}.mp4"
    )
    root = os.path.abspath(folder_paths.get_output_directory())
    target = os.path.abspath(os.path.join(root, subfolder, filename))
    if os.path.commonpath([root, target]) != root:
        raise ValueError("Exact audio output path escapes ComfyUI output storage")
    mux_video_with_audio(source_path, target, audio)
    return {"filename": filename, "subfolder": subfolder.replace(os.sep, "/"), "type": "output"}
