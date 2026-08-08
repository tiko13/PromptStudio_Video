import json
import os
from pathlib import Path
import tempfile
import unittest

from video.contracts import default_document
from video.store import (
    StoreConflictError,
    read_project_store,
    read_workflow_store,
    update_project_store,
    update_workflow_store,
)


class StoreTests(unittest.TestCase):
    def test_project_store_round_trip_and_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            empty = read_project_store(path)
            self.assertEqual(empty["revision"], 0)
            project = {
                "id": "project-1",
                "name": "Opening scene",
                "brief": "A baker opens the shutters.",
                "document": default_document(),
                "workflow_id": "[PSV] MiniMax.json",
                "generations": [],
                "created_at": 1,
                "updated_at": 1,
            }
            project["document"]["shots"][0]["steps"] = [
                {"type": "action", "text": project["brief"]},
            ]
            saved = update_project_store(path, {
                "version": 1,
                "revision": 0,
                "active_project_id": project["id"],
                "projects": [project],
            })
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(saved["version"], 2)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(len(list(Path(directory, "projects").glob("project_*.json"))), 1)
            restored_project = read_project_store(path)["projects"][0]
            self.assertEqual(restored_project["document"]["resolved_mode"], "t2va")
            self.assertEqual(restored_project["document"]["main_description"], project["brief"])
            self.assertEqual(restored_project["brief"], project["brief"])
            self.assertEqual(
                restored_project["document"]["shots"][0]["steps"][0]["text"],
                project["brief"],
            )
            with self.assertRaises(StoreConflictError):
                update_project_store(path, {
                    "version": 1,
                    "revision": 0,
                    "active_project_id": project["id"],
                    "projects": [project],
                })

    def test_project_store_uses_one_json_file_per_session_and_archives_deleted_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            project_directory = Path(directory, "projects")
            projects = [
                {
                    "id": project_id,
                    "name": project_id,
                    "brief": "",
                    "document": default_document(),
                    "workflow_id": "",
                    "generations": [],
                    "created_at": index,
                    "updated_at": index,
                }
                for index, project_id in enumerate(("project-one", "project-two"), 1)
            ]
            first = update_project_store(path, {
                "version": 2,
                "revision": 0,
                "active_project_id": "project-two",
                "projects": projects,
            })
            index = json.loads((project_directory / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["projectFiles"]), 2)
            self.assertEqual(len(list(project_directory.glob("project_*.json"))), 2)

            saved = update_project_store(path, {
                "version": 2,
                "revision": first["revision"],
                "active_project_id": "project-one",
                "projects": [projects[0]],
            })
            self.assertEqual(saved["revision"], 2)
            self.assertEqual(len(list(project_directory.glob("project_*.json"))), 1)
            self.assertEqual(len(list((project_directory / "_backups").glob("project_*.bak"))), 1)
            self.assertEqual([project["id"] for project in read_project_store(path)["projects"]], ["project-one"])

    def test_project_store_migrates_legacy_monolith(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "projects.json")
            project = {
                "id": "project-one",
                "name": "Legacy session",
                "brief": "",
                "document": default_document(),
                "workflow_id": "",
                "generations": [],
                "created_at": 1,
                "updated_at": 1,
            }
            path.write_text(json.dumps({
                "version": 1,
                "revision": 7,
                "active_project_id": project["id"],
                "projects": [project],
            }), encoding="utf-8")

            restored = read_project_store(str(path))

            project_directory = Path(directory, "projects")
            self.assertEqual(restored["version"], 2)
            self.assertEqual(restored["revision"], 7)
            self.assertFalse(path.exists())
            self.assertTrue((project_directory / "index.json").is_file())
            self.assertEqual(len(list(project_directory.glob("project_*.json"))), 1)
            self.assertTrue((project_directory / "_backups" / "legacy_store.bak").is_file())

    def test_generation_snapshot_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            document = default_document()
            generation = {
                "id": "generation-1",
                "prompt_id": "prompt-1",
                "status": "complete",
                "document": document,
                "workflow_snapshot": {"output": {"1": {"class_type": "SaveVideo", "inputs": {}}}},
                "outputs": [{"filename": "video.mp4", "subfolder": "video", "type": "output"}],
                "created_at": 2,
                "updated_at": 3,
            }
            update_project_store(path, {
                "version": 1,
                "revision": 0,
                "active_project_id": "project-1",
                "projects": [{
                    "id": "project-1", "name": "Replay", "brief": "", "document": document,
                    "workflow_id": "", "generations": [generation], "created_at": 1, "updated_at": 3,
                }],
            })
            restored = read_project_store(path)["projects"][0]["generations"][0]
            self.assertEqual(restored["workflow_snapshot"], generation["workflow_snapshot"])
            self.assertEqual(restored["outputs"][0]["filename"], "video.mp4")

    def test_workflow_store_requires_director_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "workflows.json")
            snapshot = {"output": {
                "6": {"class_type": "PSV_MiniMaxH3Director", "inputs": {"document_json": "{}"}},
                "19": {"class_type": "SaveVideo", "inputs": {}},
            }}
            saved = update_workflow_store(path, {
                "version": 1,
                "revision": 0,
                "templates": [{
                    "path": "[PSV] MiniMax.json",
                    "name": "[PSV] MiniMax",
                    "director_node_id": "6",
                    "result_node_ids": ["19"],
                    "result_fields": ["videos"],
                    "snapshot": snapshot,
                }],
            })
            self.assertEqual(saved["revision"], 1)
            restored = read_workflow_store(path)
            self.assertEqual(restored["templates"][0]["director_node_id"], "6")


if __name__ == "__main__":
    unittest.main()
