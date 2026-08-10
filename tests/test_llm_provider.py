import unittest
from unittest.mock import patch

from video.llm_provider import abort_generation, generate_chat, generation_status


class LlmProviderTests(unittest.TestCase):
    def test_kobold_abort_uses_extra_abort_endpoint(self):
        with patch("video.llm_provider._post_json", return_value={"success": True}) as post:
            result = abort_generation({"kobold_url": "http://localhost:5001"})

        self.assertTrue(result["success"])
        self.assertTrue(post.call_args.args[0].endswith("/api/extra/abort"))
        self.assertEqual(post.call_args.args[1], {})
        self.assertEqual(post.call_args.args[2], 10)

    def test_kobold_abort_preserves_negative_confirmation(self):
        with patch("video.llm_provider._post_json", return_value={"success": False}):
            result = abort_generation({"kobold_url": "http://localhost:5001"})

        self.assertFalse(result["success"])

    def test_kobold_generation_status_reports_live_partial_output_size(self):
        with (
            patch("video.llm_provider._get_json", return_value={"idle": 0, "queue": 1}),
            patch(
                "video.llm_provider._post_json",
                return_value={"results": [{"text": "partial answer"}]},
            ),
        ):
            status = generation_status({"llm_provider": "koboldcpp"})

        self.assertTrue(status["reachable"])
        self.assertTrue(status["busy"])
        self.assertEqual(status["queue"], 1)
        self.assertEqual(status["generated_characters"], len("partial answer"))

    def test_kobold_generation_status_does_not_return_stale_idle_output(self):
        with (
            patch("video.llm_provider._get_json", return_value={"idle": 1, "queue": 0}),
            patch("video.llm_provider._post_json") as post,
        ):
            status = generation_status({"llm_provider": "koboldcpp"})

        self.assertFalse(status["busy"])
        self.assertNotIn("generated_characters", status)
        post.assert_not_called()

    def test_kobold_generation_status_tolerates_malformed_partial_output(self):
        with (
            patch("video.llm_provider._get_json", return_value={"idle": 0, "queue": 0}),
            patch(
                "video.llm_provider._post_json",
                return_value={"results": {"unexpected": "shape"}},
            ),
        ):
            status = generation_status({"llm_provider": "koboldcpp"})

        self.assertTrue(status["reachable"])
        self.assertTrue(status["busy"])
        self.assertIsNone(status["generated_characters"])

    def test_kobold_generation_status_reports_loaded_model_and_vision(self):
        def get_json(url, _timeout):
            if url.endswith("/api/extra/perf"):
                return {"idle": 1, "queue": 0}
            if url.endswith("/api/v1/model"):
                return {"result": "koboldcpp/Qwen2.5-VL-7B-Q4_K_M"}
            if url.endswith("/api/extra/version"):
                return {"vision": True}
            return None

        with patch("video.llm_provider._get_json", side_effect=get_json):
            status = generation_status({"llm_provider": "koboldcpp"})

        self.assertEqual(status["model"], "koboldcpp/Qwen2.5-VL-7B-Q4_K_M")
        self.assertIs(status["vision"], True)

    def test_kobold_auto_response_budget_uses_remaining_context(self):
        posted = []

        def post_json(_url, payload, _timeout, _service_name):
            posted.append(payload)
            if "special" in payload:
                return {"value": 2_000}
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "Complete response"},
                }],
            }

        with (
            patch("video.llm_provider._get_json", side_effect=[{"jinja": True}, {"value": 8_192}]),
            patch("video.llm_provider._post_json", side_effect=post_json),
        ):
            result = generate_chat(
                {"llm_provider": "koboldcpp", "max_response_tokens": 0},
                [{"role": "user", "content": "Compose the full prompt."}],
            )

        self.assertEqual(result, "Complete response")
        self.assertEqual(posted[-1]["max_tokens"], 8_192 - 2_000 - 32)

    def test_ollama_auto_response_budget_fills_context(self):
        with patch("video.llm_provider._post_json") as post:
            post.return_value = {
                "message": {"content": "Complete response"},
                "done_reason": "stop",
            }
            result = generate_chat(
                {"llm_provider": "ollama", "ollama_model": "local-model", "max_response_tokens": 0},
                [{"role": "user", "content": "Compose the full prompt."}],
            )

        self.assertEqual(result, "Complete response")
        self.assertEqual(post.call_args.args[1]["options"]["num_predict"], -2)


if __name__ == "__main__":
    unittest.main()
