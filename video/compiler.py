"""Deterministic MiniMax H3 prompt compiler."""

from __future__ import annotations

import re

from .contracts import PromptDocumentError, effective_duration, model_references, normalize_document


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


GENERIC_ONSCREEN_SPEAKER_RE = re.compile(
    r"^(?:the\s+)?(?:speaker|subject|character|person|woman|young\s+woman|girl|"
    r"man|young\s+man|boy|she|he|they)$",
    re.IGNORECASE,
)


def _first_frame_speaker_anchor(document):
    """Identify an unambiguous S1 speaker directly from a base-mode keyframe."""
    reference = next(
        (
            item for item in document.get("references") or []
            if item.get("kind") == "image"
            and "first_frame" in set(item.get("roles") or [])
        ),
        None,
    )
    candidates = (reference or {}).get("subject_candidates") or []
    if len(candidates) != 1:
        return ""
    name = str(candidates[0].get("name") or "").strip().rstrip(" .")
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.IGNORECASE)
    source = str(reference.get("label") or "<Picture 1>").strip()
    return f"The {name} shown in {source}" if name and source else ""


def _i2va_cut_subject_continuity(document, shot):
    """Re-anchor a continuing first-frame person after an I2VA hard cut.

    A bare phrase such as "the same girl" is not a useful visual constraint once
    a new shot changes the scene.  Cached grounding stays out of Shot 1 because
    its pixels are authoritative, but it can safely make a later reappearance
    explicit without inventing traits.
    """
    reference = next(
        (
            item for item in document.get("references") or []
            if item.get("kind") == "image"
            and "first_frame" in set(item.get("roles") or [])
        ),
        None,
    )
    candidates = (reference or {}).get("subject_candidates") or []
    if len(candidates) != 1:
        return ""
    candidate = candidates[0]
    name = str(candidate.get("name") or "").strip().rstrip(" .")
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.IGNORECASE)
    if not name:
        return ""

    subject_prose = " ".join(filter(None, (
        str(shot.get("subjects") or ""),
        str((shot.get("camera") or {}).get("target") or ""),
        *(str(step.get("text") or "") for step in shot.get("steps") or []),
    )))
    # Do not force the opening person into a cut that clearly introduces a
    # different person. Otherwise, ordinary nouns and pronouns are sufficient
    # evidence that the first-frame person continues into this shot.
    if re.search(
        r"\b(?:different|another|new)\s+(?:girl|woman|boy|man|person|character|subject)\b",
        subject_prose,
        re.IGNORECASE,
    ):
        return ""
    name_words = [word for word in re.findall(r"[A-Za-z]+", name) if len(word) > 2]
    continuation = re.search(
        r"\b(?:same|she|her|he|him|his|they|them|their|girl|woman|boy|man|person|character|subject)\b",
        subject_prose,
        re.IGNORECASE,
    ) or any(re.search(rf"\b{re.escape(word)}\b", subject_prose, re.IGNORECASE) for word in name_words)
    if not continuation:
        return ""

    source = str(reference.get("label") or "<Picture 1>").strip()
    attributes = candidate.get("grounded_attributes") or {}
    change_prefix = r"\b(?:changes?\s+into|now\s+wear(?:s|ing)|replaces?|different|new)\b.{0,80}"
    changed_fields = set()
    if re.search(change_prefix + r"\b(?:hair|hairstyle)\b", subject_prose, re.IGNORECASE):
        changed_fields.add("hair")
    if re.search(
        change_prefix + r"\b(?:clothes|clothing|outfit|wardrobe|dress|gown|robe|shirt|top|"
        r"blouse|jacket|coat|skirt|pants|trousers|shorts|suit|tuxedo)\b",
        subject_prose,
        re.IGNORECASE,
    ) or re.search(
        r"\b(?:change|changes|changing|changed|switch|switches|switching|switched|replace|"
        r"replaces|replacing|replaced)\b.{0,80}\b(?:clothes|clothing|outfit|wardrobe|dress|"
        r"gown|robe|shirt|top|blouse|jacket|coat|skirt|pants|trousers|shorts|suit|tuxedo)\b",
        subject_prose,
        re.IGNORECASE,
    ):
        changed_fields.add("clothing")
    if re.search(
        change_prefix + r"\b(?:shoes?|boots?|heels?|sandals?|sneakers?|footwear|barefoot)\b",
        subject_prose,
        re.IGNORECASE,
    ) or (
        "clothing" in changed_fields
        and re.search(r"\b(?:shoes?|boots?|heels?|sandals?|sneakers?|footwear|barefoot)\b", subject_prose, re.IGNORECASE)
    ):
        changed_fields.add("footwear")
    if re.search(
        change_prefix + r"\b(?:accessor(?:y|ies)|jewelry|bracelets?|necklaces?|earrings?|"
        r"watches?|cufflinks?)\b",
        subject_prose,
        re.IGNORECASE,
    ):
        changed_fields.add("accessories")
    if re.search(
        r"\b(?:entirely|completely|totally)\s+(?:different|new)\s+(?:appearance|look)\b",
        subject_prose,
        re.IGNORECASE,
    ):
        changed_fields.update({"hair", "face", "clothing", "footwear", "accessories", "body", "other"})
    attribute_labels = {
        "hair": "hair",
        "face": "facial appearance",
        "clothing": "clothing",
        "footwear": "footwear",
        "accessories": "accessories",
        "body": "body",
        "other": "other visible traits",
    }
    grounded = [
        f"{attribute_labels[field]} ({str(attributes.get(field) or '').strip().rstrip(' .')})"
        for field in ("hair", "face", "clothing", "footwear", "accessories", "body", "other")
        if str(attributes.get(field) or "").strip()
        and field not in changed_fields
    ]
    traits = f", including {', '.join(grounded)}" if grounded else ""
    wardrobe = (
        ""
        if changed_fields & {"clothing", "footwear", "accessories"}
        else (
            f" Preserve the exact wardrobe design, fit, sleeve and hem lengths, colors, materials, "
            f"accessories, and footwear visible in {source}; there is no wardrobe change."
        )
    )
    return (
        f"The {name} is unmistakably the same person shown in {source}; facial identity, "
        f"body proportions, and all unchanged appearance traits remain identical{traits}."
        f"{wardrobe}"
    )


