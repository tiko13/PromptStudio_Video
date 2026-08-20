from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StructuredExtensionFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "web" / "js" / "promptstudio_video_studio.js").read_text(encoding="utf-8")

    def test_continue_dialog_offers_quick_and_structured_paths(self):
        self.assertIn('submit.textContent = "Quick generate"', self.source)
        self.assertIn('button("Build full extension"', self.source)
        self.assertIn("createStructuredExtensionProject(project, generation", self.source)

    def test_structured_generation_sends_authored_document_to_continuation_prepare(self):
        self.assertIn("async function generateStructuredExtension(project)", self.source)
        self.assertIn("extension_document: project.document", self.source)
        self.assertIn("source_document: clone(source.source_document)", self.source)
        self.assertIn("await generateStructuredExtension(project)", self.source)

    def test_extension_uses_shot_director_context_and_hides_grand_director(self):
        self.assertIn("continuation_context: clone(project.extension_source?.director_context || null)", self.source)
        self.assertIn("grandDirector.hidden = structuredExtension", self.source)
        self.assertIn("Save & ask Director", self.source)

    def test_extension_can_regenerate_only_its_tail_from_the_same_parent(self):
        self.assertIn('button("Regenerate extension", () => showRegenerateExtension(generation)', self.source)
        self.assertIn("async function regenerateExtension(project, generation, request, dialog, submit)", self.source)
        self.assertIn("parent_generation_id: generation.parent_generation_id", self.source)
        self.assertIn("source_segments: clone(request.source_segments)", self.source)
        self.assertIn("randomizeSnapshotSeeds(snapshot)", self.source)

    def test_quick_regeneration_can_revise_prompt_and_persists_source_document(self):
        self.assertIn('field("What should happen instead?", briefInput', self.source)
        self.assertIn("source_document: clone(parent.document)", self.source)
        self.assertIn("continuation_request: clone(request)", self.source)

    def test_structured_regeneration_uses_current_authored_extension_document(self):
        self.assertIn("extensionSource ? project.document : saved.extension_document", self.source)
        self.assertIn("The current extension project shots, dialogue, camera, and sounds will be used.", self.source)


if __name__ == "__main__":
    unittest.main()
