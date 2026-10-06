import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from video.contracts import normalize_document, model_references, PromptDocumentError
from video.compiler import compile_prompt
from video.timeline_guides import apply_timeline_guides


class TimelineGuideTests(unittest.TestCase):
    def document(self, **changes):
        reference = {"id": "guide", "path": "guide.png", "kind": "image", "roles": ["timeline_guide"], "guide_frame": 48}
        reference.update(changes)
        return {"duration_seconds": 5, "references": [reference], "shots": [{"action": "A person waves."}]}

    def test_guides_do_not_change_mode_or_reference_labels(self):
        document = normalize_document(self.document())
        self.assertEqual(document["resolved_mode"], "t2va")
        self.assertEqual(model_references(document), [])
        self.assertEqual(document["references"][0]["label"], "")
        self.assertNotIn("<Picture", compile_prompt(document))
        self.assertEqual(normalize_document(document), document)

    def test_video_and_audio_guides_do_not_switch_to_ref2va(self):
        for kind in ("video", "audio"):
            self.assertEqual(normalize_document(self.document(kind=kind))["resolved_mode"], "t2va")

    def test_invalid_frame_or_mixed_roles_are_rejected(self):
        for frame in (-1, 1.5, float("nan"), 124, True):
            with self.subTest(frame=frame), self.assertRaises(PromptDocumentError):
                normalize_document(self.document(guide_frame=frame))
        with self.assertRaisesRegex(PromptDocumentError, "exclusive"):
            normalize_document(self.document(roles=["timeline_guide", "subject"]))

    def test_native_guide_returns_conditioning_only_and_retains_latent(self):
        calls = []
        class Guide:
            @staticmethod
            def execute(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(result=("guided",))
        media = SimpleNamespace(_load_image=lambda path: ["image"], _load_audio=None, _video_components=None)
        with patch.dict(sys.modules, {"nodes": SimpleNamespace(NODE_CLASS_MAPPINGS={"MiniMaxH3AddGuide": Guide}), "video.media": media}):
            latent = {"samples": "unchanged"}
            positive, returned = apply_timeline_guides(normalize_document(self.document()), "original", latent, "vae", "audio-vae")
        self.assertEqual(positive, "guided")
        self.assertIs(returned, latent)
        self.assertEqual(calls[0]["frame_idx"], 48)

    def test_guide_clip_must_fit_remaining_timeline(self):
        media = SimpleNamespace(_load_image=None, _load_audio=None,
                                _video_components=lambda *a, **k: ([0] * 100, None, 24))
        with patch.dict(sys.modules, {"nodes": SimpleNamespace(NODE_CLASS_MAPPINGS={"MiniMaxH3AddGuide": object}), "video.media": media}):
            with self.assertRaisesRegex(PromptDocumentError, "extends beyond"):
                apply_timeline_guides(normalize_document(self.document(kind="video", guide_frame=80)), "positive", {}, None, None)
