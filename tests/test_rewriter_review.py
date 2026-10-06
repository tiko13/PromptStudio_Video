import copy
import json
import unittest

from video.compiler import compile_prompt
from video.rewriter_review import review_rewrite


class RewriteReviewTests(unittest.TestCase):
    def test_draft_review_is_nonmutating_and_preserves_protected_content(self):
        document = {"shots": [{"steps": [{"type": "dialogue", "text": "Keep me!"}], "visible_text": ["Cafe"]}]}
        before = copy.deepcopy(document)
        prompt = compile_prompt(document)
        result = review_rewrite(document, json.dumps({"enhanced_prompt": prompt}))
        self.assertTrue(result["eligible_for_proposal"], result)
        self.assertEqual(document, before)
        for bad in (prompt.replace("Keep me!", "Changed"), prompt.replace("Cafe", "Shop"),
                    prompt.replace("S1", "S2"), prompt.replace("overall_soundscape", "other")):
            self.assertFalse(review_rewrite(document, bad)["eligible_for_proposal"])

    def test_malformed_and_oversized_input_is_rejected(self):
        for value in (None, "", "{" , "{}", "x" * 24001):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                review_rewrite({}, value)

    def test_duplicate_or_added_dialogue_is_flagged(self):
        document = {"shots": [{"steps": [{"type": "dialogue", "text": "Hello."}]}]}
        prompt = compile_prompt(document)
        result = review_rewrite(document, prompt + "\n<d>[English] Hello.</d>")
        self.assertFalse(result["eligible_for_proposal"])
