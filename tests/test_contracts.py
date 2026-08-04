import unittest

from video.contracts import (
    PromptDocumentError,
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


if __name__ == "__main__":
    unittest.main()
