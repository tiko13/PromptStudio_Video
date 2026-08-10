"""Native MiniMax H3 continuation documents and immutable media assembly."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from fractions import Fraction

from .contracts import FPS, PromptDocumentError, normalize_document


CONTINUATION_CONTEXT_FRAMES = 22
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
    if not filename or filename != os.path.basename(filename) or filename in {".", ".."}:
        raise ValueError(f"{label} has an invalid filename")
    if output_type not in OUTPUT_TYPES:
        raise ValueError(f"{label} must be a saved ComfyUI output")
    parts = [part for part in subfolder.split("/") if part]
    if any(part in {".", ".."} for part in parts):
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


def _continuation_shot(parent, brief, action_start):
    opening = (
        "The carried opening frames are the exact end of the preceding clip. After those frames, "
        "the camera holds completely still in the same closing position and framing. The visible "
        "subjects, wardrobe, objects, poses, environment, lighting, and exposure remain unchanged. "
        "Do not restart, reverse, or invent an action during this hold. There is no cut, zoom, "
        "reframing, camera correction, or new sound event."
    )
    next_action = str(brief or "").strip() or "Continue the visible action naturally."
    return {
        "id": "continuation-shot-1",
        "start": 0,
        "transition": "the shot continues without a cut",
        "composition": "The opening retains the exact closing composition and framing of the preceding clip.",
        "subjects": "All established visible subjects, wardrobe, objects, poses, and spatial relationships remain consistent.",
        "environment": "The established environment continues without a reset or newly introduced element.",
        "lighting": "The established lighting and exposure remain continuous.",
        "camera": {"type": "Static Shot", "amplitude": "default", "speed": "default", "target": ""},
        "steps": [
            {"id": "continuation-opening", "type": "action", "text": opening},
            {
                "id": "continuation-action",
                "type": "action",
                "text": f"At {action_start:.2f} seconds, the continuation develops: {next_action}",
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
                "No new sound event begins during the opening hold.",
                "After the hold, only sounds directly caused by visible new actions occur.",
            ]
        ),
        "notes": "",
    }


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
):
    """Build the segment-local prompt used with pinned audiovisual context."""
    parent = normalize_document(parent_document)
    timing = continuation_frame_plan(duration_seconds, context_frames)
    complete_silence = bool(parent.get("complete_silence"))
    parent_music = str(parent.get("non_diegetic_music") or "N/A").strip()
    action_start = max(2.0, timing["context_seconds"] + 0.75)
    document = {
        "version": 1,
        "mode": "t2va",
        "duration_seconds": timing["sample_duration"],
        "width": parent["width"],
        "height": parent["height"],
        "target_megapixels": parent.get("target_megapixels"),
        "canvas_reference_id": "",
        "ref_image_size": parent.get("ref_image_size", "match"),
        "main_description": str(brief or "").strip(),
        "prompt_override": "",
        "style": _without_reference_tokens(parent.get("style") or "Live-action, cinematic"),
        "shots": [_continuation_shot(parent, brief, action_start)],
        "references": [],
        "overall_soundscape": (
            "N/A"
            if complete_silence
            else "No new dialogue, ambience, music, or off-screen sound begins during the opening hold. "
            "Continue only the sound bed already present in the carried audio context, at the same "
            "level and character, without adding another ambient layer. Afterward, add only physical "
            "sounds directly caused by visible new actions."
        ),
        "non_diegetic_music": (
            "N/A"
            if complete_silence or parent_music.upper() == "N/A"
            else "No new musical layer begins. Existing non-diegetic music continues without a change "
            "in instrumentation, tempo, rhythm, dynamics, or level."
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


def concatenate_media_files(source_paths, target_path, metadata=None):
    """Losslessly remux compatible MP4 segments onto one cumulative timeline."""
    import av

    paths = [os.path.abspath(path) for path in source_paths]
    if len(paths) < 2 or len(paths) > MAX_CONTINUATION_SOURCES:
        raise ValueError(f"Continuation assembly requires between 2 and {MAX_CONTINUATION_SOURCES} segments")
    if any(not os.path.isfile(path) for path in paths):
        raise ValueError("A continuation segment no longer exists")

    target_path = os.path.abspath(target_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
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


def assemble_generation_outputs(source_descriptors, project_id, generation_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(project_id or "")):
        raise ValueError("Project identifier is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(generation_id or "")):
        raise ValueError("Generation identifier is invalid")
    resolved = [resolve_output_path(item) for item in source_descriptors]
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
    )
    return {"filename": filename, "subfolder": subfolder, "type": "output"}
