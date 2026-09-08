"""Native MiniMax H3 continuation documents and immutable media assembly."""

from __future__ import annotations

import copy
from collections import deque
import json
import math
import ntpath
import os
import re
import tempfile
from fractions import Fraction

from .contracts import FPS, PromptDocumentError, normalize_document
from .media_budget import (audio_array, iter_audio, checkpoint, geometry as check_geometry, bounded_media, assembly_job, duration as check_duration, MAX_ASSEMBLY_SECONDS, MAX_SAMPLE_RATE)


CONTINUATION_CONTEXT_FRAMES = 39
CONTINUATION_CONTEXT_SECONDS = CONTINUATION_CONTEXT_FRAMES / FPS
# Kept as aliases for persisted metadata written by the first prototype.
CONTINUATION_TAIL_FRAMES = CONTINUATION_CONTEXT_FRAMES
CONTINUATION_TAIL_SECONDS = CONTINUATION_CONTEXT_SECONDS
MAX_CONTINUATION_SOURCES = 200
OUTPUT_TYPES = {"output"}


def normalize_output_descriptor(value, label="Video output"):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    filename = str(value.get("filename") or "").strip()
    subfolder = str(value.get("subfolder") or "").strip().replace("\\", "/")
    output_type = str(value.get("type") or "output").strip().lower()
    if not filename or filename != ntpath.basename(filename) or ntpath.splitdrive(filename)[0] or filename in {".", ".."}:
        raise ValueError(f"{label} has an invalid filename")
    if output_type not in OUTPUT_TYPES:
        raise ValueError(f"{label} must be a saved ComfyUI output")
    parts = [part for part in subfolder.split("/") if part]
    if subfolder.startswith("/") or ntpath.splitdrive(subfolder)[0] or any(part in {".", ".."} for part in parts):
        raise ValueError(f"{label} has an invalid subfolder")
    return {
        "filename": filename,
        "subfolder": "/".join(parts),
        "type": output_type,
    }


def annotated_output_path(value):
    descriptor = normalize_output_descriptor(value)
    relative = "/".join(
        part for part in (descriptor["subfolder"], descriptor["filename"]) if part
    )
    return f"{relative} [output]"


def resolve_output_path(value):
    """Resolve a saved-output descriptor without allowing output-directory escape."""
    descriptor = normalize_output_descriptor(value)
    import folder_paths

    root = os.path.abspath(folder_paths.get_output_directory())
    path = os.path.abspath(
        os.path.join(root, descriptor["subfolder"], descriptor["filename"])
    )
    if os.path.commonpath((root, path)) != root:
        raise ValueError("Video output path escapes the ComfyUI output directory")
    if not os.path.isfile(path):
        raise ValueError("The source video output no longer exists")
    return path, descriptor


def probe_video(path):
    import av

    with av.open(path, mode="r") as container:
        if not container.streams.video:
            raise ValueError("The source output has no video stream")
        stream = container.streams.video[0]
        # The container duration may be extended by AAC padding. Continuation
        # trimming is frame-based, so prefer the video timeline here.
        if stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
        elif stream.frames and stream.average_rate:
            duration = float(stream.frames / stream.average_rate)
        elif container.duration is not None:
            duration = float(container.duration / av.time_base)
        else:
            raise ValueError("The source video duration could not be determined")
        check_duration(duration)
        check_geometry(stream.width, stream.height)
        rate = float(stream.average_rate or FPS)
        if not math.isfinite(rate) or not 0 < rate <= 240:
            raise ValueError("Video frame rate is outside the decode budget")
        return {
            "duration": duration,
            "width": int(stream.width or 0),
            "height": int(stream.height or 0),
            "fps": float(stream.average_rate or FPS),
            "has_audio": bool(container.streams.audio),
        }


