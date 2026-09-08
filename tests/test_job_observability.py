import asyncio
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_extension_jobs import route_functions


path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "job_observability.py"
spec = importlib.util.spec_from_file_location("video_test_job_observability", path)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class VideoJobObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_returns_terminal_status_and_prevents_silent_inference_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.sqlite3"
            ledger = shared.JobLedger(path)
            ledger.start("old-job", studio="video", kind="extension_plan", data={"origin": {"project_id": "original"}})
            ledger.close()
            recovered = shared.JobLedger(path)
            ns = route_functions()
            ns["job_ledger"] = lambda: recovered
            with patch.object(shared, "shared_job_ledger", return_value=recovered):
                ns["job_status"] = lambda job_id: shared.shared_job_status(job_id, studio="video")
                response = await ns["promptstudio_video_extension_plan_status"](SimpleNamespace(match_info={"job_id": "old-job"}))
                body = __import__("json").loads(response.text)
                self.assertEqual(body["status"], "failed")
                self.assertEqual(body["job"]["state"], "interrupted")
                self.assertEqual(body["job"]["origin"], {"project_id": "original"})
                self.assertEqual(body["retry_action"], "replan")
                with self.assertRaises(shared.JobAlreadyRecorded):
                    ns["_start_director_job"]({"job_id": "old-job"}, kind="extension_plan")
                self.assertFalse(ns["DIRECTOR_TASKS"])
            recovered.close()

    async def test_director_progress_retains_safe_stages_and_original_project(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = shared.JobLedger(Path(directory) / "metadata.sqlite3")
            ns = route_functions()
            ns["job_ledger"] = lambda: ledger
            async def run(data, operation, **_kwargs):
                return operation(data)
            def director(_data, progress):
                progress({"phase": "intent_classification", "private": "SECRET_PRIVATE_PROMPT"})
                progress({"phase": "director_generation"})
                return {"message": "SECRET_PRIVATE_OUTPUT"}
            ns.update(run_operation=run, director_chat=director)
            ns["_start_director_job"]({"job_id": "job", "origin": {"project_id": "original-project"}})
            await asyncio.gather(*ns["DIRECTOR_TASKS"])
            envelope = ledger.get("job", studio="video")
            self.assertEqual(envelope["state"], "complete")
            self.assertEqual(envelope["origin"]["project_id"], "original-project")
            self.assertIn("routing", envelope["stage_ms"])
            self.assertIn("generation", envelope["stage_ms"])
            self.assertNotIn("SECRET", __import__("json").dumps(ledger.diagnostic_export()))
            ledger.close()


if __name__ == "__main__":
    unittest.main()
