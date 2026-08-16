import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import video.llm_provider as llm_provider
from video.llm_provider import _primary_routes, abort_generation, generate_chat, generation_status


class LlmProviderTests(unittest.TestCase):
    def test_generate_chat_delegates_complete_director_request_to_primary_prompt_studio(self):
        shared = SimpleNamespace(
            shared_llm_generate=Mock(return_value="Complete response"),
            shared_llm_status=Mock(),
            shared_llm_abort=Mock(),
        )
        settings = {
            "llm_provider": "llamacpp",
            "llamacpp_url": "http://127.0.0.1:8080",
            "llamacpp_model": "test.gguf",
            "thinking_mode": "Medium",
            "llamacpp_reasoning_budget_tokens": 1000,
        }
        messages = [{"role": "user", "content": "Compose the full prompt."}]
        images = [{"data_uri": "data:image/png;base64,AAAA", "base64": "AAAA"}]

        with patch("video.llm_provider._primary_routes", return_value=shared):
            result = generate_chat(settings, messages, images)

        self.assertEqual(result, "Complete response")
        shared.shared_llm_generate.assert_called_once_with(settings, messages, images)

    def test_generation_status_uses_primary_live_token_service(self):
        expected = {
            "provider": "llamacpp",
            "reachable": True,
            "busy": True,
            "generated_tokens": 37,
            "generation_phase": "thinking_or_generating",
        }
        shared = SimpleNamespace(
            shared_llm_generate=Mock(),
            shared_llm_status=Mock(return_value=expected),
            shared_llm_abort=Mock(),
        )
        settings = {"llm_provider": "llamacpp"}

        with patch("video.llm_provider._primary_routes", return_value=shared):
            result = generation_status(settings)

        self.assertIs(result, expected)
        shared.shared_llm_status.assert_called_once_with(settings)

    def test_abort_generation_uses_primary_provider_abort(self):
        expected = {"provider": "llamacpp", "success": True, "closed_streams": 1}
        shared = SimpleNamespace(
            shared_llm_generate=Mock(),
            shared_llm_status=Mock(),
            shared_llm_abort=Mock(return_value=expected),
        )
        settings = {"llm_provider": "llamacpp", "llamacpp_url": "http://127.0.0.1:8080"}

        with patch("video.llm_provider._primary_routes", return_value=shared):
            result = abort_generation(settings)

        self.assertIs(result, expected)
        shared.shared_llm_abort.assert_called_once_with(settings)

    def test_missing_primary_prompt_studio_has_actionable_error(self):
        with (
            patch.object(llm_provider.sys, "modules", {}),
            patch.object(llm_provider.importlib, "import_module", side_effect=ModuleNotFoundError),
        ):
            with self.assertRaisesRegex(RuntimeError, "requires the companion ComfyUI_PromptStudio"):
                _primary_routes()


if __name__ == "__main__":
    unittest.main()
