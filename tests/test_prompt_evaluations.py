"""Thin consumer of the shared synthetic suite; never performs live inference."""

import importlib.util
import sys
import unittest
from pathlib import Path

from video.compiler import compile_prompt
from video.contracts import normalize_document


PRIMARY = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio"
if "prompt_evals" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "prompt_evals", PRIMARY / "prompt_evals" / "__init__.py",
        submodule_search_locations=[str(PRIMARY / "prompt_evals")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from prompt_evals import cases, validate_cases


class VideoPromptEvaluationTests(unittest.TestCase):
    def test_video_consumes_same_versioned_synthetic_oracles(self):
        matrix = [case for case in cases() if case["product"] == "video"]
        self.assertGreaterEqual(validate_cases(matrix), 6)

    def test_video_fixture_documents_compile_with_current_deterministic_compiler(self):
        for case in cases():
            if case["product"] != "video" or "document" not in case["accepted_output"]:
                continue
            with self.subTest(case=case["id"]):
                document = normalize_document(case["accepted_output"]["document"])
                compiled = compile_prompt(document, use_override=False)
                self.assertIn("integrated_multimodal_description:", compiled)
                self.assertIn("[Shot 1]", compiled)
                self.assertIn("[Shot 2] At 00:04.000", compiled)
                self.assertIn("Stay here.", compiled)
                self.assertIn('"Stay open!"', compiled)


if __name__ == "__main__":
    unittest.main()