def _i2va_grounded_sound(document, shot, sound):
    """Prevent an audible detail from silently redesigning anchored wardrobe."""
    reference = next(
        (
            item for item in document.get("references") or []
            if item.get("kind") == "image"
            and "first_frame" in set(item.get("roles") or [])
        ),
        None,
    )
    candidates = (reference or {}).get("subject_candidates") or []
    if len(candidates) != 1:
        return sound
    grounded_attributes = candidates[0].get("grounded_attributes") or {}
    grounded_clothing = str(grounded_attributes.get("clothing") or "").strip().rstrip(" .")
    grounded_footwear = str(grounded_attributes.get("footwear") or "").strip().rstrip(" .")
    grounded_accessories = str(grounded_attributes.get("accessories") or "").strip().rstrip(" .")
    if not any((grounded_clothing, grounded_footwear, grounded_accessories)):
        return sound
    subject_prose = " ".join(filter(None, (
        str(shot.get("subjects") or ""),
        *(str(step.get("text") or "") for step in shot.get("steps") or []),
    )))
    if re.search(
        r"\b(?:changes?\s+into|now\s+wear(?:s|ing)|replaces?|different|new)\b.{0,80}"
        r"\b(?:clothes|clothing|outfit|wardrobe|dress|gown|shirt|top|blouse|jacket|coat|"
        r"skirt|pants|trousers|shorts|robe|suit|tuxedo)\b",
        subject_prose,
        re.IGNORECASE,
    ) or re.search(
        r"\b(?:change|changes|changing|changed|switch|switches|switching|switched|replace|"
        r"replaces|replacing|replaced)\b.{0,80}\b(?:clothes|clothing|outfit|wardrobe|dress|"
        r"gown|shirt|top|blouse|jacket|coat|skirt|pants|trousers|shorts|robe|suit|tuxedo)\b",
        subject_prose,
        re.IGNORECASE,
    ):
        return sound

    garment = re.compile(
        r"\b(?:(?:soft|stiff|heavy|light|silk|silky|satin|satin-like|cotton|linen|"
        r"leather|wool|velvet|denim|flowing|loose|tight)\s+){0,3}"
        r"(?:robe|gown|dress|shirt|blouse|top|jacket|coat|skirt|pants|trousers|shorts)\b",
        re.IGNORECASE,
    )
    grounded_words = set(re.findall(r"[a-z]+", grounded_clothing.casefold()))

    def replace(match):
        matched_words = set(re.findall(r"[a-z]+", match.group(0).casefold()))
        garment_nouns = {
            "robe", "gown", "dress", "shirt", "blouse", "top", "jacket", "coat",
            "skirt", "pants", "trousers", "shorts",
        }
        return match.group(0) if matched_words & garment_nouns & grounded_words else grounded_clothing

    result = garment.sub(replace, str(sound or "")) if grounded_clothing else str(sound or "")

    footwear = re.compile(
        r"\b(?:(?:high|stiletto|leather|rubber|heavy|hard|soft|bare)\s+){0,2}"
        r"(?:heels?|shoes?|boots?|sandals?|sneakers?|footwear|feet)\b",
        re.IGNORECASE,
    )
    footwear_words = set(re.findall(r"[a-z]+", grounded_footwear.casefold()))
    if footwear.search(result):
        if "barefoot" in footwear_words or "bare" in footwear_words:
            # A grounded barefoot subject cannot produce heel/shoe/boot foley.
            if re.search(r"\b(?:heel|shoe|boot|sandal|sneaker)s?\b", result, re.IGNORECASE):
                return "Her bare footsteps land softly in exact sync with each step."
        elif grounded_footwear:
            result = footwear.sub(grounded_footwear, result)

    accessory = re.compile(
        r"\b(?:(?:metal|gold|silver|wooden|glass|delicate|heavy)\s+){0,2}"
        r"(?:bracelets?|bangles?|necklaces?|earrings?|watches?|chains?|pendants?)\b",
        re.IGNORECASE,
    )
    if accessory.search(result):
        if not grounded_accessories:
            return ""
        grounded_accessory_words = set(re.findall(r"[a-z]+", grounded_accessories.casefold()))

        def replace_accessory(match):
            words = set(re.findall(r"[a-z]+", match.group(0).casefold()))
            return match.group(0) if words & grounded_accessory_words else grounded_accessories

        result = accessory.sub(replace_accessory, result)
    return result


