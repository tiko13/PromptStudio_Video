"""Structured prompt contracts shared by the MiniMax H3 node and UI."""

from __future__ import annotations

import copy
import json
import math
import re
import uuid


DOCUMENT_VERSION = 1
FPS = 24
FRAME_GRID = 17
FRAME_REMAINDER = 5
MIN_FRAME_COUNT = 5
MAX_FRAME_COUNT = 3600
CANVAS_MULTIPLE = 32
DEFAULT_CANVAS_MEGAPIXELS = (768 * 1344) / 1_000_000
MIN_CANVAS_MEGAPIXELS = 0.1
MAX_CANVAS_MEGAPIXELS = 4.0

BASE_MODES = {"t2va", "i2va", "fl2va", "l2va"}
MODES = {"auto", *BASE_MODES, "ref2va"}
ANCHOR_ROLES = {"first_frame", "last_frame"}
REFERENCE_ROLES = {
    "subject",
    "scene",
    "style",
    "action",
    "pose",
    "camera",
    "storyboard",
    "video_edit",
    "video_continue",
    "audio_copy",
    "audio_reference",
}
REFERENCE_KINDS = {"image", "video", "audio"}
TASK_TYPES = {
    "keyframe completion",
    "reference generation",
    "video editing",
    "video continuation",
    "audio reuse",
    "audio reference",
}
VISUAL_RETENTION = {
    "fully_preserved",
    "partially_preserved",
    "attribute_transfer",
    "weak_reference",
}
AUDIO_RETENTION = {"fully_copy", "partially_copy", "reference", "weak_reference"}
RETENTION_RELATIONSHIPS = VISUAL_RETENTION | AUDIO_RETENTION
RETENTION_RELATIONSHIP_ALIASES = {
    "full_preservation": "fully_preserved",
    "complete_preservation": "fully_preserved",
    "partial_preservation": "partially_preserved",
    "attributes_transferred": "attribute_transfer",
    "attribute_transferred": "attribute_transfer",
    "weakly_referenced": "weak_reference",
    "fully_copied": "fully_copy",
    "full_copy": "fully_copy",
    "partial_copy": "partially_copy",
    "partially_copied": "partially_copy",
    "referenced": "reference",
}
CAMERA_TYPES = {
    "",
    "Zoom In",
    "Zoom Out",
    "Push In",
    "Pull Out",
    "Pan Left",
    "Pan Right",
    "Truck Left",
    "Truck Right",
    "Tilt Up",
    "Tilt Down",
    "Pedestal Up",
    "Pedestal Down",
    "Arc Shot",
    "Tracking Shot",
    "Static Shot",
    "Shake Slightly",
    "Shake Strongly",
    "POV",
    "Roll Clockwise",
    "Roll Counterclockwise",
}


class PromptDocumentError(ValueError):
    """Raised when a structured video prompt document is unsafe or inconsistent."""