def continuation_frame_plan(duration_seconds, context_frames=CONTINUATION_CONTEXT_FRAMES):
    duration = float(duration_seconds)
    if not math.isfinite(duration) or duration < 5 or duration > 15:
        raise PromptDocumentError("Continuation duration must be between 5 and 15 seconds")
    context_frames = int(context_frames)
    if context_frames != CONTINUATION_CONTEXT_FRAMES:
        raise PromptDocumentError(
            f"Native continuation currently requires {CONTINUATION_CONTEXT_FRAMES} context frames"
        )
    requested_frames = max(1, round(duration * FPS))
    desired_sample_frames = requested_frames + context_frames
    grid_index = max(0, round((desired_sample_frames - 5) / 17))
    candidates = [17 * index + 5 for index in range(max(0, grid_index - 1), grid_index + 2)]
    sample_frames = min(candidates, key=lambda value: (abs(value - desired_sample_frames), value))
    delivered_frames = sample_frames - context_frames
    if delivered_frames <= 0:
        raise PromptDocumentError("Continuation duration is too short for its context window")
    return {
        "requested_duration": duration,
        "sample_frames": sample_frames,
        "sample_duration": sample_frames / FPS,
        "delivered_frames": delivered_frames,
        "delivered_duration": delivered_frames / FPS,
        "context_frames": context_frames,
        "context_seconds": context_frames / FPS,
    }


