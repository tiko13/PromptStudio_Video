"""Verify route admission/cancellation through the companion scheduler adapter."""
import ast
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


def route_functions():
    source = Path(__file__).resolve().parents[1] / "routes.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"_run_director_job", "_cancel_director_job", "promptstudio_video_director_chat"}
    tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    namespace = {
        "asyncio": asyncio, "json": json, "time": time,
        "DIRECTOR_JOBS": {}, "PromptDocumentError": ValueError,
        "web": SimpleNamespace(json_response=lambda value, **kwargs: value),
        "abort_generation": Mock(),
    }
    fake_ledger = SimpleNamespace(start=lambda *args, **kwargs: None,
                                  update=lambda *args, **kwargs: None,
                                  get=lambda *args, **kwargs: None)
    namespace.update(job_ledger=lambda: fake_ledger, job_status=lambda _id: None)
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace


class SharedSchedulerRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_director_turn_schedules_one_complete_operation(self):
        ns = route_functions()
        ns["DIRECTOR_JOBS"]["job"] = {"status": "queued"}
        ns["director_chat"] = Mock(return_value={"message": "done"})

        async def run(data, operation, **kwargs):
            self.assertEqual(ns["DIRECTOR_JOBS"]["job"]["status"], "queued")
            self.assertFalse(kwargs["cancellation_check"]())
            return operation(data)

        ns["run_operation"] = AsyncMock(side_effect=run)
        await ns["_run_director_job"]("job", {"llm_provider": "ollama"})
        ns["run_operation"].assert_awaited_once()
        ns["director_chat"].assert_called_once()
        self.assertEqual(ns["DIRECTOR_JOBS"]["job"]["status"], "complete")
        self.assertEqual(ns["DIRECTOR_JOBS"]["job"]["result"], {"message": "done"})

    async def test_sync_http_director_turn_uses_same_scheduler(self):
        ns = route_functions()
        data = {"llm_provider": "koboldcpp"}
        ns["_director_body"] = AsyncMock(return_value=data)
        ns["director_chat"] = Mock()
        ns["run_operation"] = AsyncMock(return_value={"message": "done"})
        self.assertEqual(await ns["promptstudio_video_director_chat"](object()), {"message": "done"})
        ns["run_operation"].assert_awaited_once_with(data, ns["director_chat"])

    async def test_running_cancel_cancels_operation_without_endpoint_force_stop(self):
        ns = route_functions()
        entered = asyncio.Event()

        async def run(_data, _operation, **kwargs):
            ns["DIRECTOR_JOBS"]["job"]["status"] = "running"
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.assertTrue(kwargs["cancellation_check"]())
                raise

        ns["run_operation"] = AsyncMock(side_effect=run)
        ns["director_chat"] = Mock()
        ns["DIRECTOR_JOBS"]["job"] = {"status": "queued"}
        task = asyncio.create_task(ns["_run_director_job"]("job", {}))
        ns["DIRECTOR_JOBS"]["job"]["task"] = task
        await entered.wait()
        result = await ns["_cancel_director_job"]("job")
        await task
        self.assertEqual(result, {"job_id": "job", "status": "cancelled", "provider_aborted": False})
        ns["abort_generation"].assert_not_called()
        ns["director_chat"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