def _dialogue_text(event, *, speaker_anchor=""):
    speaker = event["speaker"]
    if (
        speaker_anchor
        and event.get("speaker_id") == "S1"
        and not event.get("voiceover")
        and not event.get("offscreen")
        and GENERIC_ONSCREEN_SPEAKER_RE.fullmatch(str(speaker or "").strip())
    ):
        speaker = speaker_anchor
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


def _step_text(step, *, speaker_anchor=""):
    if step.get("type") == "dialogue":
        return _dialogue_text(step, speaker_anchor=speaker_anchor) if step.get("text") else ""
    if step.get("type") == "action" and step.get("text"):
        return _sentence(_canonical_tokens(step["text"]))
    return ""


def _event_text(text, prefix=""):
    if not text:
        return ""
    content = str(text).strip()
    if content and content[-1] in ".!?":
        content = content[:-1]
    return _sentence(" ".join(part for part in (prefix, content) if part), capitalize=False)


def _legacy_auto_distributed_timing(events, shot_duration):
    """Recognize ranges synthesized by the first Shot Editor timeline release.

    Those equal contiguous slots were visual placeholders, but were persisted as
    if the user had authored them.  Treat the exact legacy pattern as ordinary
    chronological flow unless an item is explicitly marked as user-timed.
    """
    if len(events) < 2 or shot_duration <= 0:
        return False
    if any(event["item"].get("timing_explicit") for event in events):
        return False
    if any(
        event["item"].get("start") is None or event["item"].get("end") is None
        for event in events
    ):
        return False
    ordered = sorted(events, key=lambda event: (
        float(event["item"]["start"]), event["order"]
    ))
    first_start = float(ordered[0]["item"]["start"])
    coverage = float(ordered[-1]["item"]["end"]) - first_start
    if abs(first_start) > 0.02 or coverage < shot_duration * 0.8:
        return False
    slot = coverage / len(ordered)
    tolerance = max(0.002, min(0.02, slot / 20))
    return all(
        abs(float(event["item"]["start"]) - (first_start + index * slot)) <= tolerance
        and abs(float(event["item"]["end"]) - (first_start + (index + 1) * slot)) <= tolerance
        for index, event in enumerate(ordered)
    )


