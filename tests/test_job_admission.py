import ast
import asyncio
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid


class Overload(RuntimeError):
    code = "llm_overloaded"
    retryable = True
    status = 503


def functions():
    path = Path(__file__).resolve().parents[1] / "routes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_prune_director_jobs", "_start_director_job", "_llm_error_response",
             "_shutdown_director_jobs", "promptstudio_video_director_chat"}
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    def admission(*, local_full=False):
        if local_full:
            raise Overload("capacity full")
    ns = dict(asyncio=asyncio, hashlib=hashlib, json=json, time=time, uuid=uuid,
              DIRECTOR_JOBS={}, DIRECTOR_TASKS=set(), MAX_DIRECTOR_JOBS=2,
              check_admission=Mock(side_effect=admission), PromptDocumentError=ValueError,
              web=SimpleNamespace(json_response=lambda body, status=200: (body, status)))
    fake_ledger = SimpleNamespace(start=lambda *args, **kwargs: None,
                                  update=lambda *args, **kwargs: None,
                                  get=lambda *args, **kwargs: None)
    ns.update(job_ledger=lambda: fake_ledger, job_status=lambda _id: None)
    exec(compile(tree, str(path), "exec"), ns)
    return ns


class JobAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_jobs_reject_with_retryable_503_and_keep_existing_ids(self):
        ns = functions()
        ns["DIRECTOR_JOBS"].update({str(i): {"status": "running", "created_at": 1} for i in range(2)})
        ns["_director_body"] = AsyncMock(return_value={"async": True})
        body, status = await ns["promptstudio_video_director_chat"](None)
        self.assertEqual(status, 503)
        self.assertEqual(body["code"], "llm_overloaded")
        self.assertTrue(body["retryable"])
        self.assertEqual(len(ns["DIRECTOR_JOBS"]), 2)
        self.assertEqual(ns["_start_director_job"]({"job_id": "0"}), "0")

    async def test_terminal_job_pruning_admits_a_new_job(self):
        ns = functions()
        ns["DIRECTOR_JOBS"].update({str(i): {"status": "complete", "created_at": 1} for i in range(2)})
        ns["_run_director_job"] = AsyncMock()
        ns["_start_director_job"]({"job_id": "new"})
        await asyncio.gather(*ns["DIRECTOR_TASKS"])
        self.assertIn("new", ns["DIRECTOR_JOBS"])
        self.assertEqual(len(ns["DIRECTOR_JOBS"]), 2)

    async def test_cleanup_cancels_tasks_and_marks_job(self):
        ns = functions()
        ns["DIRECTOR_JOBS"]["job"] = {"status": "running"}
        task = asyncio.create_task(asyncio.sleep(30))
        ns["DIRECTOR_TASKS"].add(task)
        await ns["_shutdown_director_jobs"](None)
        self.assertTrue(task.cancelled())
        self.assertTrue(ns["DIRECTOR_JOBS"]["job"]["cancelled"])
        self.assertEqual(ns["DIRECTOR_JOBS"]["job"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
