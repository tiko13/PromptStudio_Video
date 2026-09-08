import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from video.contracts import default_document
from video import store


class ProjectStoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "projects.json")
        self.directory = Path(self.temp.name) / "projects"
        self.projects = [{"id": key, "name": "old " + key, "document": default_document(),
                          "created_at": 1, "updated_at": 1, "generations": []} for key in ("a", "b")]
        self.first = store.update_project_store(self.path, {
            "revision": 0, "active_project_id": "b", "projects": self.projects,
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_failure_on_second_project_never_exposes_mixed_revision(self):
        service = store.shared_transactional_store()
        commit = service.commit_records
        def fault(stage, record_id=None):
            if stage == "before_record" and record_id == "b":
                raise OSError("second project failed")
        def interrupted(*args, **kwargs):
            return commit(*args, **kwargs, fault=fault)
        update = copy.deepcopy(self.first)
        for project in update["projects"]:
            project["name"] = "new " + project["id"]
        with mock.patch.object(service, "commit_records", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "second project failed"):
                store.update_project_store(self.path, update)
        self.assertEqual(store.read_project_store(self.path), self.first)

    def test_corruption_returns_complete_previous_revision_and_blocks_saves(self):
        update = copy.deepcopy(self.first)
        for project in update["projects"]:
            project["name"] = "new " + project["id"]
        store.update_project_store(self.path, update)
        index = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))
        damaged = self.directory / index["projectFiles"][1]["file"]
        damaged.write_bytes(b"broken project")
        recovered = store.read_project_store(self.path)
        self.assertEqual(recovered["projects"], self.first["projects"])
        self.assertEqual(recovered["revision"], 1)
        self.assertIn("recovery", recovered)
        with self.assertRaisesRegex(RuntimeError, "explicit recovery"):
            store.update_project_store(self.path, recovered)
        store.shared_transactional_store().recover_store(self.directory, "projectFiles", "project")
        self.assertEqual(store.read_project_store(self.path)["revision"], 3)
        self.assertEqual(damaged.read_bytes(), b"broken project")

    def test_concurrent_updates_are_serialized_around_revision_validation(self):
        start = threading.Barrier(2)
        results = []
        def writer(label):
            update = copy.deepcopy(self.first)
            update["projects"][0]["name"] = label
            start.wait()
            try:
                results.append(store.update_project_store(self.path, update)["revision"])
            except store.StoreConflictError:
                results.append("conflict")
        threads = [threading.Thread(target=writer, args=(label,)) for label in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, [2, "conflict"])

    def test_generation_lineage_and_workflow_envelope_survive_revisions(self):
        update = copy.deepcopy(self.first)
        snapshot = {"workflow": {"nodes": [{"id": 1}]}, "output": {"1": {"class_type": "SaveVideo", "inputs": {}}}}
        update["projects"][0]["generations"] = [
            {"id": "parent", "status": "complete", "workflow_snapshot": snapshot},
            {"id": "child", "status": "complete", "parent_generation_id": "parent", "workflow_snapshot": snapshot},
        ]
        saved = store.update_project_store(self.path, update)
        restored = store.read_project_store(self.path)
        self.assertEqual(restored, saved)
        child = restored["projects"][0]["generations"][1]
        self.assertEqual(child["root_generation_id"], "parent")
        self.assertEqual(child["depth"], 1)
        self.assertEqual(child["workflow_snapshot"], snapshot)


if __name__ == "__main__":
    unittest.main()
