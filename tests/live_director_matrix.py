"""Manual live-LLM integration matrix for PromptStudio Video Director.

Run from the plugin root with ComfyUI's Python. This file is deliberately not
named test_*.py because it requires a running local KoboldCpp instance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COMFY_ROOT = Path(__file__).resolve().parents[3]
for import_root in (PLUGIN_ROOT, COMFY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from video.director import director_chat, preview_changeset  # noqa: E402


SUBJECT_IMAGE = "ComfyUI_00180_.webp"
CHAIR_IMAGE = "ComfyUI_00038_.webp"
DRAWING_IMAGE = "example.png"
FIRST_FRAME_IMAGE = "ComfyUI_00061_.webp"
LAST_FRAME_IMAGE = "ComfyUI_00066_.webp"


def reference(index, path, role):
    return {
        "id": f"reference-{index}",
        "kind": "image",
        "path": path,
        "name": Path(path).stem,
        "roles": [role],
    }


def attachment(index, path, usage):
    return {
        "id": f"attachment-{index}",
        "path": path,
        "name": Path(path).stem,
        "usage": usage,
        "reference_id": f"reference-{index}",
    }


def shot(shot_id, start, marker=""):
    return {
        "id": shot_id,
        "start": start,
        "transition": "the camera cuts to",
        "composition": f"{marker} medium-wide composition".strip(),
        "subjects": f"{marker} subject".strip(),
        "environment": f"{marker} environment".strip(),
        "lighting": f"{marker} lighting".strip(),
        "steps": [{"type": "action", "text": f"{marker} action".strip()}],
        "camera": {
            "type": "Static Shot",
            "amplitude": "default",
            "speed": "default",
            "target": f"{marker} subject".strip(),
        },
        "visible_text": [],
        "sounds": [f"{marker} room tone".strip()],
        "notes": "",
    }


def document(mode="auto", references=None, shots=None, duration=9):
    return {
        "version": 1,
        "mode": mode,
        "duration_seconds": duration,
        "width": 1344,
        "height": 768,
        "ref_image_size": "match",
        "main_description": "OBSOLETE STORY: a clerk silently sorts envelopes.",
        "style": "OBSOLETE STYLE: flat office training video.",
        "shots": shots or [shot("shot-1", 0, "OBSOLETE"), shot("shot-2", 4, "OBSOLETE")],
        "references": references or [],
        "overall_soundscape": "OBSOLETE SOUND: fluorescent hum.",
        "non_diegetic_music": "OBSOLETE MUSIC: metronome clicks.",
        "complete_silence": False,
        "task_types": [],
        "subject_definitions": [],
        "summary": "",
        "retention_analysis": [],
    }


def request(document_value, prompt, attachments=None, scope="project", selected_shot_id=""):
    return {
        "llm_provider": "koboldcpp",
        "kobold_url": "http://localhost:5001",
        "thinking_mode": "High",
        "max_response_tokens": 0,
        "context_budget_chars": 16000,
        "request_timeout": 600,
        "temperature": 0.15,
        "top_p": 0.9,
        "top_k": 80,
        "min_p": 0.02,
        "rep_pen": 1.05,
        "rep_pen_range": 512,
        "sampler_seed": 42,
        "scope": scope,
        "selected_shot_id": selected_shot_id,
        "project_name": "Director live matrix",
        "brief": prompt,
        "document": document_value,
        "attachments": attachments or [],
        "messages": [{"role": "user", "content": prompt}],
        "require_proposal": True,
    }


def cases():
    i2va_refs = [reference(1, FIRST_FRAME_IMAGE, "first_frame")]
    fl2va_refs = [
        reference(1, FIRST_FRAME_IMAGE, "first_frame"),
        reference(2, LAST_FRAME_IMAGE, "last_frame"),
    ]
    l2va_refs = [reference(1, LAST_FRAME_IMAGE, "last_frame")]
    ref2va_refs = [
        reference(1, SUBJECT_IMAGE, "subject"),
        reference(2, CHAIR_IMAGE, "scene"),
        reference(3, DRAWING_IMAGE, "style"),
    ]
    return {
        "t2va_rewrite": {
            "document": document(mode="t2va"),
            "prompt": (
                "Completely rewrite the entire existing production. Replace all obsolete story, style, shot, sound, "
                "and music content with exactly three resulting shots at 0, 3, and 6 seconds. Create a cinematic "
                "nighttime bakery sequence: the baker opens the shutters, discovers a small package, then smiles at "
                "the handwritten note inside. Populate the full prompt with concrete composition, subjects, "
                "environment, lighting, action, camera, synchronized sounds, soundscape, and subtle piano music."
            ),
            "mode": "t2va",
            "shots": 3,
            "starts": [0, 3, 6],
            "forbidden": "OBSOLETE",
        },
        "i2va": {
            "document": document(mode="i2va", references=i2va_refs, shots=[shot("shot-1", 0)]),
            "attachments": [attachment(1, FIRST_FRAME_IMAGE, "first_frame")],
            "prompt": (
                "Rewrite the complete production as one continuous shot beginning exactly from the attached first "
                "frame. The woman turns from the doorway, opens a red umbrella, and steps into gentle rain. Preserve "
                "her appearance, white dress, doorway geometry, and initial composition. Fill every prompt field and "
                "include synchronized rain, fabric, umbrella, and footsteps."
            ),
            "mode": "i2va",
            "shots": 1,
            "starts": [0],
            "prompt_prefix": "For the target video, at 0.00 seconds",
        },
        "fl2va": {
            "document": document(mode="fl2va", references=fl2va_refs, shots=[shot("shot-1", 0)]),
            "attachments": [
                attachment(1, FIRST_FRAME_IMAGE, "first_frame"),
                attachment(2, LAST_FRAME_IMAGE, "last_frame"),
            ],
            "prompt": (
                "Rewrite the complete production as one continuous shot that starts from Picture 1 and lands exactly "
                "on Picture 2 at the end. Preserve the same blonde woman, white dress, doorway, lighting direction, "
                "and room geometry while she shifts from the closer pose into the wider final pose. Fill every prompt "
                "field with a concrete continuous motion path and synchronized fabric and room sounds."
            ),
            "mode": "fl2va",
            "shots": 1,
            "starts": [0],
            "prompt_prefix": "How the reference pictures align",
        },
        "l2va": {
            "document": document(mode="l2va", references=l2va_refs, shots=[shot("shot-1", 0)]),
            "attachments": [attachment(1, LAST_FRAME_IMAGE, "last_frame")],
            "prompt": (
                "Rewrite the complete production as one continuous shot that plausibly begins with the woman entering "
                "from the left and converges exactly on the attached final frame. Preserve her white dress, blonde hair, "
                "doorway, and final pose. Fill every prompt field with the visible path, camera motion, lighting, and "
                "synchronized footsteps and fabric sound."
            ),
            "mode": "l2va",
            "shots": 1,
            "starts": [0],
            "prompt_prefix": "How the reference pictures align",
        },
        "ref2va_multi": {
            "document": document(mode="ref2va", references=ref2va_refs),
            "attachments": [
                attachment(1, SUBJECT_IMAGE, "subject"),
                attachment(2, CHAIR_IMAGE, "scene"),
                attachment(3, DRAWING_IMAGE, "style"),
            ],
            "prompt": (
                "Completely rewrite the full production into exactly three resulting shots at 0, 3, and 6 seconds. "
                "Use Picture 1 only for the platinum-blonde woman's identity, pink outfit, and black boots; Picture 2 "
                "only for the burgundy wingback chair and sparse pale studio setting; and Picture 3 only for the flat "
                "childlike drawing style, simple shapes, blue sky, green hill, and bright palette. Across all three "
                "shots she approaches the chair, performs a joyful dance around it, then sits and raises both hands. "
                "Keep every reference assignment distinct and repeat each applicable reference label and its concrete "
                "traits in every affected shot. Populate all six REF2VA sections, synchronized sounds, soundscape, and music."
            ),
            "mode": "ref2va",
            "shots": 3,
            "starts": [0, 3, 6],
            "reference_tokens": ["<Picture 1>", "<Picture 2>", "<Picture 3>"],
            "shot_reference_tokens": ["<Subject 1>", "<Subject 2>", "<Subject 3>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "observation_terms": [
                ["blonde", "pink", "boots"],
                ["burgundy", "red", "chair"],
                ["drawing", "cartoon", "yellow", "blue", "green"],
            ],
            "observation_forbidden_terms": [
                [],
                ["dark brown hair", "white short-sleeved", "black skirt"],
                [],
            ],
            "definition_forbidden_terms": {
                "<Picture 2>": ["dark brown hair", "white short-sleeved", "black skirt"],
            },
            "forbidden": "OBSOLETE",
        },
        "ref2va_single_natural": {
            "document": document(
                mode="ref2va",
                references=[reference(1, SUBJECT_IMAGE, "subject")],
                shots=[shot("shot-1", 0)],
            ),
            "attachments": [attachment(1, SUBJECT_IMAGE, "subject")],
            "prompt": "Create a single continuous shot of the girl dancing in the rain.",
            "mode": "ref2va",
            "shots": 1,
            "starts": [0],
            "reference_tokens": ["<Picture 1>"],
            "shot_reference_tokens": ["<Subject 1>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "observation_terms": [["blonde", "pink", "boots"]],
            "observation_forbidden_terms": [[]],
        },
        "ref2va_single_named": {
            "document": document(
                mode="ref2va",
                references=[reference(1, SUBJECT_IMAGE, "subject")],
                shots=[shot("shot-1", 0)],
            ),
            "attachments": [attachment(1, SUBJECT_IMAGE, "subject")],
            "prompt": "Create a single continuous shot of the girl from <Picture 1> dancing joyfully in the rain.",
            "mode": "ref2va",
            "shots": 1,
            "starts": [0],
            "reference_tokens": ["<Picture 1>"],
            "shot_reference_tokens": ["<Subject 1>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "observation_terms": [["blonde", "pink", "boots"]],
            "observation_forbidden_terms": [[]],
        },
        "ref2va_first_frame_subject_steps": {
            "document": document(
                mode="auto",
                references=[
                    reference(1, FIRST_FRAME_IMAGE, "first_frame"),
                    reference(2, SUBJECT_IMAGE, "subject"),
                ],
                shots=[shot("shot-1", 0)],
                duration=5,
            ),
            "attachments": [
                attachment(1, FIRST_FRAME_IMAGE, "first_frame"),
                attachment(2, SUBJECT_IMAGE, "subject"),
            ],
            "prompt": (
                'The girl from Picture 1 says "Come here dear". Then the girl from Picture 2 enters '
                "from frame right and smiles. Create a complete, directly usable MiniMax H3 proposal "
                "for this five-second video."
            ),
            "mode": "ref2va",
            "shots": 1,
            "starts": [0],
            "reference_tokens": ["<Picture 1>", "<Picture 2>"],
            "shot_reference_tokens": ["<Subject 1>", "<Subject 2>"],
            "expected_step_types": ["dialogue", "action"],
            "first_frame_lock": True,
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "observation_terms": [
                ["woman", "dress", "door"],
                ["blonde", "pink", "boots"],
            ],
            "observation_forbidden_terms": [[], []],
        },
        "selected_shot_t2va": {
            "document": document(mode="t2va"),
            "scope": "shot",
            "selected_shot_id": "shot-2",
            "prompt": (
                "Completely rewrite the selected shot with a close composition of the baker opening a mysterious "
                "package beside a rain-streaked window. Replace its obsolete action, composition, subjects, "
                "environment, lighting, camera, and sounds with concrete synchronized details."
            ),
            "mode": "t2va",
            "shots": 2,
            "starts": [0, 4],
            "selected_required_terms": ["package", "rain"],
        },
    }


def validate(name, case, result, preview):
    errors = []
    if result.get("proposal_error"):
        errors.append(f"proposal_error: {result['proposal_error']}")
    if not result.get("proposal"):
        errors.append("no proposal returned")
        return errors
    result_document = preview["document"]
    compiled = preview["compiled_prompt"]
    if result_document.get("resolved_mode") != case["mode"]:
        errors.append(f"resolved mode {result_document.get('resolved_mode')} != {case['mode']}")
    if len(result_document.get("shots") or []) != case["shots"]:
        errors.append(f"shot count {len(result_document.get('shots') or [])} != {case['shots']}")
    starts = [float(item.get("start", 0)) for item in result_document.get("shots") or []]
    if "starts" in case and starts != [float(value) for value in case["starts"]]:
        errors.append(f"shot starts {starts} != {case['starts']}")
    has_first_frame = any(
        "first_frame" in reference_value.get("roles", [])
        for reference_value in case["document"].get("references") or []
    )
    for shot_index, shot_value in enumerate(result_document.get("shots") or []):
        required_fields = () if has_first_frame and shot_index == 0 else (
            "composition", "subjects", "environment", "lighting"
        )
        missing_fields = [
            field for field in required_fields
            if not str(shot_value.get(field) or "").strip()
        ]
        if not any(
            step.get("type") == "action" and str(step.get("text") or "").strip()
            for step in shot_value.get("steps") or []
        ):
            missing_fields.append("action step")
        if missing_fields:
            errors.append(f"{shot_value.get('id')} has empty fields {missing_fields}")
        if "sound" in case["prompt"].casefold() and not shot_value.get("sounds"):
            errors.append(f"{shot_value.get('id')} has no synchronized sounds")
    expected_steps = case.get("expected_step_types")
    if expected_steps:
        actual_steps = [
            step.get("type") for step in result_document["shots"][0].get("steps") or []
        ]
        if actual_steps != expected_steps:
            errors.append(f"step order {actual_steps} != {expected_steps}")
    if case.get("first_frame_lock"):
        first = result_document["shots"][0]
        leaked = [
            field for field in ("composition", "subjects", "environment", "lighting")
            if first.get(field) != case["document"]["shots"][0].get(field)
        ]
        if leaked:
            errors.append(f"first-frame-owned fields changed: {leaked}")
    if len(compiled.split()) > 650:
        errors.append(f"compiled prompt is unexpectedly verbose: {len(compiled.split())} words")
    if case.get("prompt_prefix") and not compiled.startswith(case["prompt_prefix"]):
        errors.append(f"compiled prompt does not start with {case['prompt_prefix']!r}")
    forbidden = case.get("forbidden")
    if forbidden and forbidden.casefold() in json.dumps(result_document).casefold():
        errors.append(f"rewritten document still contains {forbidden!r}")
    for token in case.get("reference_tokens") or []:
        definitions = " ".join(
            f"<{item.get('label', '').strip('<>')}> {item.get('text', '')}"
            for item in result_document.get("subject_definitions") or []
        )
        if token.casefold() not in definitions.casefold():
            errors.append(f"{token} is absent from subject definitions")
        if token.casefold() not in compiled.casefold():
            errors.append(f"{token} is absent from compiled prompt")
    for token in case.get("shot_reference_tokens") or []:
        for shot_value in result_document.get("shots") or []:
            if token.casefold() not in json.dumps(shot_value, ensure_ascii=False).casefold():
                errors.append(f"{token} is absent from {shot_value.get('id')}")
    section_positions = [compiled.find(section) for section in case.get("ordered_sections") or []]
    if section_positions and (
        any(position < 0 for position in section_positions)
        or section_positions != sorted(section_positions)
    ):
        errors.append(f"compiled section order is invalid: {section_positions}")
    selected_terms = case.get("selected_required_terms") or []
    if selected_terms:
        selected = next(
            (item for item in result_document.get("shots") or [] if item.get("id") == case["selected_shot_id"]),
            {},
        )
        selected_text = json.dumps(selected, ensure_ascii=False).casefold()
        missing_terms = [term for term in selected_terms if term.casefold() not in selected_text]
        if missing_terms:
            errors.append(f"selected shot missed requested terms {missing_terms}")
    observations = result.get("vision_observations") or []
    expected_terms = case.get("observation_terms") or []
    if expected_terms and len(observations) != len(expected_terms):
        errors.append(f"vision observation count {len(observations)} != {len(expected_terms)}")
    for index, terms in enumerate(expected_terms):
        text = observations[index].get("observations", "").casefold() if index < len(observations) else ""
        if not any(term in text for term in terms):
            errors.append(f"image {index + 1} observations missed expected terms {terms}: {text[:240]}")
        forbidden_terms = (case.get("observation_forbidden_terms") or [])[index]
        leaked = [term for term in forbidden_terms if term in text]
        if leaked:
            errors.append(f"image {index + 1} observations leaked role-irrelevant terms {leaked}")
    definitions = "\n".join(
        f"<{item.get('label', '').strip('<>')}> {item.get('text', '')}"
        for item in result_document.get("subject_definitions") or []
    ).casefold()
    for source_token, forbidden_terms in (case.get("definition_forbidden_terms") or {}).items():
        source_definition = next(
            (
                line for line in definitions.splitlines()
                if source_token.casefold() in line
            ),
            "",
        )
        leaked = [term for term in forbidden_terms if term in source_definition]
        if leaked:
            errors.append(f"{source_token} definition leaked role-irrelevant terms {leaked}")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="selected")
    parser.add_argument("--show-prompt", action="store_true")
    args = parser.parse_args()
    matrix = cases()
    selected = args.selected or list(matrix)
    unknown = sorted(set(selected) - set(matrix))
    if unknown:
        raise SystemExit(f"Unknown case(s): {', '.join(unknown)}")

    failures = 0
    for name in selected:
        case = matrix[name]
        started = time.monotonic()
        print(f"\n=== {name} ===", flush=True)
        try:
            data = request(
                case["document"], case["prompt"], case.get("attachments"),
                case.get("scope", "project"), case.get("selected_shot_id", ""),
            )
            result = director_chat(data)
            preview = (
                preview_changeset(case["document"], result["proposal"])
                if result.get("proposal") else {"document": {}, "compiled_prompt": ""}
            )
            errors = validate(name, case, result, preview)
            elapsed = time.monotonic() - started
            summary = {
                "case": name,
                "seconds": round(elapsed, 2),
                "proposal_error": result.get("proposal_error", ""),
                "shots": len(preview.get("document", {}).get("shots") or []),
                "resolved_mode": preview.get("document", {}).get("resolved_mode"),
                "vision_observations": result.get("vision_observations") or [],
                "errors": errors,
                "compiled_words": len(preview.get("compiled_prompt", "").split()),
            }
            if args.show_prompt:
                summary["compiled_prompt"] = preview.get("compiled_prompt", "")
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            if errors:
                failures += 1
        except Exception as exc:
            failures += 1
            print(json.dumps({"case": name, "exception": str(exc)}, indent=2), flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
