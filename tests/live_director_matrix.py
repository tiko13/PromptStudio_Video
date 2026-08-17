"""Manual live-LLM integration matrix for PromptStudio Video Director.

Run from the plugin root with ComfyUI's Python. This file is deliberately not
named test_*.py because it requires a running local KoboldCpp instance.
"""

from __future__ import annotations

import argparse
import json
import re
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
USER_FIRST_FRAME_IMAGE = "ComfyUI_00005_.webp"


def reference(index, path, role, kind="image", prompt=""):
    return {
        "id": f"reference-{index}",
        "kind": kind,
        "path": path,
        "name": Path(path).stem,
        "roles": [role],
        "prompt": prompt,
    }


def user_first_frame_reference():
    value = reference(1, USER_FIRST_FRAME_IMAGE, "first_frame")
    value.update({
        "observed_visual_facts": (
            "A full-body young woman stands on grey cobblestones with long straight brown hair, "
            "a tight-fitting red mini dress with short sleeves, and bare feet."
        ),
        "subject_candidates": [{
            "name": "young woman", "location": "center",
            "visual_selectors": ["woman in red dress", "barefoot woman", "long brown hair"],
            "grounded_attributes": {
                "hair": "long, straight, brown", "face": "fair skin",
                "clothing": "red mini dress", "footwear": "barefoot",
            },
        }],
    })
    return value


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
        "i2va_hard_cut_identity": {
            "document": document(
                mode="i2va", references=[user_first_frame_reference()],
                shots=[shot("shot-1", 0)], duration=6,
            ),
            "prompt": (
                "The girl in picture 1 will smile and start walking towards the camera. When she reaches "
                "the camera, the shot will cut to a new shot - the girl will be standing in a luxury "
                "bedroom, and the camera will pan around her. Her identity, hair, red mini dress, bare "
                "feet, and body proportions must remain exactly the same across the cut."
            ),
            "mode": "i2va",
            "shots": 2,
            "prompt_prefix": "For the target video, at 0.00 seconds",
            "compiled_required_terms": [
                "the same person shown in <Picture 1>", "clothing (red mini dress)",
                "footwear (barefoot)", "there is no wardrobe change",
                "camera moves in an arc around the subject",
            ],
            "compiled_forbidden_terms": ["silk robe", "gown", "high heels", "bracelet"],
            "shot_required_patterns": [
                [r"smil(?:e|es|ing).*(?:walk|approach)"],
                [r"luxur(?:y|ious).*bedroom", r"Arc Shot"],
            ],
        },
        "i2va_intentional_wardrobe_change": {
            "document": document(
                mode="i2va", references=[user_first_frame_reference()],
                shots=[shot("shot-1", 0)], duration=6,
            ),
            "prompt": (
                "Create exactly two shots. She takes one step toward camera in Shot 1. At the cut to Shot 2, "
                "keep the same woman's face, brown hair, and body proportions, but deliberately change only "
                "her wardrobe from the red mini dress to a tailored black tuxedo and black dress shoes. She "
                "adjusts one cuff in a hotel lobby. This wardrobe change is intentional."
            ),
            "mode": "i2va",
            "shots": 2,
            "compiled_required_terms": ["black tuxedo"],
            "compiled_forbidden_terms": ["there is no wardrobe change"],
            "shot_required_patterns": [
                [r"step.*(?:camera|forward)"],
                [r"black tuxedo", r"adjust.*cuff", r"hotel lobby"],
            ],
        },
        "t2va_match_cut_prop_continuity": {
            "document": document(
                mode="t2va",
                shots=[{"id": "shot-1", "start": 0, "steps": []}],
                duration=9,
            ),
            "prompt": (
                "Completely rewrite the video as exactly three shots at 0, 3, and 6 seconds. A detective in "
                "a charcoal coat carries a closed red umbrella in her right hand. Shot 1 tracks her walking "
                "screen-left to screen-right through a station. Cut on action to Shot 2 without reversing screen "
                "direction; the same umbrella remains closed in the same right hand as she stops under a clock. "
                "Use a match cut to Shot 3 on the umbrella's red curved handle; it is still closed and in her "
                "right hand as she enters a rainy street. Preserve her identity, coat, umbrella state, hand, and "
                "screen direction throughout. Add only synchronized footsteps, coat movement, station ambience, "
                "and rain that begins in Shot 3; no invented keys, jewelry, heels, doors, or music."
            ),
            "mode": "t2va",
            "shots": 3,
            "starts": [0, 3, 6],
            "forbidden": "OBSOLETE",
            "shot_required_patterns": [
                [r"charcoal coat", r"closed red umbrella.*right hand", r"left.*right"],
                [r"closed red umbrella.*right hand", r"clock", r"left.*right"],
                [r"red.*handle", r"closed.*right hand", r"rain"],
            ],
            "compiled_forbidden_terms": ["keys", "bracelet", "necklace", "high heels"],
        },
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
                "childlike drawing style, simple shapes, blue sky, green hill, and bright palette. Apply all three "
                "references throughout all three shots. Across all three "
                "shots she approaches the chair, performs a joyful dance around it, then sits on the chair and raises both hands. "
                "Keep every reference assignment distinct, define its traits once, and reuse each applicable canonical "
                "label naturally in every affected shot without repeating trait catalogs. Populate all six REF2VA "
                "sections, synchronized sounds, soundscape, and music."
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
                ["drawing", "cartoon", "illustration", "yellow", "blue", "green"],
            ],
            "observation_forbidden_terms": [
                [],
                ["dark brown hair", "white short-sleeved", "black skirt"],
                [],
            ],
            "definition_forbidden_terms": {
                "<Picture 2>": ["dark brown hair", "white short-sleeved", "black skirt"],
            },
            "shot_required_patterns": [
                [r"(?:approach(?:es|ing)?|walk(?:s|ing)?\s+(?:toward|to)).*<Subject 2>"],
                [r"danc(?:e|es|ing).*<Subject 2>"],
                [r"sit(?:s|ting)?.*(?:on|in).*<Subject 2>", r"rais(?:e|es|ing).*both hands"],
            ],
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
        "selected_shot_i2va": {
            "document": document(
                mode="i2va", references=i2va_refs, shots=[shot("shot-1", 0)], duration=6,
            ),
            "scope": "shot",
            "selected_shot_id": "shot-1",
            "attachments": [attachment(1, FIRST_FRAME_IMAGE, "first_frame")],
            "prompt": (
                "Rewrite only the selected shot's steps, camera, and sounds. From the exact first frame, the woman "
                "slowly turns her head toward camera while keeping both hands behind her back; use a small slow push-in "
                "and synchronize one soft hair movement and dress-fabric rustle. Do not change pixel-owned setup fields."
            ),
            "mode": "i2va",
            "shots": 1,
            "starts": [0],
            "first_frame_lock": True,
            "prompt_prefix": "For the target video, at 0.00 seconds",
            "selected_required_terms": ["turn", "behind"],
            "observation_terms": [["woman", "dress", "door"]],
            "observation_forbidden_terms": [[]],
        },
        "selected_shot_fl2va": {
            "document": document(
                mode="fl2va", references=fl2va_refs, shots=[shot("shot-1", 0)], duration=6,
            ),
            "scope": "shot",
            "selected_shot_id": "shot-1",
            "attachments": [
                attachment(1, FIRST_FRAME_IMAGE, "first_frame"),
                attachment(2, LAST_FRAME_IMAGE, "last_frame"),
            ],
            "prompt": (
                "Rewrite only this selected shot's steps, camera, and sounds so it continuously interpolates from "
                "<Picture 1> to <Picture 2>. The woman shifts her weight gradually without a cut; use a slow small "
                "pull-out and synchronize subtle dress fabric and one final foot placement."
            ),
            "mode": "fl2va",
            "shots": 1,
            "starts": [0],
            "first_frame_lock": True,
            "prompt_prefix": "How the reference pictures align",
            "selected_required_terms": ["weight"],
            "compiled_required_terms": ["<Picture 2>"],
            "observation_terms": [
                ["woman", "dress", "door"], ["woman", "dress", "door"],
            ],
            "observation_forbidden_terms": [[], []],
        },
        "selected_shot_l2va": {
            "document": document(
                mode="l2va", references=l2va_refs, shots=[shot("shot-1", 0)], duration=6,
            ),
            "scope": "shot",
            "selected_shot_id": "shot-1",
            "attachments": [attachment(1, LAST_FRAME_IMAGE, "last_frame")],
            "prompt": (
                "Rewrite only this selected shot so the woman begins one step left of the final pose, takes one slow "
                "step right, and lands exactly on <Picture 1>. Use a static camera and synchronized footstep and fabric sound."
            ),
            "mode": "l2va",
            "shots": 1,
            "starts": [0],
            "prompt_prefix": "How the reference pictures align",
            "selected_required_terms": ["Picture 1", "right"],
            "observation_terms": [["woman", "dress", "door"]],
            "observation_forbidden_terms": [[]],
        },
        "selected_shot_ref2va": {
            "document": document(
                mode="ref2va",
                references=[reference(1, SUBJECT_IMAGE, "subject")],
                shots=[shot("shot-1", 0)],
                duration=6,
            ),
            "scope": "shot",
            "selected_shot_id": "shot-1",
            "attachments": [attachment(1, SUBJECT_IMAGE, "subject")],
            "prompt": (
                "Rewrite only this selected shot so the girl from <Picture 1> takes two measured steps forward while "
                "smiling throughout, then stops. Add a slow small tracking shot and synchronized boot steps and clothing rustle."
            ),
            "mode": "ref2va",
            "shots": 1,
            "starts": [0],
            "reference_tokens": ["<Picture 1>"],
            "shot_reference_tokens": ["<Subject 1>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "selected_required_terms": ["Subject 1", "forward"],
            "observation_terms": [["blonde", "pink", "boots"]],
            "observation_forbidden_terms": [[]],
        },
        "t2va_dialogue_voiceover_text": {
            "document": document(
                mode="t2va", shots=[shot("shot-1", 0), shot("shot-2", 4)], duration=8,
            ),
            "prompt": (
                "Completely rewrite the full production as exactly two shots at 0 and 4 seconds. In Shot 1, a female "
                "engineer smiles throughout a wave while saying exactly \"Wait—don't touch that.\"; the spoken line "
                "continues across the cut. A console visibly reads \"CORE TEMP: 72°C\". In Shot 2, the same engineer "
                "keeps her lips completely closed while her off-screen voiceover says exactly \"The signal is still alive.\" "
                "and is cut off by the end of the video. Preserve both strings and punctuation verbatim. Add concrete "
                "camera work and synchronized machinery, fabric, and hand-movement sounds, with no background music."
            ),
            "mode": "t2va",
            "shots": 2,
            "starts": [0, 4],
            "forbidden": "OBSOLETE",
            "expected_dialogue_texts": ["Wait—don't touch that.", "The signal is still alive."],
            "expected_dialogue_flags": [
                {
                    "text": "Wait—don't touch that.", "crosses_cut": True,
                    "voiceover": False, "cutoff": False,
                },
                {
                    "text": "The signal is still alive.", "crosses_cut": False,
                    "voiceover": True, "cutoff": True,
                },
            ],
            "expected_visible_texts": ["CORE TEMP: 72°C"],
            "compiled_required_terms": [
                "<scenetrans>", "says in an off-screen voiceover", "lips remain completely closed",
                "<cutoff>", '"CORE TEMP: 72°C"',
            ],
        },
        "t2va_two_speakers_interrupt_and_singing": {
            "document": document(
                mode="t2va", shots=[{"id": "shot-1", "start": 0, "steps": []}], duration=8,
            ),
            "prompt": (
                "Completely rewrite the production as exactly two shots at 0 and 4 seconds. In Shot 1, a woman "
                "walks toward an exit while saying exactly \"We go now.\" in English. Before she finishes moving, "
                "a man interrupts her and says exactly \"Wait.\" in English; keep distinct stable speaker IDs and "
                "make the interruption explicit in delivery while her walking continues. A sign visibly reads "
                "\"EXIT 7\". In Shot 2, the man remains off-screen and sings exactly \"Stay with me.\" in English "
                "while the woman stops at the doorway and keeps her lips closed. Use synchronized footsteps and "
                "room ambience. Quiet piano ducks beneath the spoken lines and swells only after the singing ends."
            ),
            "mode": "t2va", "shots": 2, "starts": [0, 4], "forbidden": "OBSOLETE",
            "expected_dialogue_texts": ["We go now.", "Wait.", "Stay with me."],
            "expected_visible_texts": ["EXIT 7"],
            "compiled_required_terms": [
                "(S1) says", "(S2) says", "(S2) sings off-screen",
                "<d>[English] We go now.</d>", "<d>[English] Wait.</d>",
                "<d>[English] Stay with me.</d>", '"EXIT 7"',
            ],
            "dialogue_delivery_contains": {"Wait.": "interrupt"},
            "distinct_speaker_ids": True,
        },
        "t2va_complete_silence": {
            "document": document(mode="t2va", shots=[shot("shot-1", 0)], duration=6),
            "prompt": (
                "Completely rewrite the full production as one continuous silent shot. A paper crane slowly unfolds "
                "itself on a black table under a narrow white spotlight. The entire video must be completely silent: "
                "no dialogue, no physical sound, no ambience, and no music."
            ),
            "mode": "t2va",
            "shots": 1,
            "starts": [0],
            "forbidden": "OBSOLETE",
            "complete_silence": True,
        },
        "ref2va_video_edit_audio_reference": {
            "document": document(
                mode="ref2va",
                references=[
                    reference(
                        1, "source_edit.mp4", "video_edit", "video",
                        "A two-shot source: a courier enters at 0 seconds and opens a metal case after the 4-second cut.",
                    ),
                    reference(
                        2, "calm_voice.wav", "audio_reference", "audio",
                        "A low, calm alto voice timbre with measured pacing; words are not to be copied.",
                    ),
                ],
                shots=[shot("shot-1", 0), shot("shot-2", 4)], duration=8,
            ),
            "prompt": (
                "Create the complete two-shot REF2VA edit at 0 and 4 seconds. The target video is an edited version "
                "of <Video 1>. Preserve its courier action, cut timing, and camera rhythm, but place the action in a "
                "moonlit laboratory. In Shot 2 the courier says exactly \"Delivery confirmed.\" using only the voice "
                "timbre and pacing referenced by <Audio 1>; do not copy any source words. Use no non-diegetic music."
            ),
            "mode": "ref2va",
            "shots": 2,
            "starts": [0, 4],
            "reference_tokens": ["<Video 1>", "<Audio 1>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "expected_task_types": ["video editing", "audio reference"],
            "expected_dialogue_texts": ["Delivery confirmed."],
            "compiled_required_terms": ["The target video is an edited version of <Video 1>"],
            "forbidden": "OBSOLETE",
        },
        "ref2va_video_continue_audio_copy": {
            "document": document(
                mode="ref2va",
                references=[
                    reference(
                        1, "source_continue.mp4", "video_continue", "video",
                        "The source ends with a red train stopped at a wet platform, doors open, camera tracking right.",
                    ),
                    reference(
                        2, "platform_rain.wav", "audio_copy", "audio",
                        "A clean rain-and-platform ambience track without speech or music.",
                    ),
                ],
                shots=[shot("shot-1", 0), shot("shot-2", 4)], duration=8,
            ),
            "prompt": (
                "Create a complete two-shot continuation of <Video 1> at 0 and 4 seconds. Begin from its exact final "
                "state and continue the rightward tracking move as one passenger exits and the red train doors close. "
                "Fully copy <Audio 1> as the continuous diegetic ambience underneath both shots. Add no dialogue and "
                "no non-diegetic music."
            ),
            "mode": "ref2va",
            "shots": 2,
            "starts": [0, 4],
            "reference_tokens": ["<Video 1>", "<Audio 1>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "expected_task_types": ["video continuation", "audio reuse"],
            "compiled_required_terms": ["<Video 1>", "<Audio 1>"],
            "forbidden": "OBSOLETE",
        },
        "t2va_describe_only_image": {
            "document": document(mode="t2va", shots=[shot("shot-1", 0)], duration=6),
            "attachments": [attachment(1, CHAIR_IMAGE, "describe")],
            "prompt": (
                "Completely rewrite the full production as one continuous shot. Inspect the attached image only as "
                "visual context, not as a MiniMax reference. Create an original scene in which a woman approaches the "
                "visible red chair and rests one hand on its rolled arm. Use concrete composition, setting, lighting, "
                "camera, synchronized footsteps and upholstery contact, with no music."
            ),
            "mode": "t2va",
            "shots": 1,
            "starts": [0],
            "observation_terms": [["red", "chair", "woman"]],
            "observation_forbidden_terms": [[]],
            "compiled_forbidden_terms": ["<Picture", "<Subject"],
            "forbidden": "OBSOLETE",
        },
        "ref2va_image_role_matrix": {
            "document": document(
                mode="ref2va",
                references=[
                    reference(1, CHAIR_IMAGE, "action"),
                    reference(2, SUBJECT_IMAGE, "pose"),
                    reference(3, FIRST_FRAME_IMAGE, "camera"),
                    reference(4, DRAWING_IMAGE, "storyboard"),
                ],
                shots=[shot("shot-1", 0), shot("shot-2", 4)], duration=8,
            ),
            "attachments": [
                attachment(1, CHAIR_IMAGE, "action"),
                attachment(2, SUBJECT_IMAGE, "pose"),
                attachment(3, FIRST_FRAME_IMAGE, "camera"),
                attachment(4, DRAWING_IMAGE, "storyboard"),
            ],
            "prompt": (
                "Completely rewrite the full REF2VA production as exactly two shots at 0 and 4 seconds. Use Picture 1 "
                "only as the action reference for lowering into the chair and crossing the legs; Picture 2 only for the "
                "upright centered pose; Picture 3 only for camera height and centered doorway composition; and Picture 4 "
                "as the concrete storyboard layout. Keep all roles distinct, define their reusable labels once, and use "
                "all four references throughout both shots. Add synchronized chair, clothing, and foot sounds, no music."
            ),
            "mode": "ref2va",
            "shots": 2,
            "starts": [0, 4],
            "reference_tokens": ["<Picture 1>", "<Picture 2>", "<Picture 3>", "<Picture 4>"],
            "shot_reference_tokens": ["<Subject 1>", "<Subject 2>", "<Subject 3>", "<Picture 4>"],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "observation_terms": [
                ["chair", "crossed", "legs", "sits"],
                ["standing", "upright", "arms", "legs"],
                ["shot", "frame", "door", "sunlight"],
                ["creature", "hill", "sky"],
            ],
            "observation_forbidden_terms": [[], [], [], []],
            "forbidden": "OBSOLETE",
        },
        "ref2va_first_last_style": {
            "document": document(
                mode="ref2va",
                references=[
                    reference(1, FIRST_FRAME_IMAGE, "first_frame"),
                    reference(2, LAST_FRAME_IMAGE, "last_frame"),
                    reference(3, DRAWING_IMAGE, "style"),
                ],
                shots=[shot("shot-1", 0)], duration=8,
            ),
            "attachments": [
                attachment(1, FIRST_FRAME_IMAGE, "first_frame"),
                attachment(2, LAST_FRAME_IMAGE, "last_frame"),
                attachment(3, DRAWING_IMAGE, "style"),
            ],
            "prompt": (
                "Create the complete one-shot REF2VA keyframe interpolation. Begin exactly from Picture 1 and land "
                "exactly on Picture 2 at the end while the woman slowly brings both hands from behind her back to rest "
                "at her sides. Apply only the flat vector treatment and bright palette from Picture 3 throughout, without "
                "copying its depicted character. Populate all six sections and synchronized fabric sound, no music."
            ),
            "mode": "ref2va",
            "shots": 1,
            "starts": [0],
            "reference_tokens": ["<Picture 1>", "<Picture 2>", "<Picture 3>"],
            "compiled_required_terms": [
                "The visual treatment throughout the target video follows <Subject 3>.",
                "<Picture 2>",
            ],
            "ordered_sections": [
                "subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
            ],
            "expected_task_types": ["keyframe completion", "reference generation"],
            "first_frame_lock": True,
            "observation_terms": [
                ["woman", "dress", "door"],
                ["woman", "dress", "door"],
                ["drawing", "illustration", "vector", "blue", "green", "yellow"],
            ],
            "observation_forbidden_terms": [[], [], []],
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
        if (
            not case.get("complete_silence")
            and "sound" in case["prompt"].casefold()
            and not shot_value.get("sounds")
        ):
            errors.append(f"{shot_value.get('id')} has no synchronized sounds")
    expected_steps = case.get("expected_step_types")
    if expected_steps:
        actual_steps = [
            step.get("type") for step in result_document["shots"][0].get("steps") or []
        ]
        if actual_steps != expected_steps:
            errors.append(f"step order {actual_steps} != {expected_steps}")
    for index, patterns in enumerate(case.get("shot_required_patterns") or []):
        if index >= len(result_document.get("shots") or []):
            break
        shot_text = json.dumps(result_document["shots"][index], ensure_ascii=False)
        for pattern in patterns:
            if not re.search(pattern, shot_text, re.IGNORECASE | re.DOTALL):
                errors.append(f"shot-{index + 1} missed required relation /{pattern}/")
    if case.get("first_frame_lock"):
        first = result_document["shots"][0]
        leaked = [
            field for field in ("composition", "subjects", "environment", "lighting")
            if first.get(field) != case["document"]["shots"][0].get(field)
        ]
        if leaked:
            errors.append(f"first-frame-owned fields changed: {leaked}")
    expected_silence = case.get("complete_silence")
    if expected_silence is not None and bool(result_document.get("complete_silence")) != expected_silence:
        errors.append(
            f"complete_silence {result_document.get('complete_silence')} != {expected_silence}"
        )
    if expected_silence and (
        "overall_soundscape: N/A" not in compiled or "non_diegetic_music: N/A" not in compiled
    ):
        errors.append("complete_silence did not suppress compiled soundscape and music")
    maximum_words = 650 + 100 * max(0, len(result_document.get("references") or []) - 3)
    if len(compiled.split()) > maximum_words:
        errors.append(f"compiled prompt is unexpectedly verbose: {len(compiled.split())} words")
    speaker_id_pattern = re.compile(r"(?<![A-Za-z0-9_<])S\d+(?![A-Za-z0-9_>])")
    prose_fields = (
        "composition", "subjects", "environment", "lighting", "transition", "notes"
    )
    for shot_value in result_document.get("shots") or []:
        prose_values = [str(shot_value.get(field) or "") for field in prose_fields]
        prose_values.extend(str(item or "") for item in shot_value.get("sounds") or [])
        prose_values.extend(str(item or "") for item in shot_value.get("visible_text") or [])
        prose_values.extend(
            str(step.get("text") or "")
            for step in shot_value.get("steps") or []
            if step.get("type") == "action"
        )
        bare_ids = sorted(set(speaker_id_pattern.findall("\n".join(prose_values))))
        if bare_ids:
            errors.append(
                f"{shot_value.get('id')} uses speaker IDs as prose nouns outside dialogue: {bare_ids}"
            )
    if case.get("prompt_prefix") and not compiled.startswith(case["prompt_prefix"]):
        errors.append(f"compiled prompt does not start with {case['prompt_prefix']!r}")
    forbidden = case.get("forbidden")
    if forbidden and forbidden.casefold() in json.dumps(result_document).casefold():
        errors.append(f"rewritten document still contains {forbidden!r}")
    expected_task_types = case.get("expected_task_types")
    if expected_task_types and result_document.get("task_types") != expected_task_types:
        errors.append(f"task_types {result_document.get('task_types')} != {expected_task_types}")
    dialogue_texts = [
        step.get("text")
        for shot_value in result_document.get("shots") or []
        for step in shot_value.get("steps") or []
        if step.get("type") == "dialogue"
    ]
    for expected_text in case.get("expected_dialogue_texts") or []:
        if expected_text not in dialogue_texts:
            errors.append(f"exact dialogue string missing: {expected_text!r}")
    for expected in case.get("expected_dialogue_flags") or []:
        event = next((item for item in (
            step
            for shot_value in result_document.get("shots") or []
            for step in shot_value.get("steps") or []
            if step.get("type") == "dialogue"
        ) if item.get("text") == expected["text"]), None)
        if event is None:
            continue
        for flag, value in expected.items():
            if flag != "text" and bool(event.get(flag)) != value:
                errors.append(
                    f"dialogue {expected['text']!r} flag {flag}={event.get(flag)} != {value}"
                )
    for text_value, delivery_term in (case.get("dialogue_delivery_contains") or {}).items():
        event = next((item for item in (
            step for shot_value in result_document.get("shots") or []
            for step in shot_value.get("steps") or [] if step.get("type") == "dialogue"
        ) if item.get("text") == text_value), None)
        if event is not None and delivery_term.casefold() not in str(event.get("delivery") or "").casefold():
            errors.append(f"dialogue {text_value!r} delivery missed {delivery_term!r}")
    if case.get("distinct_speaker_ids"):
        ids = [
            step.get("speaker_id") for shot_value in result_document.get("shots") or []
            for step in shot_value.get("steps") or [] if step.get("type") == "dialogue"
        ]
        if len(set(ids)) < 2:
            errors.append(f"expected at least two distinct speaker IDs, got {ids}")
    visible_texts = [
        value
        for shot_value in result_document.get("shots") or []
        for value in shot_value.get("visible_text") or []
    ]
    for expected_text in case.get("expected_visible_texts") or []:
        if expected_text not in visible_texts:
            errors.append(f"exact visible-text string missing: {expected_text!r}")
    for term in case.get("compiled_required_terms") or []:
        if term not in compiled:
            errors.append(f"compiled prompt missed required term {term!r}")
    for term in case.get("compiled_forbidden_terms") or []:
        if term in compiled:
            errors.append(f"compiled prompt contains forbidden term {term!r}")
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
    parser.add_argument("--show-document", action="store_true")
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
            if args.show_document:
                summary["document"] = preview.get("document", {})
                summary["pending_proposal"] = result.get("pending_proposal")
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            if errors:
                failures += 1
        except Exception as exc:
            failures += 1
            print(json.dumps({"case": name, "exception": str(exc)}, indent=2), flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
