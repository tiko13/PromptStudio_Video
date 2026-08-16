import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShotEditorScrollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.styles = (ROOT / "web" / "css" / "promptstudio_video_studio.css").read_text(encoding="utf-8")

    def rule(self, selector):
        return self.styles.split(f"{selector} {{", 1)[1].split("}", 1)[0]

    def test_sidebar_and_sections_scroll_independently(self):
        sidebar = self.rule(".psvstudio-shot-editor-setup")
        section = self.rule(".psvstudio-shot-editor-setup .psvstudio-inspector-section-body")

        self.assertIn("grid-auto-rows: max-content", sidebar)
        self.assertIn("overflow-y: auto", sidebar)
        self.assertIn("overflow-y: auto", section)
        self.assertIn("max-height:", section)

    def test_entire_chronological_workspace_scrolls(self):
        sequence = self.rule(".psvstudio-shot-sequence")
        step_list = self.rule(".psvstudio-step-list")

        self.assertIn("overflow-y: auto", sequence)
        self.assertIn("grid-template-rows: auto max-content", sequence)
        self.assertIn("overflow: visible", step_list)


if __name__ == "__main__":
    unittest.main()
