"""Exercise actual async route job functions with the shared scheduler mocked."""
import ast
from types import SimpleNamespace
import asyncio
import hashlib
import json
from pathlib import Path
import time
import unittest
import uuid
from aiohttp import web
from video.extension_planner import plan_extension


def route_functions():
    source = Path(__file__).resolve().parents[1] / "routes.py"
    names = {"_prune_director_jobs", "_start_director_job", "_run_director_job", "_cancel_director_job",
             "promptstudio_video_director_status", "promptstudio_video_director_cancel",
             "promptstudio_video_extension_plan_status", "promptstudio_video_extension_plan_cancel"}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    module = ast.Module(body=[node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names], type_ignores=[])
    namespace = {"asyncio": asyncio, "hashlib": hashlib, "json": json, "time": time, "uuid": uuid, "web": web,
                 "DIRECTOR_JOBS": {}, "DIRECTOR_TASKS": set(), "MAX_DIRECTOR_JOBS": 32,
                 "plan_extension": plan_extension, "check_admission": lambda **_kwargs: None}
    fake_ledger = SimpleNamespace(start=lambda *args, **kwargs: None,
                                  update=lambda *args, **kwargs: None,
                                  get=lambda *args, **kwargs: None)
    namespace.update(job_ledger=lambda: fake_ledger, job_status=lambda _id: None)
    exec(compile(module, str(source), "exec"), namespace)
    return namespace


class ExtensionJobsTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_retries_same_id_once_and_retains_progress_and_result(self):
        ns = route_functions()
        calls = []
        def planner(data, progress):
            calls.append(data)
            progress({"phase": "director_generation"})
            return {"valid": True, "document": {"shots": []}}
        async def shared(data, operation, *, cancellation_check, job_context=None):
            self.assertEqual(ns["DIRECTOR_JOBS"]["plan-one"]["status"], "queued")
            self.assertFalse(cancellation_check())
            return operation(data)
        ns.update(plan_extension=planner, run_operation=shared)
        data = {"job_id": "plan-one", "brief": "Continue", "duration_seconds": 5}
        first = ns["_start_director_job"](data, kind="extension_plan")
        self.assertEqual(ns["_start_director_job"](dict(data), kind="extension_plan"), first)
        await ns["DIRECTOR_JOBS"][first]["task"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(ns["DIRECTOR_JOBS"][first]["status"], "complete")
        self.assertEqual(ns["DIRECTOR_JOBS"][first]["director_progress"]["phase"], "director_generation")
        self.assertEqual(ns["_start_director_job"](dict(data), kind="extension_plan"), first)
        with self.assertRaisesRegex(ValueError, "different request"):
            ns["_start_director_job"]({**data, "brief": "Changed"}, kind="extension_plan")

    async def test_cancel_while_waiting_for_shared_slot_never_runs_planner(self):
        ns = route_functions()
        waiting = asyncio.Event()
        async def shared(data, operation, *, cancellation_check, job_context=None):
            waiting.set()
            await asyncio.Future()
        ns.update(run_operation=shared, plan_extension=lambda *args: self.fail("Cancelled planner ran"))
        job_id = ns["_start_director_job"]({"job_id": "cancel-plan"}, kind="extension_plan")
        task = ns["DIRECTOR_JOBS"][job_id]["task"]
        await waiting.wait()
        await ns["_cancel_director_job"](job_id)
        await task
        self.assertEqual(ns["DIRECTOR_JOBS"][job_id]["status"], "cancelled")
        self.assertNotIn("result", ns["DIRECTOR_JOBS"][job_id])

    async def test_failed_plan_is_observable_and_new_retry_can_succeed(self):
        ns = route_functions()
        async def shared(data, operation, **kwargs):
            return operation(data)
        def broken(*args):
            raise ValueError("Late cue outside tail")
        ns.update(run_operation=shared, plan_extension=broken)
        job_id = ns["_start_director_job"]({"job_id": "failed-plan"}, kind="extension_plan")
        await ns["DIRECTOR_JOBS"][job_id]["task"]
        request = type("Request", (), {"match_info": {"job_id": job_id}})()
        response = await ns["promptstudio_video_extension_plan_status"](request)
        self.assertEqual(json.loads(response.body)["error"], "Late cue outside tail")
        ns["plan_extension"] = lambda *args: {"valid": True}
        retry = ns["_start_director_job"]({"job_id": "retry-plan"}, kind="extension_plan")
        await ns["DIRECTOR_JOBS"][retry]["task"]
        self.assertEqual(ns["DIRECTOR_JOBS"][retry]["status"], "complete")

    async def test_plan_endpoints_cannot_cancel_unrelated_director_job(self):
        ns = route_functions()
        ns["DIRECTOR_JOBS"]["director"] = {"kind": "director", "status": "running"}
        request = type("Request", (), {"match_info": {"job_id": "director"}})()
        response = await ns["promptstudio_video_extension_plan_cancel"](request)
        self.assertEqual(response.status, 404)
        self.assertEqual(ns["DIRECTOR_JOBS"]["director"]["status"], "running")
