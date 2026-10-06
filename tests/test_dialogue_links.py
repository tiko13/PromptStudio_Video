import copy
import unittest
from video.compiler import compile_prompt
from video.contracts import normalize_document, PromptDocumentError
from video.prompt_advisories import prompt_advisories


class DialogueLinkTests(unittest.TestCase):
    def document(self):
        return {"duration_seconds": 5, "shots": [
            {"start": 0, "steps": [{"type": "dialogue", "text": "Keep this,", "utterance_id": "line-1"}]},
            {"start": 2, "steps": [{"type": "dialogue", "text": "exactly!", "utterance_id": "line-1"}]},
        ]}

    def test_markers_at_connecting_points_and_verbatim_text(self):
        document = self.document()
        before = copy.deepcopy(document)
        prompt = compile_prompt(document)
        self.assertIn("[English] Keep this,<scenetrans></d>", prompt)
        self.assertIn("[English] <scenetrans>exactly!</d>", prompt)
        self.assertIn("continues seamlessly across the cut", prompt)
        self.assertIn("carries over from the previous shot", prompt)
        self.assertEqual(document, before)

    def test_incompatible_speakers_and_cutoff_fail(self):
        document = self.document()
        document["shots"][1]["steps"][0]["speaker_id"] = "S2"
        with self.assertRaisesRegex(PromptDocumentError, "same speaker"):
            compile_prompt(document)
        document = self.document()
        document["shots"][0]["steps"][0]["cutoff"] = True
        with self.assertRaisesRegex(PromptDocumentError, "cutoff"):
            compile_prompt(document)

    def test_advice_does_not_rewrite_content(self):
        document = self.document()
        document["shots"][0]["steps"][0]["text"] = "one " * 20
        normalized = normalize_document(document)
        before = copy.deepcopy(normalized)
        self.assertIn("dialogue_density", {item["code"] for item in prompt_advisories(normalized)})
        self.assertEqual(normalized, before)

    def test_legacy_cut_flag_retains_its_original_prompt_form(self):
        prompt = compile_prompt({"shots": [{"steps": [{"type": "dialogue", "text": "Legacy.", "crosses_cut": True}]}]})
        self.assertIn("<scenetrans>Legacy.<scenetrans></d>", prompt)
        self.assertNotIn("continues seamlessly", prompt)