def _event_end_sentence(kind, end, shot_duration):
    if shot_duration <= 0:
        return ""
    noun = {"dialogue": "line", "sound": "sound", "action": "action"}.get(kind, "event")
    if end >= shot_duration - 1 / 24:
        return _sentence(f"The {noun} continues through the final frame")
    position = end / shot_duration
    phase = (
        "early in the shot"
        if position <= 0.4
        else "around the middle of the shot"
        if position <= 0.72
        else "late in the shot"
    )
    verb = "finishes" if kind in {"dialogue", "action"} else "ends"
    return _sentence(f"The {noun} {verb} {phase}")


def _sequence_event_texts(events, shot_duration):
    """Compile shot-local ranges into MiniMax's guide-native natural timeline.

    The official H3 grammar timestamps cuts, not individual events.  Numeric
    ranges therefore control ordering, overlap language, and relative phases
    without being emitted as unsupported START/END labels.
    """
    events = [event for event in events if event.get("text")]
    if not events:
        return []
    ignore_timing = _legacy_auto_distributed_timing(events, shot_duration)
    if ignore_timing or not any(event["item"].get("start") is not None for event in events):
        return [_event_text(event["text"]) for event in events]

    count = len(events)
    for event in events:
        item = event["item"]
        event["timed"] = item.get("start") is not None and item.get("end") is not None
        event["effective_start"] = (
            float(item["start"])
            if event["timed"]
            else (event["order"] + 0.5) / count * shot_duration
        )
    ordered = sorted(events, key=lambda event: (event["effective_start"], event["order"]))
    result = []
    prior_timed = []
    for event in ordered:
        if not event["timed"]:
            result.append(_event_text(event["text"]))
            continue
        item = event["item"]
        start = float(item["start"])
        end = float(item["end"])
        simultaneous = any(abs(float(previous["item"]["start"]) - start) <= 1 / 48 for previous in prior_timed)
        overlapping = any(float(previous["item"]["end"]) > start + 1 / 48 for previous in prior_timed)
        if simultaneous:
            prefix = "At the same time,"
        elif overlapping:
            prefix = "While the preceding event continues,"
        elif not prior_timed and start <= 1 / 48:
            prefix = "Immediately at the start of the shot,"
        elif prior_timed:
            prefix = "Then,"
        else:
            position = start / shot_duration if shot_duration > 0 else 0
            prefix = (
                "Early in the shot,"
                if position <= 0.4
                else "Around the middle of the shot,"
                if position <= 0.72
                else "Late in the shot,"
            )
        result.append(" ".join(filter(None, (
            _event_text(event["text"], prefix),
            _event_end_sentence(event["kind"], end, shot_duration),
        ))))
        prior_timed.append(event)
    return result