def _final_action(shot):
    for step in reversed(shot.get("steps") or []):
        if str(step.get("type") or "").lower() != "action":
            continue
        text = _without_reference_tokens(step.get("text"))
        text = re.sub(
            r"^\s*at\s+\d+(?:\.\d+)?\s+seconds?\s*,?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if text:
            return text
    return "the action and camera movement visible at the preceding ending"


def _continuation_opening(parent):
    previous = (parent.get("shots") or [{}])[-1]
    incoming_action = _final_action(previous)
    return (
        "The first 39 picture frames and their 65-step audio run are the protected exact ending "
        "of the preceding clip, not a new action or a replay. Continue every visible subject "
        "movement, object movement, camera movement, and active sound from the exact boundary "
        "phase into the newly generated future without a pause, reset, reversal, repeated onset, "
        "held transition pose, or cut. Preserve the incoming "
        "pose, direction, momentum, composition, spatial relationships, lighting, and exposure. "
        f"Carry forward the currently visible phase of this unfinished action without replaying its beginning: "
        f"{incoming_action}"
    )


def _continuation_shot(parent, brief):
    previous = (parent.get("shots") or [{}])[-1]
    opening = _continuation_opening(parent)
    next_action = _without_reference_tokens(brief).strip() or "Continue the visible action naturally."
    camera = dict(previous.get("camera") or {})
    if not camera:
        camera = {"type": "Static Shot", "amplitude": "default", "speed": "default", "target": ""}
    else:
        camera["target"] = _without_reference_tokens(camera.get("target")).strip()
    return {
        "id": "continuation-shot-1",
        "start": 0,
        "transition": "The same shot continues across the clip boundary without a cut or temporal reset.",
        "composition": _without_reference_tokens(previous.get("composition")).strip()
        or "The opening continues from the exact closing composition and framing of the preceding clip.",
        "subjects": _without_reference_tokens(previous.get("subjects")).strip()
        or "All visible subjects, wardrobe, objects, poses, and spatial relationships remain consistent.",
        "environment": _without_reference_tokens(previous.get("environment")).strip()
        or "The visible environment continues without resetting its established geometry.",
        "lighting": _without_reference_tokens(previous.get("lighting")).strip()
        or "The established lighting and exposure remain continuous across the boundary.",
        "camera": camera,
        "steps": [
            {"id": "continuation-opening", "type": "action", "text": opening},
            {
                "id": "continuation-action",
                "type": "action",
                "text": f"Continue directly into the requested development: {next_action}",
            },
        ],
        # The pixels in Video 1 own any existing on-screen wording. Repeating
        # unobserved text here risks changing it, while rewriting it would
        # violate the prompt contract's verbatim-text rule.
        "visible_text": [],
        "sounds": (
            []
            if parent.get("complete_silence")
            else [
                "Any ambience, dialogue, music, or physical sound already in progress at the boundary continues without a gap or duplicate onset.",
                "Synchronize new sounds and dialogue requested by the continuation with the new visible actions.",
            ]
        ),
        "notes": "",
    }


def _offset_timed_items(items, offset):
    for item in items or []:
        if item.get("start") is not None:
            item["start"] = float(item["start"]) + offset
        if item.get("end") is not None:
            item["end"] = float(item["end"]) + offset


def validate_extension_tail_timeline(document, delivered_duration):
    """Check delivered-tail bounds before ordinary H3 frame-grid normalization."""
    limit = float(delivered_duration)
    if not math.isfinite(limit) or limit <= 0:
        raise PromptDocumentError("Invalid post-trim authored-tail duration")
    shots = document.get("shots") or []
    for index, shot in enumerate(shots):
        start = float(shot.get("start") or 0)
        end = float(shots[index + 1].get("start") or 0) if index + 1 < len(shots) else limit
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or start >= limit or end > limit or end <= start or (index == 0 and start != 0):
            raise PromptDocumentError("Shot cut falls outside the post-trim authored-tail timeline")
        for field in ("steps", "sound_cues", "audio_clips"):
            for item in shot.get(field) or []:
                for edge in ("start", "end"):
                    if item.get(edge) is None:
                        continue
                    cue = float(item[edge])
                    if not math.isfinite(cue) or cue < 0 or start + cue > end + 1e-9 or (edge == "start" and start + cue >= end):
                        raise PromptDocumentError("Timed cue falls outside the post-trim authored-tail timeline")


def continuation_director_context(parent_document, source_effective_duration=0):
    parent = normalize_document(parent_document)
    shot = copy.deepcopy(parent["shots"][-1])
    for key in ("composition", "subjects", "environment", "lighting"):
        shot[key] = _without_reference_tokens(shot.get(key))
    shot["steps"] = [step for step in shot.get("steps", []) if not str(step.get("id", "")).startswith("continuation-opening")]
    return {
        "type": "native_h3_soft_av_extension", "engine": "native_h3_soft_av_39",
        "transition_policy": "soft_av", "context_frames": CONTINUATION_CONTEXT_FRAMES,
        "video_latent_steps": 12, "audio_latent_steps": 65, "audio_feather_steps": 8,
        "source_effective_duration": max(0, float(source_effective_duration or 0)),
        "source_final_shot": shot,
    }


def build_extension_authoring_document(parent_document, brief, duration_seconds):
    """Seed only the new tail; generation alone adds the protected prefix."""
    parent = normalize_document(parent_document)
    timing = continuation_frame_plan(duration_seconds)
    shot = _continuation_shot(parent, "")
    shot["id"] = "extension-shot-1"
    shot["steps"] = []
    shot["sounds"] = []
    return normalize_document({
        **copy.deepcopy(parent), "mode": "t2va", "duration_seconds": timing["requested_duration"],
        "main_description": str(brief or "").strip(), "prompt_override": "",
        "style": _without_reference_tokens(parent.get("style")), "shots": [shot],
        "references": [], "task_types": [], "subject_definitions": [], "retention_analysis": [],
        "canvas_reference_id": "", "summary": "",
    })


def _build_structured_continuation_document(parent, extension_document, timing):
    if not isinstance(extension_document, dict):
        raise PromptDocumentError("Structured extension document must be an object")
    try:
        requested_duration = float(extension_document.get("duration_seconds"))
    except (TypeError, ValueError) as exc:
        raise PromptDocumentError("Structured extension requires a valid duration") from exc
    if abs(requested_duration - timing["requested_duration"]) > 1 / FPS:
        raise PromptDocumentError("Structured extension duration does not match the continuation request")

    authored_value = copy.deepcopy(extension_document)
    # The authoring timeline describes only newly delivered frames. Normalize
    # it against the exact native-grid tail so a cut cannot land in the overlap
    # or beyond the frames that survive trimming.
    authored_value["duration_seconds"] = timing["delivered_duration"]
    validate_extension_tail_timeline(authored_value, timing["delivered_duration"])
    authored = normalize_document(authored_value)
    if authored.get("references"):
        raise PromptDocumentError(
            "Structured extensions currently support generated sound and dialogue, but not new media references"
        )
    if authored.get("prompt_override"):
        raise PromptDocumentError(
            "Structured extensions must use their shot timeline; manual prompt overrides are not supported"
        )
    if (authored["width"], authored["height"]) != (parent["width"], parent["height"]):
        raise PromptDocumentError("Structured extension canvas must match the source video")

    shots = copy.deepcopy(authored["shots"])
    context_seconds = timing["context_seconds"] if "context_seconds" in timing else CONTINUATION_CONTEXT_SECONDS
    for index, shot in enumerate(shots):
        if index:
            shot["start"] = float(shot["start"]) + context_seconds
        else:
            shot["start"] = 0.0
            shot["transition"] = "The same shot continues across the clip boundary without a cut or temporal reset."
            existing_ids = {item.get("id") for item in shot.get("steps") or []}
            bridge_id = "continuation-opening"
            suffix = 2
            while bridge_id in existing_ids:
                bridge_id = f"continuation-opening-{suffix}"
                suffix += 1
            _offset_timed_items(shot.get("steps"), context_seconds)
            _offset_timed_items(shot.get("sound_cues"), context_seconds)
            _offset_timed_items(shot.get("audio_clips"), context_seconds)
            shot["steps"] = [{
                "id": bridge_id,
                "type": "action",
                "text": _continuation_opening(parent),
            }, *(shot.get("steps") or [])]
            if not authored.get("complete_silence"):
                bridge_sounds = [
                    "Any ambience, dialogue, music, or physical sound already in progress at the boundary continues without a gap or duplicate onset.",
                    "Synchronize the extension's authored sounds and dialogue with its new visible actions.",
                ]
                shot["sounds"] = [*bridge_sounds, *(shot.get("sounds") or [])]
                shot["sound_cues"] = [
                    {"id": f"continuation-bridge-sound-{index + 1}", "text": text}
                    for index, text in enumerate(bridge_sounds)
                ] + (shot.get("sound_cues") or [])

    result = {
        **copy.deepcopy(authored),
        "mode": "t2va",
        "duration_seconds": timing["sample_duration"],
        "width": parent["width"],
        "height": parent["height"],
        "target_megapixels": parent.get("target_megapixels"),
        "canvas_reference_id": "",
        "prompt_override": "",
        "shots": shots,
        "references": [],
        "task_types": [],
        "subject_definitions": [],
        "summary": "",
        "retention_analysis": [],
    }
    return normalize_document(result)


def _without_reference_tokens(value):
    return re.sub(
        r"<\s*(?:Picture|Video|Audio|Subject)\s+\d+\s*>",
        "the established reference",
        str(value or ""),
        flags=re.IGNORECASE,
    )


def build_continuation_document(
    parent_document,
    brief,
    duration_seconds,
    context_frames=CONTINUATION_CONTEXT_FRAMES,
    extension_document=None,
):
    """Build the segment-local prompt used with pinned audiovisual context."""
    parent = normalize_document(parent_document)
    timing = continuation_frame_plan(duration_seconds, context_frames)
    if extension_document is not None:
        return _build_structured_continuation_document(parent, extension_document, timing)
    complete_silence = bool(parent.get("complete_silence"))
    parent_music = str(parent.get("non_diegetic_music") or "N/A").strip()
    next_action = _without_reference_tokens(brief).strip()
    document = {
        "version": 1,
        "mode": "t2va",
        "duration_seconds": timing["sample_duration"],
        "width": parent["width"],
        "height": parent["height"],
        "target_megapixels": parent.get("target_megapixels"),
        "canvas_reference_id": "",
        "ref_image_size": parent.get("ref_image_size", "match"),
        "main_description": next_action,
        "prompt_override": "",
        "style": _without_reference_tokens(parent.get("style") or "Live-action, cinematic"),
        "shots": [_continuation_shot(parent, next_action)],
        "references": [],
        "overall_soundscape": (
            "N/A"
            if complete_silence
            else "The audible ambience and every sound already in progress at the boundary continue "
            "seamlessly from the carried audio context without a gap, restart, duplicate onset, or "
            "abrupt level change. New dialogue, ambience, music, and physical sounds explicitly "
            "requested by the continuation may begin naturally and remain synchronized with the image."
        ),
        "non_diegetic_music": (
            "N/A"
            if complete_silence or parent_music.upper() == "N/A"
            else "The established non-diegetic music continues seamlessly from the carried audio "
            "context, preserving its current tempo, rhythm, instrumentation, dynamics, and level "
            "unless the requested continuation explicitly changes it."
        ),
        "complete_silence": complete_silence,
        "task_types": [],
        "subject_definitions": [],
        "summary": "",
        "retention_analysis": [],
    }
    return normalize_document(document)


def _fraction(value):
    return Fraction(value.numerator, value.denominator) if value is not None else None


def _stream_key(stream):
    return stream.type


def _stream_signature(stream):
    codec = stream.codec_context
    if stream.type == "video":
        return (
            "video", codec.name, int(getattr(stream, "width", 0)), int(getattr(stream, "height", 0)),
            str(getattr(codec, "format", "")), bytes(codec.extradata or b""),
        )
    return (
        "audio", codec.name, int(getattr(codec, "sample_rate", 0)),
        str(getattr(codec, "layout", "")), bytes(codec.extradata or b""),
    )


def _media_duration(container, streams):
    durations = []
    for stream in streams:
        if stream.duration is not None and stream.time_base is not None:
            durations.append(Fraction(stream.duration) * _fraction(stream.time_base))
    if durations:
        return max(durations)
    if container.duration is not None:
        import av
        return Fraction(container.duration, av.time_base)
    raise ValueError("A continuation segment has no usable duration metadata")




def _normalize_overlap_frames(overlap_frames, source_count):
    if overlap_frames is None:
        return [0] * int(source_count)
    values = [int(value or 0) for value in overlap_frames]
    if len(values) != int(source_count):
        raise ValueError("Continuation overlap metadata does not match its source count")
    if values and values[0] != 0:
        raise ValueError("The first continuation source cannot have an incoming overlap")
    for value in values[1:]:
        if value not in {0, CONTINUATION_CONTEXT_FRAMES}:
            raise ValueError(
                f"Continuation overlap must be 0 or {CONTINUATION_CONTEXT_FRAMES} frames"
            )
    return values


def _probe_blend_streams(paths, overlaps):
    import av

    infos = []
    canvas = None
    sample_rate = None
    audio_layout = None
    for index, path in enumerate(paths):
        with av.open(path, mode="r") as container:
            if not container.streams.video:
                raise ValueError("A continuation source has no video stream")
            stream = container.streams.video[0]
            rate = float(stream.average_rate or 0)
            if not rate or abs(rate - float(FPS)) > 0.01:
                raise ValueError(f"Continuation assembly requires {FPS:g} fps CFR video")
            geometry = (int(stream.width or 0), int(stream.height or 0))
            if not all(geometry):
                raise ValueError("A continuation source has invalid video dimensions")
            geometry_check_frames = max(1, CONTINUATION_CONTEXT_FRAMES * 2)
            check_geometry(*geometry, geometry_check_frames)
            if canvas is None:
                canvas = geometry
            elif geometry != canvas:
                raise ValueError("Continuation sources must use matching video dimensions")
            frame_count = int(stream.frames or 0)
            if frame_count <= 0:
                if stream.duration is not None and stream.time_base is not None:
                    frame_count = round(float(stream.duration * stream.time_base) * FPS)
                elif container.duration is not None:
                    frame_count = round(float(container.duration / av.time_base) * FPS)
            outgoing_overlap = int(overlaps[index + 1]) if index + 1 < len(overlaps) else 0
            if frame_count <= int(overlaps[index]) + outgoing_overlap:
                raise ValueError("A continuation source is not longer than its incoming overlap")
            if container.streams.audio:
                audio = container.streams.audio[0]
                codec = audio.codec_context
                current_rate = int(codec.sample_rate or audio.rate or 0)
                current_layout = str(getattr(codec.layout, "name", "") or "")
                if current_rate > 0 and sample_rate is None:
                    sample_rate = current_rate
                if current_layout and audio_layout is None:
                    audio_layout = current_layout
            infos.append({"frames": frame_count, "width": geometry[0], "height": geometry[1]})
    check_duration(sum(item["frames"] for item in infos) / FPS, MAX_ASSEMBLY_SECONDS)
    if int(sample_rate or 32000) > MAX_SAMPLE_RATE:
        raise ValueError("Continuation audio sample rate exceeds the budget")
    return infos, int(sample_rate or 32000), str(audio_layout or "stereo")


def _resampled_audio_frames(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _decode_audio_array(path, sample_rate, layout, wanted_samples):
    return audio_array(path, sample_rate, end=int(wanted_samples) / sample_rate, layout=layout)


def _iter_crossfaded_video(paths, overlaps, infos):
    import av
    import numpy as np

    pending = deque()
    for index, path in enumerate(paths):
        incoming = int(overlaps[index])
        hold = int(overlaps[index + 1]) if index + 1 < len(paths) else 0
        with av.open(path, mode="r") as container:
            frames = iter(container.decode(video=0))
            if incoming:
                prefix = []
                for _position in range(incoming):
                    checkpoint()
                    try:
                        prefix.append(next(frames))
                    except StopIteration as exc:
                        raise ValueError("A continuation source ended inside its overlap") from exc
                if len(pending) != incoming:
                    raise ValueError("The preceding source does not contain the required seam window")
                denominator = max(1, incoming - 1)
                for position, (previous, current) in enumerate(zip(pending, prefix)):
                    if (previous.width, previous.height) != (current.width, current.height):
                        raise ValueError("Continuation overlap frames use different dimensions")
                    alpha = position / denominator
                    first = previous.to_ndarray(format="rgb24").astype(np.float32)
                    second = current.to_ndarray(format="rgb24").astype(np.float32)
                    blended = np.clip(
                        np.rint(first * (1.0 - alpha) + second * alpha), 0, 255
                    ).astype(np.uint8)
                    yield av.VideoFrame.from_ndarray(blended, format="rgb24")
                pending.clear()
            decoded_count = incoming
            for frame in frames:
                checkpoint()
                decoded_count += 1
                if decoded_count > int(infos[index]["frames"]) + 1:
                    raise ValueError("Video exceeds its declared frame count")
                if (int(frame.width), int(frame.height)) != (
                    int(infos[index]["width"]),
                    int(infos[index]["height"]),
                ):
                    raise ValueError("A continuation source changes dimensions mid-stream")
                pending.append(frame)
                if len(pending) > hold:
                    yield pending.popleft()
        if len(pending) != hold:
            raise ValueError("A continuation source does not contain its outgoing seam window")
    while pending:
        yield pending.popleft()


def _iter_owned_audio(paths, overlaps, infos, sample_rate, layout, wanted_total, chunk_size=1024):
    import numpy as np

    emitted = 0
    for index, path in enumerate(paths):
        segment_samples = round(int(infos[index]["frames"]) / FPS * sample_rate)
        outgoing = round(int(overlaps[index + 1]) / FPS * sample_rate) if index + 1 < len(paths) else 0
        if outgoing >= segment_samples:
            raise ValueError("A continuation audio stream is not longer than its seam window")
        available = min(segment_samples - outgoing, max(0, int(wanted_total) - emitted))
        if available <= 0:
            continue
        for value in iter_audio(path, sample_rate, layout, end=available / sample_rate, chunk_size=chunk_size):
            checkpoint(emitted, wanted_total)
            yield value
            emitted += value.shape[-1]
    if emitted < int(wanted_total):
        channels = len(__import__("av").AudioLayout(layout).channels)
        remaining = int(wanted_total) - emitted
        while remaining:
            count = min(int(chunk_size), remaining)
            yield np.zeros((channels, count), dtype=np.float32)
            remaining -= count


def _blend_media_files(paths, target_path, overlaps, metadata=None):
    """Encode a cumulative full-overlap video with incoming Soft AV audio ownership."""
    import av

    infos, sample_rate, layout = _probe_blend_streams(paths, overlaps)
    total_frames = sum(int(item["frames"]) for item in infos) - sum(overlaps)
    total_samples = round(total_frames / FPS * sample_rate)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(target_path) + ".",
        suffix=".tmp.mp4",
        dir=os.path.dirname(target_path),
    )
    os.close(descriptor)
    video_frames = audio_chunks = None
    try:
        with av.open(
            temporary,
            mode="w",
            format="mp4",
            options={"movflags": "use_metadata_tags+faststart"},
        ) as output:
            if metadata:
                output.metadata["promptstudio_continuation"] = json.dumps(
                    metadata, ensure_ascii=False, separators=(",", ":")
                )
            video_stream = output.add_stream("h264", rate=Fraction(int(FPS), 1))
            video_stream.width = int(infos[0]["width"])
            video_stream.height = int(infos[0]["height"])
            video_stream.pix_fmt = "yuv420p"
            video_stream.options = {"crf": "18", "preset": "medium"}
            audio_stream = output.add_stream("aac", rate=sample_rate)
            audio_stream.layout = layout
            audio_stream.bit_rate = 192000

            video_frames = iter(_iter_crossfaded_video(paths, overlaps, infos))
            audio_chunks = iter(
                _iter_owned_audio(
                    paths, overlaps, infos, sample_rate, layout, total_samples
                )
            )
            next_video = next(video_frames, None)
            next_audio = next(audio_chunks, None)
            video_index = 0
            audio_index = 0
            while next_video is not None or next_audio is not None:
                video_time = video_index / FPS if next_video is not None else math.inf
                audio_time = audio_index / sample_rate if next_audio is not None else math.inf
                if video_time <= audio_time:
                    frame = next_video.reformat(
                        width=video_stream.width,
                        height=video_stream.height,
                        format="yuv420p",
                    )
                    frame.pts = video_index
                    frame.time_base = Fraction(1, int(FPS))
                    for packet in video_stream.encode(frame):
                        output.mux(packet)
                    video_index += 1
                    next_video = next(video_frames, None)
                else:
                    frame = av.AudioFrame.from_ndarray(
                        next_audio, format="fltp", layout=layout
                    )
                    frame.sample_rate = sample_rate
                    frame.pts = audio_index
                    frame.time_base = Fraction(1, sample_rate)
                    for packet in audio_stream.encode(frame):
                        output.mux(packet)
                    audio_index += int(next_audio.shape[1])
                    next_audio = next(audio_chunks, None)
            if video_index != total_frames:
                raise ValueError(
                    f"Continuation assembly produced {video_index} frames; expected {total_frames}"
                )
            if audio_index != total_samples:
                raise ValueError(
                    f"Continuation assembly produced {audio_index} audio samples; expected {total_samples}"
                )
            for packet in video_stream.encode(None):
                output.mux(packet)
            for packet in audio_stream.encode(None):
                output.mux(packet)
        os.replace(temporary, target_path)
    finally:
        for iterator in (video_frames, audio_chunks):
            if iterator is not None:
                iterator.close()
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target_path

@bounded_media
def concatenate_media_files(source_paths, target_path, metadata=None, overlap_frames=None):
    """Assemble compatible MP4 segments, blending explicitly retained overlaps."""
    import av

    if not isinstance(source_paths, (list, tuple)) or not 2 <= len(source_paths) <= MAX_CONTINUATION_SOURCES:
        raise ValueError(f"Continuation assembly requires between 2 and {MAX_CONTINUATION_SOURCES} segments")
    paths = [os.path.abspath(path) for path in source_paths]
    if any(not os.path.isfile(path) for path in paths):
        raise ValueError("A continuation segment no longer exists")

    check_duration(sum(probe_video(path)["duration"] for path in paths), MAX_ASSEMBLY_SECONDS)
    overlaps = _normalize_overlap_frames(overlap_frames, len(paths))
    target_path = os.path.abspath(target_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if any(overlaps):
        return _blend_media_files(paths, target_path, overlaps, metadata)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(target_path) + ".",
        suffix=".tmp.mp4",
        dir=os.path.dirname(target_path),
    )
    os.close(descriptor)
    try:
        with av.open(paths[0], mode="r") as first:
            first_streams = [
                stream for stream in first.streams
                if stream.type in {"video", "audio"} and stream.codec_context is not None
            ]
            if not any(stream.type == "video" for stream in first_streams):
                raise ValueError("The first continuation segment has no video stream")
            if len({_stream_key(stream) for stream in first_streams}) != len(first_streams):
                raise ValueError("Continuation assembly supports one video and one audio stream per segment")
            signatures = {_stream_key(stream): _stream_signature(stream) for stream in first_streams}
            with av.open(
                temporary,
                mode="w",
                format="mp4",
                options={"movflags": "use_metadata_tags+faststart"},
            ) as output:
                if metadata:
                    output.metadata["promptstudio_continuation"] = json.dumps(
                        metadata, ensure_ascii=False, separators=(",", ":")
                    )
                output_streams = {
                    _stream_key(stream): output.add_stream_from_template(stream, opaque=True)
                    for stream in first_streams
                }
                timeline_offset = Fraction(0)
                for path in paths:
                    with av.open(path, mode="r") as source:
                        streams = [
                            stream for stream in source.streams
                            if stream.type in output_streams and stream.codec_context is not None
                        ]
                        source_map = {_stream_key(stream): stream for stream in streams}
                        if set(source_map) != set(output_streams):
                            raise ValueError("Continuation segments do not contain matching audio/video streams")
                        for key, stream in source_map.items():
                            if _stream_signature(stream) != signatures[key]:
                                raise ValueError(
                                    "Continuation segments must use matching dimensions, codecs, and audio settings"
                                )
                        segment_duration = _media_duration(source, streams)
                        starts = {}
                        for packet in source.demux(*streams):
                            checkpoint()
                            if packet.dts is None:
                                continue
                            key = _stream_key(packet.stream)
                            time_base = _fraction(packet.time_base or packet.stream.time_base)
                            if time_base is None or time_base <= 0:
                                raise ValueError("A continuation packet has no valid time base")
                            starts.setdefault(key, int(packet.dts))
                            base = starts[key]
                            relative_dts = Fraction(int(packet.dts) - base) * time_base
                            # AAC and some video encoders emit a final padding packet
                            # outside the container's display duration. Keeping it
                            # would overlap the first packet of the next segment.
                            if relative_dts >= segment_duration:
                                continue
                            offset = int(round(timeline_offset / time_base))
                            packet.dts = int(packet.dts) + offset
                            if packet.pts is not None:
                                packet.pts = int(packet.pts) + offset
                            packet.stream = output_streams[key]
                            output.mux(packet)
                        timeline_offset += segment_duration
        os.replace(temporary, target_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target_path


@assembly_job
def assemble_generation_outputs(source_descriptors, project_id, generation_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(project_id or "")):
        raise ValueError("Project identifier is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(generation_id or "")):
        raise ValueError("Generation identifier is invalid")
    if not isinstance(source_descriptors, list) or not 2 <= len(source_descriptors) <= MAX_CONTINUATION_SOURCES:
        raise ValueError("Continuation assembly sources must be a list")
    resolved = []
    overlaps = []
    for index, item in enumerate(source_descriptors):
        resolved.append(resolve_output_path(item))
        try:
            overlap = int(item.get("overlap_frames") or 0)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Continuation source {index + 1} has invalid overlap metadata") from exc
        overlaps.append(overlap)
    overlaps = _normalize_overlap_frames(overlaps, len(resolved))
    import folder_paths

    subfolder = f"video/PromptStudio_Video/continuations/{project_id}"
    filename = f"{generation_id}.mp4"
    target = os.path.abspath(os.path.join(folder_paths.get_output_directory(), subfolder, filename))
    root = os.path.abspath(folder_paths.get_output_directory())
    if os.path.commonpath((root, target)) != root:
        raise ValueError("Continuation output path escapes the ComfyUI output directory")
    concatenate_media_files(
        [path for path, _descriptor in resolved],
        target,
        metadata={"project_id": project_id, "generation_id": generation_id},
        overlap_frames=overlaps,
    )
    return {"filename": filename, "subfolder": subfolder, "type": "output"}
