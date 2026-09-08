import unittest
from pathlib import Path
from video.contracts import normalize_document, normalize_document_wire, shared_wire_contracts


class VideoWireContractsTests(unittest.TestCase):
    def test_legacy_document_and_versioned_variant_compile_to_same_document(self):
        legacy = normalize_document({"main_description": "A train arrives.", "duration_seconds": 5})
        envelope = normalize_document_wire(legacy)
        self.assertEqual(envelope["kind"], "video_document")
        self.assertEqual(envelope["wire_version"], 1)
        self.assertEqual(normalize_document(envelope), legacy)

    def test_wrong_version_and_variant_rejected_at_existing_document_boundary(self):
        for value in ({"wire_version": 2, "kind": "video_document", "document": {}}, {"wire_version": 1, "kind": "llm_job", "document": {}}):
            with self.assertRaises(ValueError):
                normalize_document(value)

    def test_shared_provider_contract_is_the_image_module(self):
        module = shared_wire_contracts()
        expected = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "wire_contracts.py"
        self.assertEqual(Path(module.__file__).resolve(), expected.resolve())
        self.assertIs(module, shared_wire_contracts())
        self.assertEqual(module.normalize_provider_settings({"llm_provider": "ollama"})["llm_provider"], "ollama")
