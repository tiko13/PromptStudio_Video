import unittest

from video.contracts import (
    PromptDocumentError,
    adapt_canvas,
    align_frame_count,
    effective_duration,
    normalize_document,
)


def document(**overrides):
    value = {
        "version": 1,
        "mode": "auto",
        "duration_seconds": 5,
        "shots": [{"id": "shot-1", "start": 0}],
        "references": [],
    }
    value.update(overrides)
    return value


class ContractTests(unittest.TestCase):
    def test_main_description_is_preserved(self):
        normalized = normalize_document(document(main_description="A cyclist crosses the city at dawn."))
        self.assertEqual(normalized["main_description"], "A cyclist crosses the city at dawn.")

    def test_manual_prompt_override_is_preserved(self):
        normalized = normalize_document(document(prompt_override="A complete ad-hoc MiniMax prompt."))
        self.assertEqual(normalized["prompt_override"], "A complete ad-hoc MiniMax prompt.")

    def test_adaptive_canvas_preserves_ratio_on_minimax_grid(self):
        self.assertEqual(adapt_canvas(1920, 1080), (1344, 768))
        self.assertEqual(adapt_canvas(1080, 1920), (768, 1344))
        self.assertEqual(adapt_canvas(1024, 1024), (1024, 1024))
        width, height = adapt_canvas(2520, 1080)
        self.assertEqual((width % 32, height % 32), (0, 0))
        self.assertLessEqual(width * height, 768 * 1344 + 32 * max(width, height))
        smaller = adapt_canvas(1920, 1080, target_megapixels=0.5)
        self.assertEqual((smaller[0] % 32, smaller[1] % 32), (0, 0))
        self.assertLess(smaller[0] * smaller[1], 1344 * 768)

    def test_reference_dimensions_and_canvas_link_are_preserved(self):
        reference = {
            "id": "opening",
            "kind": "image",
            "path": "opening.png",
            "roles": ["first_frame"],
            "source_width": 1920,
            "source_height": 1080,
        }
        normalized = normalize_document(document(
            width=1024,
            height=1024,
            target_megapixels=0.5,
            canvas_reference_id="opening",
            references=[reference],
        ))
        self.assertEqual(normalized["canvas_reference_id"], "opening")
        self.assertEqual((normalized["width"], normalized["height"]), adapt_canvas(1920, 1080, 0.5))
        self.assertEqual(normalized["target_megapixels"], 0.5)
        self.assertEqual(
            (normalized["references"][0]["source_width"], normalized["references"][0]["source_height"]),
            (1920, 1080),
        )

    def test_frame_count_uses_minimax_grid(self):
        self.assertEqual(align_frame_count(120), 124)
        self.assertEqual(align_frame_count(124), 124)
        self.assertEqual(effective_duration(5), 124 / 24)

    def test_auto_mode_tracks_anchor_roles(self):
        self.assertEqual(normalize_document(document())["resolved_mode"], "t2va")
        first = {"kind": "image", "path": "first.png", "roles": ["first_frame"]}
        last = {"kind": "image", "path": "last.png", "roles": ["last_frame"]}
        self.assertEqual(normalize_document(document(references=[first]))["resolved_mode"], "i2va")
        self.assertEqual(normalize_document(document(references=[last]))["resolved_mode"], "l2va")
        self.assertEqual(normalize_document(document(references=[first, last]))["resolved_mode"], "fl2va")

    def test_subject_or_video_reference_uses_ref2va(self):
        subject = {"kind": "image", "path": "subject.png", "roles": ["subject"]}
        normalized = normalize_document(document(references=[subject]))
        self.assertEqual(normalized["resolved_mode"], "ref2va")
        self.assertEqual(normalized["task_types"], ["reference generation"])
        self.assertEqual(normalized["references"][0]["label"], "<Picture 1>")

    def test_reference_subject_grounding_cache_is_preserved(self):
        subject = {
            "kind": "image",
            "path": "subject.png",
            "roles": ["subject"],
            "observed_visual_facts": "One young woman stands near the center.",
            "subject_candidates": [{
                "name": "young woman",
                "location": "center",
                "visual_selectors": ["person in black", "black jacket"],
            }],
        }
        normalized = normalize_document(document(references=[subject]))

        reference = normalized["references"][0]
        self.assertEqual(reference["observed_visual_facts"], subject["observed_visual_facts"])
        self.assertEqual(
            reference["subject_candidates"],
            [{
                "name": "young woman",
                "location": "center",
                "visual_selectors": ["person in black", "black jacket"],
            }],
        )

    def test_retention_relationship_spelling_variants_are_canonicalized(self):
        normalized = normalize_document(document(retention_analysis=[{
            "label": "<Subject 1>",
            "where": "appears in [Shot 1]",
            "relationship": "Fully Preserved",
            "detail": "The referenced appearance is retained.",
        }]))
        self.assertEqual(normalized["retention_analysis"][0]["relationship"], "fully_preserved")

    def test_invalid_retention_relationship_reports_the_value_and_allowed_enum(self):
        with self.assertRaisesRegex(
            PromptDocumentError,
            "invalid relationship 'identity_preserved'.*fully_preserved",
        ):
            normalize_document(document(retention_analysis=[{
                "label": "<Subject 1>",
                "relationship": "identity preserved",
                "detail": "The referenced appearance is retained.",
            }]))

    def test_explicit_modes_enforce_required_anchors(self):
        with self.assertRaisesRegex(PromptDocumentError, "first-frame"):
            normalize_document(document(mode="i2va"))
        with self.assertRaisesRegex(PromptDocumentError, "one first-frame and one last-frame"):
            normalize_document(document(mode="fl2va", references=[
                {"kind": "image", "path": "first.png", "roles": ["first_frame"]},
            ]))

    def test_shot_times_are_strict_and_inside_duration(self):
        with self.assertRaisesRegex(PromptDocumentError, "strictly increasing"):
            normalize_document(document(shots=[{"start": 0}, {"start": 0}]))
        with self.assertRaisesRegex(PromptDocumentError, "inside the effective duration"):
            normalize_document(document(shots=[{"start": 0}, {"start": 9}]))

    def test_dialogue_ids_are_validated(self):
        with self.assertRaisesRegex(PromptDocumentError, "speaker ID"):
            normalize_document(document(shots=[{
                "start": 0,
                "dialogue": [{"speaker_id": "speaker one", "text": "Hello."}],
            }]))

    def test_singing_is_supported_but_not_as_voiceover(self):
        normalized = normalize_document(document(shots=[{
            "start": 0,
            "dialogue": [{"speaker_id": "S1", "performance": "singing", "text": "Hold on."}],
        }]))
        self.assertEqual(normalized["shots"][0]["dialogue"][0]["performance"], "singing")
        with self.assertRaisesRegex(PromptDocumentError, "offscreen rather than voiceover"):
            normalize_document(document(shots=[{
                "start": 0,
                "dialogue": [{
                    "speaker_id": "S1", "performance": "singing", "voiceover": True, "text": "Hold on.",
                }],
            }]))

    def test_legacy_action_and_dialogue_migrate_to_ordered_steps(self):
        normalized = normalize_document(document(shots=[{
            "start": 0,
            "action": "She picks up the letter.",
            "dialogue": [{"speaker_id": "S1", "text": "This came yesterday."}],
        }]))
        shot = normalized["shots"][0]
        self.assertEqual([step["type"] for step in shot["steps"]], ["action", "dialogue"])
        self.assertEqual(shot["action"], "She picks up the letter.")
        self.assertEqual(shot["dialogue"][0]["text"], "This came yesterday.")

    def test_steps_are_authoritative_and_keep_legacy_mirrors(self):
        normalized = normalize_document(document(shots=[{
            "start": 0,
            "action": "Obsolete action.",
            "dialogue": [{"speaker_id": "S9", "text": "Obsolete line."}],
            "steps": [
                {"id": "step-1", "type": "action", "text": "She opens the letter."},
                {"id": "line-1", "type": "dialogue", "speaker_id": "S1", "text": "It is his handwriting."},
                {"id": "step-2", "type": "action", "text": "She freezes."},
            ],
        }]))
        shot = normalized["shots"][0]
        self.assertEqual(shot["action"], "She opens the letter. She freezes.")
        self.assertEqual([item["text"] for item in shot["dialogue"]], ["It is his handwriting."])
        self.assertEqual([step["id"] for step in shot["steps"]], ["step-1", "line-1", "step-2"])

    def test_step_types_are_validated(self):
        with self.assertRaisesRegex(PromptDocumentError, "unsupported type"):
            normalize_document(document(shots=[{
                "start": 0,
                "steps": [{"type": "pause", "text": "A pause."}],
            }]))


if __name__ == "__main__":
    unittest.main()
