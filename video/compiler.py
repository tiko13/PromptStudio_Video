"""Deterministic MiniMax H3 prompt compiler."""

from __future__ import annotations

import re

from .contracts import PromptDocumentError, effective_duration, normalize_document


def _canonical_tokens(value):
    value = re.sub(
        r"<\s*(Picture|Video|Audio|Subject|Shot)\s*(\d+)\s*>",
        lambda match: (
            f"[Shot {match.group(2)}]"
            if match.group(1).casefold() == "shot"
            else f"<{match.group(1).title()} {match.group(2)}>"
        ),
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\[\s*Shot\s+(\d+)\s*\]",
        lambda match: f"[Shot {match.group(1)}]",
        value,
        flags=re.IGNORECASE,
    )


def _sentence(value, *, capitalize=True):
    value = str(value or "").strip()
    if not value:
        return ""
    if capitalize and value[0].islower():
        value = value[0].upper() + value[1:]
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
        phrase += f" toward {_canonical_tokens(camera['target'])}"
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
    verb = "sings" if event.get("performance") == "singing" else "says"
    location = " off-screen" if event.get("offscreen") else ""
    return _sentence(f"{speaker} ({speaker_id}) {verb}{location}{delivery}: {block}")


def _step_text(step):
    if step.get("type") == "dialogue":
        return _dialogue_text(step) if step.get("text") else ""
    if step.get("type") == "action" and step.get("text"):
        return _sentence(_canonical_tokens(step["text"]))
    return ""


def _shot_text(shot, index, *, include_style="", first_frame_lock=False):
    if index == 0:
        prefix = "[Shot 1]"
    else:
        transition = _canonical_tokens(shot.get("transition") or "the camera cuts to").rstrip(" .")
        if transition.casefold().endswith(" to"):
            transition += " a new view"
        prefix = f"[Shot {index + 1}] At {_cut_time(shot['start'])}, {_sentence(transition, capitalize=False)}"
    parts = []
    if first_frame_lock and index == 0:
        parts.append(_sentence(
            "The style, subjects, composition, scene, lighting, clothing, colors, key objects, and spatial "
            "relationships established by <Picture 1> remain fully preserved"
        ))
    if include_style:
        parts.append(_sentence(_canonical_tokens(include_style)))
    visual_fields = () if first_frame_lock and index == 0 else (
        "composition", "subjects", "environment", "lighting"
    )
    for field in visual_fields:
        if shot.get(field):
            parts.append(_sentence(_canonical_tokens(shot[field])))
    camera = _camera_sentence(shot.get("camera") or {})
    if camera:
        parts.append(camera)
    parts.extend(_step_text(step) for step in shot.get("steps") or [])
    for visible in shot.get("visible_text") or []:
        escaped = visible.replace('"', '\\"')
        parts.append(_sentence(f'A visible text element reads "{escaped}"'))
    parts.extend(_sentence(_canonical_tokens(sound)) for sound in shot.get("sounds") or [])
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
        _shot_text(
            shot,
            index,
            include_style=document["style"] if index == 0 and mode != "i2va" else "",
            first_frame_lock=mode == "i2va",
        )
        for index, shot in enumerate(document["shots"])
    )
    fields = [
        f"integrated_multimodal_description: {shots}",
        f"overall_soundscape: {_canonical_tokens('N/A' if document['complete_silence'] else document['overall_soundscape'])}",
        f"non_diegetic_music: {_canonical_tokens(document['non_diegetic_music'] or 'N/A')}",
    ]
    alignment = _base_alignment(mode, len(document["shots"]), duration)
    return f"{alignment}\n\n" + "\n\n".join(fields) if alignment else "\n\n".join(fields)


def _definition_lines(document):
    lines = []
    for item in document["subject_definitions"]:
        label = item["label"].strip("<>")
        lines.append(_canonical_tokens(f"<{label}> {item['text']}".rstrip()))
    return lines


def _retention_lines(document):
    lines = []
    for item in document["retention_analysis"]:
        where = f" ({item['where']})" if item["where"] else ""
        detail = f" - {item['detail']}" if item["detail"] else ""
        lines.append(_canonical_tokens(f"{item['label']}{where}: {item['relationship']}{detail}"))
    return lines


def _compile_reference(document):
    definitions = "\n".join(_definition_lines(document))
    task_types = document["task_types"] or ["reference generation"]
    summary = _canonical_tokens(f"[{' + '.join(task_types)}] {document['summary']}".rstrip())
    retention = "\n".join(_retention_lines(document))
    shots = " ".join(
        _shot_text(shot, index)
        for index, shot in enumerate(document["shots"])
    )
    detailed = " ".join(part for part in (
        _sentence(_canonical_tokens(document["style"])),
        shots,
    ) if part)
    return "\n\n".join([
        f"subject_definitions:\n{definitions}".rstrip(),
        f"summary:\n{summary}".rstrip(),
        f"retention_analysis:\n{retention}".rstrip(),
        f"detailed_description:\n{detailed}".rstrip(),
        f"overall_soundscape:\n{_canonical_tokens('N/A' if document['complete_silence'] else document['overall_soundscape'])}".rstrip(),
        f"non_diegetic_music:\n{_canonical_tokens(document['non_diegetic_music'] or 'N/A')}",
    ])