def _text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _number(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def normalize_retention_relationship(value, default="fully_preserved"):
    """Canonicalize safe spelling variants without changing retention semantics."""
    relationship = re.sub(r"[^a-z0-9]+", "_", _text(value, default).casefold()).strip("_")
    return RETENTION_RELATIONSHIP_ALIASES.get(relationship, relationship)


def _identifier(value, prefix):
    value = _text(value)
    if value and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        return value
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _canonical_reference_tokens(value):
    """Normalize legacy labels to MiniMax's guide-exact reference grammar."""
    value = re.sub(
        r"<\s*(Picture|Video|Audio|Subject|Shot)\s+(\d+)\s*>",
        lambda match: (
            f"[Shot {match.group(2)}]"
            if match.group(1).casefold() == "shot"
            else f"<{match.group(1).title()} {match.group(2)}>"
        ),
        str(value or ""),
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"<\s*(Picture|Video|Audio|Subject|Shot)\s*(\d+)\s*>",
        lambda match: (
            f"[Shot {match.group(2)}]"
            if match.group(1).casefold() == "shot"
            else f"<{match.group(1).title()} {match.group(2)}>"
        ),
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\[\s*Shot\s+(\d+)\s*\]",
        lambda match: f"[Shot {match.group(1)}]",
        value,
        flags=re.IGNORECASE,
    )


def align_frame_count(frame_count):
    """Snap upward to MiniMax H3's 17k+5 temporal grid."""
    count = max(MIN_FRAME_COUNT, int(round(_number(frame_count, MIN_FRAME_COUNT))))
    while count % FRAME_GRID != FRAME_REMAINDER:
        count += 1
    if count > MAX_FRAME_COUNT:
        raise PromptDocumentError(f"MiniMax frame count exceeds {MAX_FRAME_COUNT} frames")
    return count


def frame_count_for_duration(seconds):
    seconds = _number(seconds, 5.0)
    if seconds <= 0:
        raise PromptDocumentError("Video duration must be positive")
    return align_frame_count(seconds * FPS)


def effective_duration(document_or_seconds):
    seconds = (
        document_or_seconds.get("duration_seconds", 5.0)
        if isinstance(document_or_seconds, dict)
        else document_or_seconds
    )
    return frame_count_for_duration(seconds) / FPS


def adapt_canvas(width, height, target_megapixels=DEFAULT_CANVAS_MEGAPIXELS):
    """Preserve a source ratio near the Studio's 1 MP target on MiniMax's grid."""
    width = _number(width)
    height = _number(height)
    if width <= 0 or height <= 0:
        raise PromptDocumentError("Canvas source dimensions must be positive")
    target_megapixels = min(
        MAX_CANVAS_MEGAPIXELS,
        max(MIN_CANVAS_MEGAPIXELS, _number(target_megapixels, DEFAULT_CANVAS_MEGAPIXELS)),
    )
    scale = math.sqrt((target_megapixels * 1_000_000) / (width * height))
    nominal_width = width * scale
    nominal_height = height * scale
    return (
        max(CANVAS_MULTIPLE, round(nominal_width / CANVAS_MULTIPLE) * CANVAS_MULTIPLE),
        max(CANVAS_MULTIPLE, round(nominal_height / CANVAS_MULTIPLE) * CANVAS_MULTIPLE),
    )


def _canvas_dimension(value, default):
    return max(CANVAS_MULTIPLE, round(_number(value, default) / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)


def _normalize_reference(reference, index):
    if not isinstance(reference, dict):
        raise PromptDocumentError(f"Reference {index + 1} must be an object")
    kind = _text(reference.get("kind"), "image").lower()
    if kind not in REFERENCE_KINDS:
        raise PromptDocumentError(f"Reference {index + 1} has unsupported kind '{kind}'")
    roles = reference.get("roles")
    if isinstance(roles, str):
        roles = [roles]
    roles = [_text(role).lower() for role in (roles or []) if _text(role)]
    unknown = set(roles) - ANCHOR_ROLES - REFERENCE_ROLES
    if unknown:
        raise PromptDocumentError(
            f"Reference {index + 1} has unsupported role '{sorted(unknown)[0]}'"
        )
    if kind != "image" and set(roles) & ANCHOR_ROLES:
        raise PromptDocumentError("Only images can be first-frame or last-frame anchors")
    return {
        "id": _identifier(reference.get("id"), "reference"),
        "kind": kind,
        "path": _text(reference.get("path")),
        "name": _text(reference.get("name") or reference.get("path"), f"Reference {index + 1}"),
        "roles": list(dict.fromkeys(roles)),
        "prompt": _text(reference.get("prompt")),
        "label": _canonical_reference_tokens(_text(reference.get("label"))),
        "trim_start": max(0.0, _number(reference.get("trim_start"), 0.0)),
        "trim_end": (
            max(0.0, _number(reference.get("trim_end")))
            if reference.get("trim_end") is not None
            else None
        ),
        "use_embedded_audio": bool(reference.get("use_embedded_audio", False)),
        "source_width": max(0, int(_number(reference.get("source_width"), 0))),
        "source_height": max(0, int(_number(reference.get("source_height"), 0))),
        # Cached Director grounding is descriptive metadata, not prompt prose.
        # It lets later conversational turns keep stable subject bindings without
        # requiring the user to attach the same project reference again.
        "observed_visual_facts": _text(reference.get("observed_visual_facts")),
        "subject_candidates": [
            {
                "name": _text(item.get("name")),
                "location": _text(item.get("location")),
                "visual_selectors": [
                    _text(selector)
                    for selector in (item.get("visual_selectors") or [])
                    if _text(selector)
                ][:16],
                "grounded_attributes": {
                    str(key): _text(value)
                    for key, value in (item.get("grounded_attributes") or {}).items()
                    if str(key) in {"hair", "face", "clothing", "footwear", "accessories", "body", "other"}
                    and _text(value)
                } if isinstance(item.get("grounded_attributes"), dict) else {},
            }
            for item in (reference.get("subject_candidates") or [])
            if isinstance(item, dict) and _text(item.get("name"))
        ][:16],
    }


def _normalize_dialogue(event, index):
    if not isinstance(event, dict):
        raise PromptDocumentError(f"Dialogue event {index + 1} must be an object")
    speaker_id = _text(event.get("speaker_id"), "S1").upper()
    if not re.fullmatch(r"S\d+(?:,S\d+)*", speaker_id):
        raise PromptDocumentError(f"Dialogue event {index + 1} has invalid speaker ID")
    performance = _text(event.get("performance"), "speech").lower()
    if performance not in {"speech", "singing"}:
        raise PromptDocumentError(f"Dialogue event {index + 1} has invalid performance type")
    if performance == "singing" and bool(event.get("voiceover", False)):
        raise PromptDocumentError("Singing must use offscreen rather than voiceover")
    text = str(event.get("text") or "").strip()
    delivery = _text(event.get("delivery")).strip(" :,-")
    delivery = re.sub(
        r"^(?:says?\s+in\s+an\s+off[- ]screen\s+voiceover|says?|sings?)\b\s*",
        "",
        delivery,
        flags=re.IGNORECASE,
    ).strip(" :,-")
    return {
        "id": _identifier(event.get("id") or f"dialogue-{index + 1}", "dialogue"),
        "speaker": _text(event.get("speaker"), "The speaker"),
        "speaker_id": speaker_id,
        "language": _text(event.get("language"), "English"),
        "performance": performance,
        "text": text,
        "delivery": delivery,
        "voiceover": bool(event.get("voiceover", False)),
        "offscreen": bool(event.get("offscreen", False)),
        "crosses_cut": bool(event.get("crosses_cut", False)),
        "cutoff": bool(event.get("cutoff", False)),
    }


def _normalize_step(step, index):
    if not isinstance(step, dict):
        raise PromptDocumentError(f"Shot step {index + 1} must be an object")
    step_type = _text(step.get("type")).lower()
    if step_type == "action":
        return {
            "id": _identifier(step.get("id") or f"step-{index + 1}", "step"),
            "type": "action",
            "text": _text(step.get("text")),
        }
    if step_type == "dialogue":
        dialogue = _normalize_dialogue(step, index)
        return {**dialogue, "type": "dialogue"}
    raise PromptDocumentError(f"Shot step {index + 1} has unsupported type '{step_type}'")


def _normalize_shot(shot, index):
    if not isinstance(shot, dict):
        raise PromptDocumentError(f"Shot {index + 1} must be an object")
    camera = shot.get("camera") if isinstance(shot.get("camera"), dict) else {}
    camera_type = _text(camera.get("type"))
    if camera_type not in CAMERA_TYPES:
        raise PromptDocumentError(f"Shot {index + 1} has unsupported camera motion '{camera_type}'")
    amplitude = _text(camera.get("amplitude"), "default").lower()
    speed = _text(camera.get("speed"), "default").lower()
    if amplitude not in {"small", "default", "large"}:
        raise PromptDocumentError(f"Shot {index + 1} has invalid camera amplitude")
    if speed not in {"slow", "default", "fast"}:
        raise PromptDocumentError(f"Shot {index + 1} has invalid camera speed")
    if "steps" in shot:
        raw_steps = shot.get("steps")
        if not isinstance(raw_steps, list):
            raise PromptDocumentError(f"Shot {index + 1} steps must be a list")
        steps = [_normalize_step(step, step_index) for step_index, step in enumerate(raw_steps)]
    else:
        # One-way import for documents saved before ordered shot steps became
        # canonical. Legacy fields are deliberately not retained in the
        # normalized document.
        steps = []
        legacy_action = _text(shot.get("action"))
        if legacy_action:
            steps.append({"id": "step-1", "type": "action", "text": legacy_action})
        legacy_dialogue = [
            _normalize_dialogue(event, event_index)
            for event_index, event in enumerate(shot.get("dialogue") or [])
        ]
        steps.extend({**event, "type": "dialogue"} for event in legacy_dialogue)
    used_step_ids = set()
    for step_index, step in enumerate(steps):
        candidate = step["id"]
        if candidate in used_step_ids:
            prefix = "dialogue" if step["type"] == "dialogue" else "step"
            suffix = step_index + 1
            candidate = f"{prefix}-{suffix}"
            while candidate in used_step_ids:
                suffix += 1
                candidate = f"{prefix}-{suffix}"
            step["id"] = candidate
        used_step_ids.add(candidate)
    return {
        "id": _identifier(shot.get("id"), "shot"),
        "start": max(0.0, _number(shot.get("start"), 0.0 if index == 0 else index * 2.0)),
        "transition": _text(shot.get("transition"), "the camera cuts to"),
        "composition": _text(shot.get("composition")),
        "subjects": _text(shot.get("subjects")),
        "environment": _text(shot.get("environment")),
        "lighting": _text(shot.get("lighting")),
        "camera": {
            "type": camera_type,
            "amplitude": amplitude,
            "speed": speed,
            "target": _text(camera.get("target")),
        },
        "steps": steps,
        "visible_text": [str(value).strip() for value in (shot.get("visible_text") or []) if str(value).strip()],
        "sounds": [str(value).strip() for value in (shot.get("sounds") or []) if str(value).strip()],
        "notes": _text(shot.get("notes")),
    }


def resolve_mode(document):
    explicit = _text(document.get("mode"), "auto").lower()
    if explicit not in MODES:
        raise PromptDocumentError(f"Unsupported MiniMax mode '{explicit}'")
    if explicit != "auto":
        return explicit
    references = document.get("references") or []
    roles = {role for reference in references for role in reference.get("roles", [])}
    if any(reference.get("kind") in {"video", "audio"} for reference in references):
        return "ref2va"
    if roles & REFERENCE_ROLES:
        return "ref2va"
    first = sum("first_frame" in reference.get("roles", []) for reference in references)
    last = sum("last_frame" in reference.get("roles", []) for reference in references)
    if first > 1 or last > 1:
        raise PromptDocumentError("Only one first-frame and one last-frame anchor are allowed")
    if first and last:
        return "fl2va"
    if first:
        return "i2va"
    if last:
        return "l2va"
    return "t2va"


def _normalize_subject_definition(item, index):
    if not isinstance(item, dict):
        raise PromptDocumentError(f"Subject definition {index + 1} must be an object")
    raw_label = _text(item.get("label"), f"Subject {index + 1}").strip("<>")
    match = re.fullmatch(r"(Subject|Picture|Video|Audio)\s*(\d+)", raw_label, re.IGNORECASE)
    if not match:
        raise PromptDocumentError(
            f"Subject definition {index + 1} must use a Subject, Picture, Video, or Audio label"
        )
    return {
        "label": f"{match.group(1).title()} {match.group(2)}",
        "text": _text(item.get("text")),
    }


def _normalize_retention(item, index):
    if not isinstance(item, dict):
        raise PromptDocumentError(f"Retention entry {index + 1} must be an object")
    relationship = normalize_retention_relationship(item.get("relationship"))
    if relationship not in RETENTION_RELATIONSHIPS:
        allowed = ", ".join(sorted(RETENTION_RELATIONSHIPS))
        raise PromptDocumentError(
            f"Retention entry {index + 1} has invalid relationship {relationship!r}; use one of: {allowed}"
        )
    return {
        "label": _canonical_reference_tokens(_text(item.get("label"))),
        "where": _canonical_reference_tokens(_text(item.get("where"))),
        "relationship": relationship,
        "detail": _text(item.get("detail")),
    }


def normalize_document(value):
    """Return a normalized copy of a versioned structured prompt document."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise PromptDocumentError(f"Video document contains invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PromptDocumentError("Video document must be an object")
    value = copy.deepcopy(value)
    version = int(_number(value.get("version"), DOCUMENT_VERSION))
    if version != DOCUMENT_VERSION:
        raise PromptDocumentError(f"Unsupported video document version {version}")
    duration = _number(value.get("duration_seconds"), 5.0)
    if duration <= 0 or duration > MAX_FRAME_COUNT / FPS:
        raise PromptDocumentError("Video duration must be between 0 and 150 seconds")
    references = [
        _normalize_reference(reference, index)
        for index, reference in enumerate(value.get("references") or [])
    ]
    label_counts = {"image": 0, "video": 0, "audio": 0}
    label_names = {"image": "Picture", "video": "Video", "audio": "Audio"}
    for reference in references:
        label_counts[reference["kind"]] += 1
        reference["label"] = f"<{label_names[reference['kind']]} {label_counts[reference['kind']]}>"
    shots = [
        _normalize_shot(shot, index)
        for index, shot in enumerate(value.get("shots") or [{}])
    ]
    if not shots:
        shots = [_normalize_shot({}, 0)]
    shots[0]["start"] = 0.0
    previous = -1.0
    final_time = effective_duration(duration)
    for index, shot in enumerate(shots):
        if shot["start"] <= previous:
            raise PromptDocumentError("Shot start times must be strictly increasing")
        if index and shot["start"] >= final_time:
            raise PromptDocumentError("Shot cut times must fall inside the effective duration")
        previous = shot["start"]
    document = {
        "version": DOCUMENT_VERSION,
        "mode": _text(value.get("mode"), "auto").lower(),
        "duration_seconds": duration,
        "width": _canvas_dimension(value.get("width"), 1344),
        "height": _canvas_dimension(value.get("height"), 768),
        "target_megapixels": min(
            MAX_CANVAS_MEGAPIXELS,
            max(
                MIN_CANVAS_MEGAPIXELS,
                _number(value.get("target_megapixels"), DEFAULT_CANVAS_MEGAPIXELS),
            ),
        ),
        "canvas_reference_id": _text(value.get("canvas_reference_id")),
        "ref_image_size": _text(value.get("ref_image_size"), "match").lower(),
        "main_description": _text(value.get("main_description")),
        "prompt_override": _text(value.get("prompt_override")),
        "style": _text(value.get("style"), "Live-action, cinematic"),
        "shots": shots,
        "references": references,
        "overall_soundscape": _text(value.get("overall_soundscape")),
        "non_diegetic_music": _text(value.get("non_diegetic_music"), "N/A"),
        "complete_silence": bool(value.get("complete_silence", False)),
        "task_types": [
            task for task in (_text(item).lower() for item in (value.get("task_types") or []))
            if task in TASK_TYPES
        ],
        "subject_definitions": [
            _normalize_subject_definition(item, index)
            for index, item in enumerate(value.get("subject_definitions") or [])
        ],
        "summary": _text(value.get("summary")),
        "retention_analysis": [
            _normalize_retention(item, index)
            for index, item in enumerate(value.get("retention_analysis") or [])
        ],
    }
    if document["ref_image_size"] not in {"match", "max"}:
        raise PromptDocumentError("Reference image size must be 'match' or 'max'")
    reference_ids = {reference["id"] for reference in references}
    if document["canvas_reference_id"] not in reference_ids:
        document["canvas_reference_id"] = ""
    elif document["canvas_reference_id"]:
        canvas_reference = next(
            reference for reference in references
            if reference["id"] == document["canvas_reference_id"]
        )
        if canvas_reference["source_width"] and canvas_reference["source_height"]:
            document["width"], document["height"] = adapt_canvas(
                canvas_reference["source_width"],
                canvas_reference["source_height"],
                document["target_megapixels"],
            )
    document["resolved_mode"] = resolve_mode(document)
    roles = {role for reference in references for role in reference["roles"]}
    first_count = sum("first_frame" in reference["roles"] for reference in references)
    last_count = sum("last_frame" in reference["roles"] for reference in references)
    if document["resolved_mode"] == "t2va" and references:
        raise PromptDocumentError("T2VA mode does not accept media references")
    if document["resolved_mode"] == "i2va" and (first_count != 1 or last_count):
        raise PromptDocumentError("I2VA mode requires exactly one first-frame image")
    if document["resolved_mode"] == "fl2va" and (first_count != 1 or last_count != 1):
        raise PromptDocumentError("FL2VA mode requires one first-frame and one last-frame image")
    if document["resolved_mode"] == "l2va" and (last_count != 1 or first_count):
        raise PromptDocumentError("L2VA mode requires exactly one last-frame image")
    if document["resolved_mode"] == "ref2va" and not references:
        raise PromptDocumentError("REF2VA mode requires at least one reference asset")
    if document["resolved_mode"] == "ref2va" and not document["task_types"]:
        derived = []
        if roles & ANCHOR_ROLES:
            derived.append("keyframe completion")
        if roles & {"subject", "scene", "style", "action", "pose", "camera", "storyboard"}:
            derived.append("reference generation")
        if "video_edit" in roles:
            derived.append("video editing")
        if "video_continue" in roles:
            derived.append("video continuation")
        if "audio_copy" in roles:
            derived.append("audio reuse")
        if "audio_reference" in roles:
            derived.append("audio reference")
        document["task_types"] = derived or ["reference generation"]
    return document


def default_document():
    return normalize_document({
        "version": DOCUMENT_VERSION,
        "mode": "auto",
        "duration_seconds": 5,
        "width": 1344,
        "height": 768,
        "main_description": "",
        "style": "Live-action, cinematic",
        "shots": [{
            "id": "shot-1",
            "start": 0,
            "composition": "A medium-wide shot establishes the scene.",
            "camera": {"type": "Static Shot", "amplitude": "default", "speed": "default"},
        }],
        "overall_soundscape": "",
        "non_diegetic_music": "N/A",
        "references": [],
    })
