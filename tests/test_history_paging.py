import copy
import json
from pathlib import Path
import statistics
import tempfile
import time
import unittest
from unittest import mock

from video.contracts import default_document
from video import store


def synthetic_projects(count):
    return [{"id": f"project-{index:04d}", "name": f"Project {index}", "created_at": index + 1,
             "updated_at": index + 1, "document": default_document(),
             "generations": [{"id": "generation", "status": "complete", "created_at": 1, "updated_at": 1,
                              "workflow_snapshot": {"workflow": {"nodes": [], "payload": "x" * 16384}, "output": {"1": {"inputs": {}}}}}]}
            for index in range(count)]


class ProjectHistoryPagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "projects.json")
        self.directory = str(Path(self.temp.name) / "projects")
        self.saved = store.update_project_store(self.path, {"revision": 0, "active_project_id": "project-0000", "projects": synthetic_projects(100)}, self.directory)
        self.service = store.shared_transactional_store()

    def tearDown(self):
        self.temp.cleanup()

    def reads(self):
        return mock.patch.object(self.service, "_read_record_bytes", wraps=self.service._read_record_bytes)

    def test_revision_and_summaries_read_no_generation_histories(self):
        with self.reads() as reads:
            _, status = store.read_project_query(self.path, self.directory, {"revision": "1"})
            self.assertEqual(status, 204)
            page, status = store.read_project_query(self.path, self.directory, {"limit": "20", "summaries": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(page["projects"], [])
        self.assertEqual(len(page["summaries"]), 20)
        reads.assert_not_called()

    def test_detail_and_page_load_only_selected_projects(self):
        with self.reads() as reads:
            detail, status = store.read_project_query(self.path, self.directory, {"project_id": "project-0003"})
            self.assertEqual(reads.call_count, 1)
            reads.reset_mock()
            page, status = store.read_project_query(self.path, self.directory, {"limit": "20"})
            self.assertEqual(reads.call_count, 20)
        self.assertEqual(detail["projects"][0]["generations"], self.saved["projects"][3]["generations"])
        self.assertEqual(page["projects"][0]["id"], "project-0099")
        self.assertEqual(page["active_project_id"], "project-0000")

    def test_one_project_edit_retains_unrelated_records_without_reading_them(self):
        changed = copy.deepcopy(self.saved["projects"][3])
        changed["name"] = "Changed"
        before = store._read_project_index(self.directory)
        with self.reads() as reads:
            result = store.update_project_store(self.path, {"revision": 1, "partial": True, "projects": [changed]}, self.directory)
        reads.assert_not_called()
        self.assertEqual(result["revision"], 2)
        after = store._read_project_index(self.directory)
        self.assertEqual(before["projectFiles"][4], after["projectFiles"][4])
        self.assertNotEqual(before["projectFiles"][3]["file"], after["projectFiles"][3]["file"])
        self.assertEqual(len(after["projectFiles"]), 100)

    def test_initial_page_also_hydrates_older_pending_generations(self):
        changed = copy.deepcopy(self.saved["projects"][3])
        changed["generations"][0]["status"] = "queued"
        store.update_project_store(self.path, {"revision": 1, "partial": True, "projects": [changed]}, self.directory)
        page, _ = store.read_project_query(self.path, self.directory, {"limit": "20", "include_active": "1", "include_pending": "1"})
        self.assertEqual(len(page["projects"]), 22)
        self.assertIn("project-0003", [project["id"] for project in page["projects"]])

    def test_delete_wins_over_incoming_record_and_keeps_cursor_stable(self):
        page, _ = store.read_project_query(self.path, self.directory, {"limit": "20", "summaries": "1"})
        store.update_project_store(self.path, {"revision": 1, "partial": True, "projects": [self.saved["projects"][90]],
                                              "deletedProjectIds": ["project-0090"]}, self.directory)
        cursor = page["nextCursor"]
        next_page, _ = store.read_project_query(self.path, self.directory, {"limit": "20", "summaries": "1", "before_updated": str(cursor["updated_at"]),
                                                                          "before_created": str(cursor["created_at"]), "before_id": cursor["id"]})
        self.assertEqual(next_page["summaries"][0]["id"], "project-0079")
        self.assertNotIn("project-0090", [entry["id"] for entry in store._read_project_index(self.directory)["projectFiles"]])

    def test_legacy_summary_upgrade_is_an_explicit_bounded_batch(self):
        path = Path(self.directory) / "index.json"
        index = json.loads(path.read_text(encoding="utf-8"))
        for entry in index["projectFiles"]:
            entry.pop("summary")
        path.write_text(json.dumps(index), encoding="utf-8")
        with self.reads() as reads:
            page, status = store.read_project_query(self.path, self.directory, {"summaries": "1"})
        self.assertEqual(status, 202)
        self.assertTrue(page["maintenance_required"])
        reads.assert_not_called()
        with self.reads() as reads:
            result = store.maintain_project_store(self.path, self.directory, 0, 20)
        self.assertEqual(reads.call_count, 20)
        self.assertEqual(result["processed"], 20)

    def test_valid_workflow_cache_identity_is_preserved_and_invalid_is_omitted(self):
        identity = {"version": 1, "adapterId": "minimax_h3", "adapterVersion": 1, "conversionVersion": 1,
                    "inputVersion": 3, "contentHash": "a" * 64, "capabilityHash": "b" * 64}
        value = {"path": "[PSV] test.json", "director_node_id": "1", "result_node_ids": ["2"],
                 "snapshot": {"output": {"1": {"class_type": "PSV_MiniMaxH3Director"}, "2": {"class_type": "SaveVideo"}}},
                 "cacheIdentity": identity}
        normalized = store._normalize_workflow(value, 0)
        self.assertEqual(normalized["cacheIdentity"], identity)
        self.assertIsNot(normalized["cacheIdentity"], identity)
        self.assertNotIn("cacheIdentity", store._normalize_workflow({**value, "cacheIdentity": {**identity, "contentHash": "invalid"}}, 0))

    def test_corrupt_detail_recovers_a_whole_previous_revision(self):
        changed = copy.deepcopy(self.saved["projects"][3])
        changed["name"] = "Changed"
        store.update_project_store(self.path, {"revision": 1, "partial": True, "projects": [changed]}, self.directory)
        index = store._read_project_index(self.directory)
        entry = next(entry for entry in index["projectFiles"] if entry["id"] == changed["id"])
        (Path(self.directory) / entry["file"]).write_bytes(b"corrupt")
        detail, status = store.read_project_query(self.path, self.directory, {"project_id": changed["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(detail["revision"], 1)
        self.assertIn("recovery", detail)
        self.assertEqual(detail["projects"][0], self.saved["projects"][3])

    def test_synthetic_100_and_1000_project_measurements(self):
        measurements = []
        for count in (100, 1000):
            with tempfile.TemporaryDirectory() as directory, mock.patch.object(store, "MAX_PROJECTS", 1000):
                path = str(Path(directory) / "projects.json")
                target = str(Path(directory) / "projects")
                data = store.update_project_store(path, {"revision": 0, "active_project_id": "project-0000", "projects": synthetic_projects(count)}, target)
                store.read_project_query(path, target, {"summaries": "1", "limit": "20"})
                timings = []
                with self.reads() as reads:
                    for _ in range(25):
                        started = time.perf_counter()
                        page, _ = store.read_project_query(path, target, {"summaries": "1", "limit": "20"})
                        timings.append((time.perf_counter() - started) * 1000)
                self.assertEqual(reads.call_count, 0)
                self.assertEqual(len(page["summaries"]), 20)
                measurements.append({"records": count, "summary_records_opened": reads.call_count,
                                     "summary_response_bytes": len(json.dumps(page).encode()),
                                     "single_edit_request_bytes": len(json.dumps({"revision": 1, "partial": True, "projects": [data["projects"][0]]}).encode()),
                                     "full_store_request_bytes": len(json.dumps(data).encode()),
                                     "summary_p50_ms": round(statistics.median(timings), 3),
                                     "summary_p95_ms": round(sorted(timings)[23], 3)})
        print("VIDEO_HISTORY_BENCHMARK " + json.dumps(measurements))


if __name__ == "__main__":
    unittest.main()