def _reference_semantic_issues(document):
    """Return guide-compliance failures that would make REF2VA references inert."""
    definitions = document["subject_definitions"]
    retention = document["retention_analysis"]
    issues = []
    if not definitions:
        issues.append("subject_definitions is empty")
    if not document["summary"]:
        issues.append("summary is empty")
    if not retention:
        issues.append("retention_analysis is empty")
    if issues:
        return issues

    roles = {
        role for reference in document["references"] for role in reference.get("roles", [])
    }
    expected_tasks = []
    if roles & {"first_frame", "last_frame"}:
        expected_tasks.append("keyframe completion")
    if roles & {"subject", "scene", "style", "action", "pose", "camera", "storyboard"}:
        expected_tasks.append("reference generation")
    if "video_edit" in roles:
        expected_tasks.append("video editing")
    if "video_continue" in roles:
        expected_tasks.append("video continuation")
    if "audio_copy" in roles:
        expected_tasks.append("audio reuse")
    if "audio_reference" in roles:
        expected_tasks.append("audio reference")
    missing_tasks = [task for task in expected_tasks if task not in document["task_types"]]
    if missing_tasks:
        issues.append("task_types is missing: " + ", ".join(missing_tasks))

    definition_tokens = []
    definition_blob_parts = []
    for index, item in enumerate(definitions):
        token = _canonical_tokens(f"<{item['label'].strip('<>')}>")
        definition_tokens.append(token)
        definition_blob_parts.extend((token, _canonical_tokens(item["text"])))
        if not item["text"]:
            issues.append(f"definition {index + 1} has no description")
        placeholder = re.search(
            r"\b(?:concrete|specific|observed|visible)\s+(?:visible\s+)?(?:identity\s+)?traits?\b",
            item["text"],
            re.IGNORECASE,
        )
        concrete_trait = re.search(
            r"\b(hair|dress|shirt|blouse|jacket|coat|skirt|trousers|pants|clothing|wardrobe|"
            r"necklace|jewelry|eyes?|face|facial|skin|fur|color|pattern|texture|material|silhouette|build)\b",
            item["text"],
            re.IGNORECASE,
        )
        if placeholder and not concrete_trait:
            issues.append(f"definition {index + 1} contains unresolved visual-trait placeholder language")
    duplicate_definitions = sorted({token for token in definition_tokens if definition_tokens.count(token) > 1})
    if duplicate_definitions:
        issues.append("duplicate definitions: " + ", ".join(duplicate_definitions))

    definition_blob = " ".join(definition_blob_parts).casefold()
    for reference in document["references"]:
        token = _canonical_tokens(reference["label"])
        if token.casefold() not in definition_blob:
            issues.append(f"{token} is not represented in subject_definitions")

    retention_labels = [_canonical_tokens(item["label"]) for item in retention]
    retention_keys = {label.casefold() for label in retention_labels}
    definition_keys = {token.casefold() for token in definition_tokens}
    for token in definition_tokens:
        if token.casefold() not in retention_keys:
            issues.append(f"{token} has no retention_analysis entry")
    for index, item in enumerate(retention):
        token = retention_labels[index]
        if token.casefold() not in definition_keys:
            issues.append(f"retention_analysis uses undefined label {token or '(empty label)'}")
        if not item["detail"]:
            issues.append(f"retention entry for {token or f'item {index + 1}'} has no explanation")

    shot_count = len(document["shots"])
    structured_shot_mentions = [
        ("summary", document["summary"]),
        *(
            (f"definition {index + 1}", item["text"])
            for index, item in enumerate(definitions)
        ),
        *(
            (f"retention entry for {retention_labels[index] or f'item {index + 1}'}", item["where"])
            for index, item in enumerate(retention)
        ),
    ]
    for location, value in structured_shot_mentions:
        missing = sorted({
            int(number) for number in re.findall(r"\[\s*Shot\s+(\d+)\s*\]", value, re.IGNORECASE)
            if int(number) < 1 or int(number) > shot_count
        })
        if missing:
            issues.append(
                f"{location} references missing "
                + ", ".join(f"[Shot {number}]" for number in missing)
            )

    summary = _canonical_tokens(document["summary"]).casefold()
    # main_description is a planning synopsis for the user and Grand Director,
    # not part of the generated prompt. Reference tokens must be grounded in a
    # compiled shot or audio field to count as active.
    detailed_parts = [document["style"]]
    for shot in document["shots"]:
        detailed_parts.extend(
            shot.get(name, "")
            for name in ("composition", "subjects", "environment", "lighting", "action", "notes")
        )
        detailed_parts.extend(shot.get("sounds") or [])
        detailed_parts.append((shot.get("camera") or {}).get("target", ""))
    detailed_parts.extend((document["overall_soundscape"], document["non_diegetic_music"]))
    detailed = _canonical_tokens(" ".join(str(value or "") for value in detailed_parts)).casefold()
    for token in definition_tokens:
        if token.casefold() not in summary:
            issues.append(f"summary does not use {token}")
        if token.casefold() not in detailed:
            issues.append(f"the shot/audio description does not use {token}")
    return issues


def validate_reference_semantics(document):
    if document["resolved_mode"] != "ref2va":
        return
    issues = _reference_semantic_issues(document)
    if issues:
        raise PromptDocumentError(
            "REF2VA reference semantics are incomplete: " + "; ".join(issues)
        )


def compile_prompt(value, *, use_override=True):
    document = normalize_document(value)
    if use_override and document["prompt_override"]:
        return document["prompt_override"]
    if document["resolved_mode"] == "ref2va":
        validate_reference_semantics(document)
        return _compile_reference(document)
    return _compile_base(document)
