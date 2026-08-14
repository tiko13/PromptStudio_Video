import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedStudioContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "web" / "js" / "promptstudio_video_studio.js").read_text(encoding="utf-8")
        cls.redirect = (ROOT / "web" / "js" / "promptstudio_video_redirect.js").read_text(encoding="utf-8")
        cls.page = (ROOT / "web" / "prompt_studio_video.html").read_text(encoding="utf-8")
        cls.routes = (ROOT / "routes.py").read_text(encoding="utf-8")
        cls.styles = (ROOT / "web" / "css" / "promptstudio_video_studio.css").read_text(encoding="utf-8")

    def test_capabilities_advertise_unified_shell(self):
        self.assertIn('"unified_studio_shell"', self.routes)

    def test_hidden_video_view_remains_an_open_handoff_target(self):
        status_start = self.source.index("function videoStudioStatus")
        status_end = self.source.index("async function completeRelayedHandoff", status_start)
        status = self.source[status_start:status_end]
        self.assertIn("state.standaloneAttached", status)
        self.assertNotIn("!state.panel.hidden", status)
        self.assertIn("setStandaloneVisibility", self.source)

    def test_video_header_has_the_shared_switch_and_absolute_icon(self):
        self.assertGreaterEqual(self.source.count('data-promptstudio-studio-mode="image"'), 2)
        self.assertIn("VIDEO_ICON_URL", self.source)
        self.assertIn("psvstudio-inspector-brand", self.source)

    def test_video_switch_stays_beside_the_brand_like_image_mode(self):
        switch_rule = self.styles.split(".psvstudio-studio-switch {", 1)[1].split("}", 1)[0]
        self.assertIn("flex: 0 0 auto", switch_rule)
        self.assertNotIn("margin-left: auto", switch_rule)

    def test_legacy_video_page_redirects_to_unified_video_mode(self):
        self.assertIn("prompt_studio.html?mode=video", self.page)
        self.assertIn("promptstudio_video_redirect.js", self.page)
        self.assertIn('pathname.endsWith("/prompt_studio_video.html")', self.redirect)

    def test_live_execution_activity_promotes_queued_generations(self):
        helper_start = self.source.index("function markGenerationExecuting")
        helper_end = self.source.index("function failGeneration", helper_start)
        helper = self.source[helper_start:helper_end]
        self.assertIn('record.generation.status === "queued"', helper)
        self.assertIn('updateGeneration(id, { status: "generating" })', helper)

        events_start = self.source.index("function setupProgressEvents")
        events_end = self.source.index("function setStandaloneVisibility", events_start)
        events = self.source[events_start:events_end]
        for event_name in ("execution_start", "progress", "executing", "progress_state"):
            handler_start = events.index(f'api.addEventListener("{event_name}"')
            handler = events[handler_start:handler_start + 500]
            self.assertIn("markGenerationExecuting(id)", handler)


if __name__ == "__main__":
    unittest.main()
