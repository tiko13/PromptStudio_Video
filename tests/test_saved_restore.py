import copy
import os
import tempfile
import unittest

from video.contracts import default_document
from video.store import normalize_project_store, read_project_store, update_project_store


class SavedRestoreTests(unittest.TestCase):
    def project(self):
        document = default_document()
        snapshot = {"workflow": {"nodes": [{"id": 1}]}, "output": {
            "1": {"class_type": "PSV_MiniMaxH3Director", "inputs": {"seed": 42}},
        }, "metadata": {"untouched": [1, 2]}}
        generation = {"id": "saved", "status": "complete", "document": copy.deepcopy(document),
                      "workflow_id": "missing-workflow", "workflow_snapshot": snapshot,
                      "outputs": [], "created_at": 1, "updated_at": 1}
        return {"id": "project", "name": "Synthetic", "document": document,
                "workflow_id": "missing-workflow", "generations": [generation], "created_at": 1, "updated_at": 1,
                "pending_generation_restore": {"version": 1, "generation": copy.deepcopy(generation),
                    "fingerprint": {"document": copy.deepcopy(document), "workflow_id": "missing-workflow",
                                    "additional_input_selections": {}}}}

    def test_complete_restore_envelope_survives_disk_round_trip_without_aliasing(self):
        project = self.project()
        before = copy.deepcopy(project)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            update_project_store(path, {"revision": 0, "projects": [project], "active_project_id": "project"})
            restored = read_project_store(path)["projects"][0]
        pending = restored["pending_generation_restore"]
        self.assertEqual(pending["generation"]["workflow_snapshot"], before["generations"][0]["workflow_snapshot"])
        self.assertEqual(pending["fingerprint"]["document"], restored["document"])
        self.assertEqual(project, before)
        pending["generation"]["workflow_snapshot"]["output"]["1"]["inputs"]["seed"] = 99
        self.assertEqual(restored["generations"][0]["workflow_snapshot"]["output"]["1"]["inputs"]["seed"], 42)

    def test_invalid_restore_fails_explicitly_instead_of_silently_dropping_it(self):
        for mutation in (
            lambda pending: pending.update(version=2),
            lambda pending: pending["generation"].update(workflow_snapshot={"output": []}),
            lambda pending: pending.update(fingerprint=None),
        ):
            with self.subTest(mutation=mutation):
                project = self.project()
                mutation(project["pending_generation_restore"])
                with self.assertRaises(ValueError):
                    normalize_project_store({"projects": [project]})

    def test_explicit_disarm_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            first = update_project_store(path, {"revision": 0, "projects": [self.project()]})
            project = first["projects"][0]
            project.pop("pending_generation_restore")
            update_project_store(path, {"revision": first["revision"], "projects": [project]})
            self.assertNotIn("pending_generation_restore", read_project_store(path)["projects"][0])


if __name__ == "__main__":
    unittest.main()