def _shot_text(
    shot,
    index,
    *,
    include_style="",
    first_frame_lock=False,
    first_frame_token="<Picture 1>",
    reference_scope="",
    first_frame_preserve_style=True,
    suppress_audio=False,
    speaker_anchor="",
    subject_continuity="",
    sound_texts=None,
    shot_duration=0.0,
):
    if index == 0:
        prefix = "[Shot 1]"
    else:
        transition = _canonical_tokens(shot.get("transition") or "the camera cuts to").rstrip(" .")
        if transition.casefold().endswith(" to"):
            transition += " a new view"
        prefix = f"[Shot {index + 1}] At {_cut_time(shot['start'])}, {_sentence(transition, capitalize=False)}"
    parts = []
    if first_frame_lock and index == 0:
        preserved = (
            "style, subjects, composition, scene, lighting, clothing, colors, key objects, and spatial relationships"
            if first_frame_preserve_style
            else "subjects, composition, scene, lighting, clothing, colors, key objects, and spatial relationships"
        )
        parts.append(_sentence(
            f"The {preserved} established by {first_frame_token} remain fully preserved"
        ))
        if reference_scope:
            parts.append(_sentence(reference_scope))
    if include_style:
        parts.append(_sentence(_canonical_tokens(include_style)))
    if subject_continuity:
        parts.append(_sentence(_canonical_tokens(subject_continuity)))
    visual_fields = () if first_frame_lock and index == 0 else (
        "composition", "subjects", "environment", "lighting"
    )
    for field in visual_fields:
        if shot.get(field):
            parts.append(_sentence(_canonical_tokens(shot[field])))
    camera = _camera_sentence(shot.get("camera") or {})
    if camera:
        parts.append(camera)
    timed_events = []
    event_order = 0
    for step in shot.get("steps") or []:
        if suppress_audio and step.get("type") == "dialogue":
            continue
        text = _step_text(step, speaker_anchor=speaker_anchor)
        timed_events.append({
            "item": step, "order": event_order, "text": text, "kind": step.get("type") or "event",
        })
        event_order += 1
    for visible in shot.get("visible_text") or []:
        escaped = visible.replace('"', '\\"')
        parts.append(_sentence(f'A visible text element reads "{escaped}"'))
    if not suppress_audio:
        sounds = shot.get("sounds") or [] if sound_texts is None else sound_texts
        cues = shot.get("sound_cues") or []
        for sound_index, sound in enumerate(sounds):
            cue = cues[sound_index] if sound_index < len(cues) else {}
            timed_events.append({
                "item": cue,
                "order": event_order,
                "text": _sentence(_canonical_tokens(sound)),
                "kind": "sound",
            })
            event_order += 1
    parts.extend(_sequence_event_texts(timed_events, shot_duration))
    body = " ".join(part for part in parts if part)
    return f"{prefix} {body}".strip()


def _base_alignment(mode, final_shot, duration):
    if mode == "t2va":
        return ""
    if mode == "i2va":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) "
            "aligns with the 0.00-second mark of the target video; <Picture 2> "
            f"(from [Shot {final_shot}]) aligns with the {duration:.2f}-second mark of the target video."
        )
    return (
        "How the reference pictures align with the target video — <Picture 1> "
        f"(from [Shot {final_shot}]) aligns with the {duration:.2f}-second mark of the target video."
    )


def _document_shot_duration(document, index):
    shot = document["shots"][index]
    end = (
        document["shots"][index + 1]["start"]
        if index + 1 < len(document["shots"])
        else effective_duration(document)
    )
    return max(1 / 24, float(end) - float(shot["start"]))


def _soundscape_value(document):
    if document["complete_silence"]:
        return "N/A"
    value = _canonical_tokens(document.get("overall_soundscape") or "").strip()
    if value:
        return value
    has_synchronized_sound = any(shot.get("sounds") for shot in document.get("shots") or [])
    if has_synchronized_sound:
        return (
            "The soundtrack contains only the dialogue and synchronized diegetic events described "
            "in the shots, with no additional ambient layer specified."
        )
    return "No additional ambience, physical action sounds, or non-verbal human sounds are specified."


def _compile_base(document):
    mode = document["resolved_mode"]
    duration = effective_duration(document)
    speaker_anchor = _first_frame_speaker_anchor(document)
    shots = " ".join(
        _shot_text(
            shot,
            index,
            include_style=document["style"] if index == 0 and mode not in {"i2va", "fl2va"} else "",
            first_frame_lock=mode in {"i2va", "fl2va"},
            suppress_audio=document["complete_silence"],
            speaker_anchor=speaker_anchor,
            subject_continuity=(
                _i2va_cut_subject_continuity(document, shot)
                if mode == "i2va" and index > 0
                else ""
            ),
            sound_texts=(
                [_i2va_grounded_sound(document, shot, sound) for sound in shot.get("sounds") or []]
                if mode == "i2va"
                else None
            ),
            shot_duration=_document_shot_duration(document, index),
        )
        for index, shot in enumerate(document["shots"])
    )
    fields = [
        f"integrated_multimodal_description: {shots}",
        f"overall_soundscape: {_soundscape_value(document)}",
        f"non_diegetic_music: {_canonical_tokens('N/A' if document['complete_silence'] else (document['non_diegetic_music'] or 'N/A'))}",
    ]
    alignment = _base_alignment(mode, len(document["shots"]), duration)
    return f"{alignment}\n\n" + "\n\n".join(fields) if alignment else "\n\n".join(fields)


