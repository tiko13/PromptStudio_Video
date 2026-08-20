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

    def test_director_history_budgeting_keeps_complete_turns(self):
        start = self.source.index("function boundedDirectorMessages")
        end = self.source.index("\nfunction directorShotLabel", start)
        helper = self.source[start:end]

        self.assertIn("const turns = []", helper)
        self.assertIn("retainedTurns", helper)
        self.assertIn("return retainedTurns.reverse().flat()", helper)

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

    def test_live_progress_is_restored_after_returning_to_video_project(self):
        events_start = self.source.index("function setupProgressEvents")
        events_end = self.source.index("function setStandaloneVisibility", events_start)
        events = self.source[events_start:events_end]

        self.assertIn('node?.state === "running"', events)
        self.assertIn("Number(running.value)", events)
        self.assertIn("Number(running.max)", events)

        executing_start = events.index('api.addEventListener("executing"')
        executing_handler = events[executing_start:executing_start + 700]
        self.assertIn("samplerComplete", executing_handler)
        self.assertIn('node == null || samplerComplete ? "finalizing" : "generating"', executing_handler)
        self.assertIn("updateProgress(id", executing_handler)

        state_start = events.index('api.addEventListener("progress_state"')
        state_handler = events[state_start:state_start + 450]
        self.assertIn("const progress = runningProgress(event)", state_handler)
        self.assertIn("...(progress || {})", state_handler)
        self.assertIn("renderGenerations()", state_handler)

    def test_completed_sampler_progress_stays_visible_while_video_finalizes(self):
        render_start = self.source.index("function renderGenerations")
        render_end = self.source.index("function showCompiledPrompt", render_start)
        render = self.source[render_start:render_end]
        events_start = self.source.index("function setupProgressEvents")
        events_end = self.source.index("function setStandaloneVisibility", events_start)
        events = self.source[events_start:events_end]

        self.assertIn('{ ...current, ...changes }', events)
        self.assertIn("state.activeGenerationPromptId", events)
        self.assertIn('api.addEventListener("execution_success"', events)
        self.assertIn('phase: "finalizing"', events)
        self.assertIn('?.phase === "finalizing"', events)
        self.assertIn('current.phase !== "finalizing"', events)
        self.assertIn('progress.phase === "finalizing"', render)
        self.assertIn('"Finalizing…"', render)

    def test_turbo_profile_is_visible_and_follows_backend_routing_order(self):
        helper_start = self.source.index("function resolvedTurboProfile")
        helper_end = self.source.index("function renderTurboProfileIndicator", helper_start)
        helper = self.source[helper_start:helper_end]
        self.assertIn('mode === "ref2va"', helper)
        self.assertIn("width === 1344 && height === 768", helper)
        self.assertIn('preset === "fast_4step"', helper)
        self.assertLess(helper.index('mode === "ref2va"'), helper.index("width === 1344 && height === 768"))
        self.assertLess(helper.index("width === 1344 && height === 768"), helper.index('preset === "fast_4step"'))
        self.assertIn("renderTurboProfileIndicator(project, true)", self.source)
        self.assertIn("container.append(row, turboIndicator)", self.source)
        self.assertIn(".psvstudio-turbo-profile {", self.styles)

    def test_llamacpp_piggybacks_primary_settings_and_management(self):
        self.assertIn('const LLM_STATUS_ENDPOINT = "/promptstudio/prompt-studio/llm/status"', self.source)
        self.assertIn('const IMAGE_LLM_PROFILES_KEY = "promptstudio.promptStudio.llmProfiles.v1"', self.source)
        self.assertIn("studio.llamacpp_url", self.source)
        self.assertIn("studio.llamacpp_model", self.source)
        self.assertIn("studio.llamacpp_config_profile", self.source)
        self.assertIn("studio.llamacpp_autostart", self.source)
        self.assertIn("profile.llamacpp_reasoning_budget_tokens", self.source)
        self.assertIn("Shared Prompt Studio LLM", self.source)
        self.assertNotIn("/promptstudio-video/llamacpp/server", self.source)
        self.assertNotIn("/promptstudio-video/llamacpp/config-builder", self.routes)

    def test_restart_uses_manager_v4_with_a_legacy_fallback(self):
        self.assertIn('["/v2/manager/reboot", "/manager/reboot"]', self.source)
        restart_start = self.source.index("async function restartComfyUIFromStatus")
        restart_end = self.source.index("\nfunction startKoboldStatusMonitor", restart_start)
        restart = self.source[restart_start:restart_end]
        self.assertIn("for (const endpoint of COMFY_RESTART_ENDPOINTS)", restart)
        self.assertIn("![404, 405].includes(response.status)", restart)

    def test_video_studio_freezes_controls_while_comfyui_is_disconnected(self):
        self.assertIn('const DISCONNECTED_CONTROL_SELECTOR = "input, textarea, select, button"', self.source)
        self.assertIn("disconnectedControls: new Map()", self.source)
        self.assertIn("function freezeDisconnectedControls", self.source)
        self.assertIn('panel.dataset.apiConnected = connected ? "true" : "false"', self.source)
        self.assertIn('setStatus("ComfyUI disconnected — Video Studio is frozen.", "error")', self.source)
        self.assertIn('id="psvstudio-api-connection"', self.source)
        self.assertIn("state.disconnectedControlObserver.observe", self.source)
        self.assertIn('.psvstudio-app[data-api-connected="false"]', self.styles)

    def test_empty_video_studio_offers_complete_default_workflow_setup(self):
        self.assertIn('"default_workflow_setup"', self.routes)
        self.assertIn('/promptstudio-video/default-workflows', self.routes)
        self.assertIn('/promptstudio-video/default-setup', self.routes)
        self.assertIn("async function offerDefaultWorkflowSetup", self.source)
        self.assertIn("Create the default Normal and Turbo workflows?", self.source)
        self.assertIn("install every model and LoRA they currently use", self.source)
        self.assertIn("Missing downloads are resumable.", self.source)
        self.assertIn("await offerDefaultWorkflowSetup()", self.source)
        self.assertIn("overwrite: false", self.source)
        self.assertIn("void pollDefaultSetup(job.id)", self.source)

    def test_director_progress_prefers_live_token_counts(self):
        activity_start = self.source.index("function llmActivityLabel")
        activity_end = self.source.index("function llmGeneratedTokenCount", activity_start)
        activity = self.source[activity_start:activity_end]
        start = self.source.index("function directorJobStatusText")
        end = self.source.index("async function pollDirectorJob", start)
        progress = self.source[start:end]
        self.assertIn('return "Processing"', activity)
        self.assertIn('return "Thinking / processing"', activity)
        self.assertIn("llmGeneratedTokenCount(provider)", progress)
        self.assertIn("tokens received", progress)
        self.assertIn("llmActivityLabel", progress)
        self.assertNotIn("Director is generating", progress)

    def test_director_reports_intent_routing_and_preserves_router_diagnostics(self):
        start = self.source.index("function directorJobStatusText")
        end = self.source.index("async function pollDirectorJob", start)
        progress = self.source[start:end]
        variants_start = self.source.index("function ensureDirectorVariants")
        variants_end = self.source.index("function directorAttachmentMetadata", variants_start)
        variants = self.source[variants_start:variants_end]

        self.assertIn('progress.phase === "intent_classification"', progress)
        self.assertIn("Classifying this turn as a concrete edit or discussion", progress)
        self.assertIn("intent_route", variants)
        self.assertIn("intent_warning", variants)

    def test_status_popover_dismisses_and_live_queue_status_rerenders(self):
        transient_start = self.source.index("function installTransientUiDismissal")
        transient_end = self.source.index("function isVideoStudioControl", transient_start)
        transient = self.source[transient_start:transient_end]
        events_start = self.source.index("function setupProgressEvents")
        events_end = self.source.index("function setStandaloneVisibility", events_start)
        events = self.source[events_start:events_end]
        status_handler = events[events.index('api.addEventListener("status"'):]

        self.assertIn('control?.open && !control.contains(event.target)', transient)
        self.assertIn('closeSystemStatus({ restoreFocus: true })', transient)
        self.assertIn("renderSystemStatusSummary()", status_handler[:400])

    def test_mobile_drawers_have_close_backdrop_escape_and_aria_state(self):
        self.assertIn('id="psvstudio-close-projects"', self.source)
        self.assertIn('class="psvstudio-drawer-scrim"', self.source)
        self.assertIn('aria-controls="psvstudio-projects-drawer"', self.source)
        self.assertIn('aria-controls="psvstudio-shots-drawer"', self.source)
        self.assertIn("function closeVideoDrawer", self.source)
        self.assertIn("closeVideoDrawer({ restoreFocus: true })", self.source)
        self.assertIn('.psvstudio-app[data-drawer="projects"] .psvstudio-drawer-scrim', self.styles)
        self.assertIn('.psvstudio-app[data-drawer="inspector"] .psvstudio-drawer-scrim', self.styles)

    def test_destructive_media_removal_is_confirmed_and_controls_are_accessible(self):
        start = self.source.index("function removeProjectReference")
        end = self.source.index("function mediaLimit", start)
        remove = self.source[start:end]

        self.assertIn("dependentClips", remove)
        self.assertIn("view?.confirm", remove)
        self.assertIn("button:focus-visible", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        self.assertIn('close.ariaLabel = close.title', self.source)
        self.assertIn('aria-label="Director message"', self.source)
        self.assertIn('dialog.setAttribute("aria-labelledby", "psvstudio-director-title")', self.source)
        self.assertGreaterEqual(self.source.count('dialog.setAttribute("aria-label"'), 8)
        self.assertNotIn("function removeSelectedShot", self.source)


if __name__ == "__main__":
    unittest.main()
