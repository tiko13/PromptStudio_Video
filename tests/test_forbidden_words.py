from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from video.compiler import compile_prompt
from video.contracts import PromptDocumentError
from video.director import build_provider_messages
from video.vocabulary import shared_rules
from test_compiler import base_document


class ForbiddenWordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "forbidden_words.json"
        patcher = patch.object(shared_rules(), "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, name, replacement="", **options):
        self.path.write_text(json.dumps({"forbidden_words": [{"name": name, "replacement": replacement, **options}]}), encoding="utf-8")

    def test_guidance_reaches_director_without_becoming_compiled_text(self):
        guidance = "Replace cinematic with a concrete description of lighting"
        self.save("cinematic", guidance, replacement_mode="guidance")
        messages, _ = build_provider_messages({"document": base_document(), "scope": "project",
            "messages": [{"role": "user", "text": "Rephrase the style"}]})
        self.assertIn(guidance, messages[0]["content"])
        self.assertIn('"replacement_mode": "guidance"', messages[0]["content"])
        with self.assertRaisesRegex(PromptDocumentError, "cinematic"):
            compile_prompt(base_document())
        self.assertNotIn(guidance, compile_prompt(base_document(style="Live-action, soft natural lighting")))
        self.save("cinematic", "filmic", replacement_mode="verbatim")
        self.assertEqual(compile_prompt(base_document(prompt_override="CINEMATIC street")), "filmic street")
        for mode in ("verbatim", "guidance"):
            self.save("cinematic", "", replacement_mode=mode)
            with self.assertRaisesRegex(PromptDocumentError, "cinematic"):
                compile_prompt(base_document(prompt_override="cinematic street"))

    def test_compilation_replacement_and_blank_rule_including_override(self):
        self.save("cinematic", "filmic")
        self.assertIn("filmic", compile_prompt(base_document()))
        self.assertNotIn("cinematic", compile_prompt(base_document()).lower())
        self.assertEqual(compile_prompt(base_document(prompt_override="CINEMATIC street")), "filmic street")
        self.save("cinematic")
        with self.assertRaises(PromptDocumentError):
            compile_prompt(base_document())
        with self.assertRaises(PromptDocumentError):
            compile_prompt(base_document(prompt_override="cinematic street"))

    def test_rules_do_not_damage_required_grammar(self):
        self.save("Shot", "Scene")
        with self.assertRaisesRegex(PromptDocumentError, "syntax"):
            compile_prompt(base_document())

    def test_director_receives_rules_even_when_existing_document_is_forbidden(self):
        self.save("cinematic")
        messages, _ = build_provider_messages({"document": base_document(), "scope": "project", "messages": [{"role": "user", "text": "Rephrase the style"}]})
        self.assertIn("Forbidden words", messages[0]["content"])
        self.assertIn('"name": "cinematic"', messages[0]["content"])