def _definition_lines(document):
    lines = []
    for item in document["subject_definitions"]:
        label = item["label"].strip("<>")
        lines.append(_canonical_tokens(f"<{label}> {item['text']}".rstrip()))
    return lines


def _retention_lines(document, replacement_tokens=()):
    lines = []
    replacement_tokens = {token.casefold() for token in replacement_tokens}
    for item in document["retention_analysis"]:
        where = f" ({item['where']})" if item["where"] else ""
        relationship = item["relationship"]
        detail_text = item["detail"]
        if _canonical_tokens(item["label"]).casefold() in replacement_tokens:
            relationship = "attribute_transfer"
            detail_text = (
                "Identity and appearance transfer continuously from the first frame through the final frame; "
                "the subject reference's static pose, held objects, and action do not transfer."
            )
        detail = f" - {detail_text}" if detail_text else ""
        lines.append(_canonical_tokens(f"{item['label']}{where}: {relationship}{detail}"))
    return lines


def _first_frame_reference_scope(document):
    """State precedence when subject-only pictures accompany a first-frame anchor."""
    first_frame = next(
        (
            reference for reference in document.get("references") or []
            if reference.get("kind") == "image"
            and "first_frame" in set(reference.get("roles") or [])
        ),
        None,
    )
    if not first_frame:
        return "", ""

    definitions = document.get("subject_definitions") or []
    subject_sources = []
    for reference in document.get("references") or []:
        roles = set(reference.get("roles") or [])
        if (
            reference is first_frame
            or reference.get("kind") != "image"
            or not roles
            or not roles <= {"subject"}
        ):
            continue
        source_token = reference.get("label") or ""
        subjects = [
            f"<{definition['label'].strip('<>')}>"
            for definition in definitions
            if definition.get("label", "").casefold().startswith("subject")
            and source_token.casefold() in definition.get("text", "").casefold()
        ]
        if subjects:
            subject_sources.append((source_token, subjects))

    if not subject_sources:
        return first_frame.get("label") or "<Picture 1>", ""

    scopes = []
    for source_token, subjects in subject_sources:
        subject_text = " and ".join(subjects)
        scopes.append(f"{source_token} supplies only {subject_text}'s identity and appearance")
    return first_frame.get("label") or "<Picture 1>", (
        f"{first_frame.get('label') or '<Picture 1>'} remains the sole source for this shot's background, "
        "environment, lighting, composition, camera framing, and spatial relationships; "
        + "; ".join(scopes)
        + ", with no source-picture scene or framing transferred"
    )


def _video_edit_replacement_scope(document):
    video_tokens = [
        reference.get("label") or ""
        for reference in document.get("references") or []
        if reference.get("kind") == "video"
        and "video_edit" in set(reference.get("roles") or [])
    ]
    if not video_tokens:
        return "", [], ""
    subject_sources = {
        (reference.get("label") or "").casefold()
        for reference in document.get("references") or []
        if reference.get("kind") == "image"
        and set(reference.get("roles") or []) == {"subject"}
    }
    subject_tokens = [
        f"<{definition['label'].strip('<>')}>"
        for definition in document.get("subject_definitions") or []
        if str(definition.get("label") or "").casefold().startswith("subject")
        and any(
            source and source in str(definition.get("text") or "").casefold()
            for source in subject_sources
        )
    ]
    semantic_parts = [document.get("main_description"), document.get("summary")]
    for shot in document.get("shots") or []:
        semantic_parts.extend(shot.get(name) for name in ("composition", "subjects", "notes"))
        semantic_parts.extend(
            step.get("text")
            for step in shot.get("steps") or []
            if isinstance(step, dict) and step.get("type") == "action"
        )
    semantic_text = "\n".join(str(value or "") for value in semantic_parts)
    replacement = re.search(
        r"\b(?:replac(?:e|es|ed|ing|ement)|swap(?:s|ped|ping)?|substitut(?:e|es|ed|ing|ion))\b",
        semantic_text,
        re.IGNORECASE,
    )
    if not replacement or not subject_tokens:
        return video_tokens[0], [], semantic_text
    return video_tokens[0], list(dict.fromkeys(subject_tokens)), semantic_text


