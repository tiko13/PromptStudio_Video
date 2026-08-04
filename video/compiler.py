"""Deterministic MiniMax H3 prompt compiler."""

from __future__ import annotations

from .contracts import effective_duration, normalize_document


def _sentence(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value[-1] in ".!?" else f"{value}."


def _cut_time(seconds):
    total_ms = max(0, int(round(float(seconds) * 1000)))
    minutes, remainder = divmod(total_ms, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _camera_sentence(camera):
    motion = camera.get("type") or ""
    if not motion:
        return ""
    phrase = {
        "Zoom In": "The camera zooms in",
        "Zoom Out": "The camera zooms out",
        "Push In": "The camera pushes in",
        "Pull Out": "The camera pulls out",
        "Pan Left": "The camera pans left",
        "Pan Right": "The camera pans right",
        "Truck Left": "The camera trucks left",
        "Truck Right": "The camera trucks right",
        "Tilt Up": "The camera tilts up",
        "Tilt Down": "The camera tilts down",
        "Pedestal Up": "The camera moves upward on a pedestal",
        "Pedestal Down": "The camera moves downward on a pedestal",
        "Arc Shot": "The camera moves in an arc around the subject",
        "Tracking Shot": "The camera follows the moving subject in a tracking shot",
        "Static Shot": "The camera holds a static shot",
        "POV": "The camera adopts a POV perspective",
        "Shake Slightly": "The camera shakes slightly",
        "Shake Strongly": "The camera shakes strongly",
        "Roll Clockwise": "The camera rolls clockwise around the lens axis",
        "Roll Counterclockwise": "The camera rolls counterclockwise around the lens axis",
    }.get(motion, f"The camera performs a {motion.lower()}")
    amplitude = camera.get("amplitude")
    speed = camera.get("speed")
    if amplitude in {"small", "large"}:
        phrase += f" with {amplitude} amplitude"
    if speed in {"slow", "fast"}:
        phrase += f" at {speed} speed"
    if camera.get("target"):
        phrase += f" toward {camera['target']}"
    return _sentence(phrase)


def _dialogue_text(event):
    speaker = event["speaker"]
    speaker_id = event["speaker_id"]
    delivery = f" {event['delivery']}" if event.get("delivery") else ""
    dialogue = event["text"]
    if event.get("crosses_cut"):
        dialogue = f"<scenetrans>{dialogue}<scenetrans>"
    if event.get("cutoff"):
        dialogue = f"{dialogue}<cutoff>"
    block = f"<d>[{event['language']}] {dialogue}</d>"
    if event.get("voiceover"):
        return _sentence(
            f"{speaker} ({speaker_id}) says in an off-screen voiceover{delivery}: "
            f"{block} while the corresponding on-screen character's lips remain completely closed"
        )
    location = " off-screen" if event.get("offscreen") else ""
    return _sentence(f"{speaker} ({speaker_id}) says{location}{delivery}: {block}")


def _shot_text(shot, index, *, include_style=""):
    if index == 0:
        prefix = "[Shot 1]"
    else:
        transition = shot.get("transition") or "the camera cuts to"
        prefix = f"[Shot {index + 1}] At {_cut_time(shot['start'])}, {transition}"
    parts = []
    if include_style:
        parts.append(_sentence(include_style))
    for field in ("composition", "subjects", "environment", "lighting", "action"):
        if shot.get(field):
            parts.append(_sentence(shot[field]))
    camera = _camera_sentence(shot.get("camera") or {})
    if camera:
        parts.append(camera)
    parts.extend(_dialogue_text(event) for event in shot.get("dialogue") or [] if event.get("text"))
    for visible in shot.get("visible_text") or []:
        escaped = visible.replace('"', '\\"')
        parts.append(_sentence(f'A visible text element reads "{escaped}"'))
    parts.extend(_sentence(sound) for sound in shot.get("sounds") or [])
    body = " ".join(part for part in parts if part)
    return f"{prefix} {body}".strip()


def _base_alignment(mode, final_shot, duration):
    if mode == "t2va":
        return ""
    if mode == "i2va":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — Picture 1 (from Shot 1) "
            "aligns with the 0.00-second mark of the target video; Picture 2 "
            f"(from Shot {final_shot}) aligns with the {duration:.2f}-second mark of the target video."
        )
    return (
        "How the reference pictures align with the target video — <Picture 1> "
        f"(from [Shot {final_shot}]) aligns with the {duration:.2f}-second mark of the target video."
    )


def _compile_base(document):
    mode = document["resolved_mode"]
    duration = effective_duration(document)
    shots = " ".join(
        _shot_text(shot, index, include_style=document["style"] if index == 0 else "")
        for index, shot in enumerate(document["shots"])
    )
    fields = [
        f"integrated_multimodal_description: {shots}",
        f"overall_soundscape: {'N/A' if document['complete_silence'] else document['overall_soundscape']}",
        f"non_diegetic_music: {document['non_diegetic_music'] or 'N/A'}",
    ]
    alignment = _base_alignment(mode, len(document["shots"]), duration)
    return f"{alignment}\n\n" + "\n\n".join(fields) if alignment else "\n\n".join(fields)


def _definition_lines(document):
    lines = []
    for item in document["subject_definitions"]:
        label = item["label"].strip("<>")
        lines.append(f"<{label}> {item['text']}".rstrip())
    return lines


def _retention_lines(document):
    lines = []
    for item in document["retention_analysis"]:
        where = f" ({item['where']})" if item["where"] else ""
        detail = f" - {item['detail']}" if item["detail"] else ""
        lines.append(f"{item['label']}{where}: {item['relationship']}{detail}")
    return lines


def _compile_reference(document):
    definitions = "\n".join(_definition_lines(document))
    task_types = document["task_types"] or ["reference generation"]
    summary = f"[{' + '.join(task_types)}] {document['summary']}".rstrip()
    retention = "\n".join(_retention_lines(document))
    shots = " ".join(
        _shot_text(shot, index)
        for index, shot in enumerate(document["shots"])
    )
    detailed = " ".join(part for part in (_sentence(document["style"]), shots) if part)
    return "\n\n".join([
        f"subject_definitions:\n{definitions}".rstrip(),
        f"summary:\n{summary}".rstrip(),
        f"retention_analysis:\n{retention}".rstrip(),
        f"detailed_description:\n{detailed}".rstrip(),
        f"overall_soundscape:\n{'N/A' if document['complete_silence'] else document['overall_soundscape']}".rstrip(),
        f"non_diegetic_music:\n{document['non_diegetic_music'] or 'N/A'}",
    ])


def compile_prompt(value):
    document = normalize_document(value)
    if document["resolved_mode"] == "ref2va":
        return _compile_reference(document)
    return _compile_base(document)