def _compile_reference(document):
    definitions = "\n".join(_definition_lines(document))
    task_types = document["task_types"] or ["reference generation"]
    video_edit_token, replacement_tokens, replacement_semantics = _video_edit_replacement_scope(document)
    summary_text = document["summary"]
    if video_edit_token and not re.search(
        r"\btarget video is an edited version of\b",
        str(summary_text or ""),
        re.IGNORECASE,
    ):
        summary_text = " ".join(filter(None, (
            f"The target video is an edited version of {video_edit_token}.",
            str(summary_text or "").strip(),
        )))
    summary = _canonical_tokens(f"[{' + '.join(task_types)}] {summary_text}".rstrip())
    retention = "\n".join(_retention_lines(document, replacement_tokens))
    first_frame_token, reference_scope = _first_frame_reference_scope(document)
    external_style_tokens = []
    for reference in document.get("references") or []:
        if "style" not in set(reference.get("roles") or []):
            continue
        source_token = str(reference.get("label") or "").strip()
        definition = next(
            (
                item for item in document.get("subject_definitions") or []
                if source_token.casefold() in str(item.get("text") or "").casefold()
            ),
            None,
        )
        if definition:
            external_style_tokens.append(f"<{str(definition.get('label') or '').strip('<>')}>")
    external_style_tokens = list(dict.fromkeys(token for token in external_style_tokens if token != "<>"))
    if external_style_tokens:
        style_scope = "The visual treatment throughout the target video follows " + " and ".join(
            external_style_tokens
        ) + "."
        reference_scope = " ".join(part for part in (reference_scope, style_scope) if part)
    shot_parts = []
    for index, original_shot in enumerate(document["shots"]):
        shot = original_shot
        subject_continuity = ""
        if replacement_tokens:
            shot_blob = " ".join(str(value or "") for value in (
                original_shot.get("composition"), original_shot.get("subjects"),
                original_shot.get("notes"),
                *(step.get("text") for step in original_shot.get("steps") or [] if isinstance(step, dict)),
            ))
            active_tokens = [
                token for token in replacement_tokens
                if token.casefold() in shot_blob.casefold()
            ] or (replacement_tokens if len(document["shots"]) == 1 else [])
            if active_tokens:
                subjects = " and ".join(active_tokens)
                verb = "replaces" if len(active_tokens) == 1 else "replace"
                shot = dict(original_shot)
                shot["subjects"] = (
                    f"From the first visible frame through the final frame, {subjects} {verb} only the "
                    f"corresponding source-video subject's identity and appearance in {video_edit_token}; "
                    "source-video screen position, scale, occlusion, props, and object contact remain unchanged."
                )
                subject_continuity = (
                    f"Throughout the complete shot from its first frame onward, {video_edit_token} supplies every "
                    f"body motion, pose change, gesture, locomotion path, and timing for {subjects}; the subject "
                    "reference supplies no action or static pose."
                )
                camera = original_shot.get("camera") or {}
                if (
                    camera.get("type") == "Static Shot"
                    and camera.get("amplitude") in {None, "", "default"}
                    and camera.get("speed") in {None, "", "default"}
                    and not camera.get("target")
                    and re.search(
                        r"\bcamera\b.{0,80}\b(?:preserv|unchang|same|identical|exact)",
                        replacement_semantics,
                        re.IGNORECASE,
                    )
                ):
                    shot["camera"] = {}
        shot_parts.append(_shot_text(
            shot,
            index,
            first_frame_lock=bool(index == 0 and any(
                "first_frame" in reference.get("roles", [])
                for reference in document.get("references") or []
            )),
            first_frame_token=first_frame_token or "<Picture 1>",
            reference_scope=reference_scope if index == 0 else "",
            first_frame_preserve_style=not external_style_tokens,
            suppress_audio=document["complete_silence"],
            subject_continuity=subject_continuity,
            shot_duration=_document_shot_duration(document, index),
        ))
    shots = " ".join(shot_parts)
    has_first_frame = any(
        "first_frame" in reference.get("roles", [])
        for reference in document.get("references") or []
    )
    detailed = " ".join(part for part in (
        "" if has_first_frame else _sentence(_canonical_tokens(document["style"])),
        shots,
    ) if part)
    return "\n\n".join([
        f"subject_definitions:\n{definitions}".rstrip(),
        f"summary:\n{summary}".rstrip(),
        f"retention_analysis:\n{retention}".rstrip(),
        f"detailed_description:\n{detailed}".rstrip(),
        f"overall_soundscape:\n{_soundscape_value(document)}".rstrip(),
        f"non_diegetic_music:\n{_canonical_tokens('N/A' if document['complete_silence'] else (document['non_diegetic_music'] or 'N/A'))}",
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
        role for reference in model_references(document) for role in reference.get("roles", [])
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
    for reference in model_references(document):
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
    has_first_frame = any(
        "first_frame" in reference.get("roles", [])
        for reference in document.get("references") or []
    )
    has_external_style = any(
        "style" in set(reference.get("roles") or [])
        and "first_frame" not in set(reference.get("roles") or [])
        for reference in document.get("references") or []
    )
    detailed_parts = [document["style"]] if (not has_first_frame or has_external_style) else []
    detailed_parts.extend(
        reference.get("label", "")
        for reference in document.get("references") or []
        if "first_frame" in reference.get("roles", [])
    )
    for shot in document["shots"]:
        detailed_parts.extend(
            shot.get(name, "")
            for name in ("composition", "subjects", "environment", "lighting", "notes")
        )
        if not document["complete_silence"]:
            detailed_parts.extend(shot.get("sounds") or [])
        detailed_parts.append((shot.get("camera") or {}).get("target", ""))
        for step in shot.get("steps") or []:
            if step.get("type") != "dialogue" or not document["complete_silence"]:
                detailed_parts.append(step.get("text", ""))
            if step.get("type") == "dialogue" and not document["complete_silence"]:
                detailed_parts.append(step.get("speaker", ""))
    if not document["complete_silence"]:
        detailed_parts.extend((document["overall_soundscape"], document["non_diegetic_music"]))
    detailed = _canonical_tokens(" ".join(str(value or "") for value in detailed_parts)).casefold()
    allowed_tokens = {
        _canonical_tokens(reference.get("label", "")).casefold()
        for reference in document.get("references") or []
    } | {token.casefold() for token in definition_tokens}
    all_prompt_content = _canonical_tokens(" ".join((
        document.get("summary", ""),
        *definition_blob_parts,
        *(item.get("where", "") for item in retention),
        *(item.get("detail", "") for item in retention),
        *detailed_parts,
    )))
    used_tokens = {
        f"<{kind.title()} {number}>".casefold()
        for kind, number in re.findall(
            r"<\s*(Picture|Video|Audio|Subject)\s+(\d+)\s*>",
            all_prompt_content,
            re.IGNORECASE,
        )
    }
    unknown_tokens = sorted(used_tokens - allowed_tokens)
    if unknown_tokens:
        issues.append("undefined reference labels in prompt content: " + ", ".join(unknown_tokens))
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
    compiled = _compile_base(document)
    allowed = {
        _canonical_tokens(reference.get("label", "")).casefold()
        for reference in document.get("references") or []
    }
    used = {
        _canonical_tokens(token)
        for token in re.findall(
            r"<\s*(?:Picture|Video|Audio|Subject)\s+\d+\s*>",
            compiled,
            re.IGNORECASE,
        )
    }
    invented = sorted(token for token in used if token.casefold() not in allowed)
    if invented:
        raise PromptDocumentError(
            "Reference labels require REF2VA source definitions: " + ", ".join(invented)
        )
    return compiled
