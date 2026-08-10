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

    def test_generation_continuation_lineage_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            document = default_document()
            base = {
                "id": "generation-base", "prompt_id": "prompt-base", "status": "complete",
                "document": document, "outputs": [{"filename": "base.mp4", "type": "output"}],
                "effective_duration": 5.17, "created_at": 1, "updated_at": 1,
            }
            extension = {
                "id": "generation-extension", "prompt_id": "prompt-extension", "status": "complete",
                "kind": "extension", "parent_generation_id": "generation-base",
                "document": document, "outputs": [{"filename": "cumulative.mp4", "type": "output"}],
                "segment_outputs": [{"filename": "segment.mp4", "type": "output"}],
                "effective_duration": 5.17, "total_effective_duration": 10.34,
                "context_latent_path": "video/PromptStudio_Video/latents/project-1/generation-extension.safetensors",
                "continuation": {"engine": "native_h3_motion_context", "context_frames": 22},
                "created_at": 2, "updated_at": 2,
            }
            update_project_store(path, {
                "version": 2, "revision": 0, "active_project_id": "project-1",
                "projects": [{
                    "id": "project-1", "name": "Continuation", "brief": "", "document": document,
                    "workflow_id": "", "generations": [extension, base], "created_at": 1, "updated_at": 2,
                }],
            })

            generations = read_project_store(path)["projects"][0]["generations"]
            restored_extension = next(item for item in generations if item["id"] == "generation-extension")
            self.assertEqual(restored_extension["root_generation_id"], "generation-base")
            self.assertEqual(restored_extension["depth"], 1)
            self.assertEqual(restored_extension["kind"], "extension")
            self.assertEqual(restored_extension["segment_outputs"][0]["filename"], "segment.mp4")
            self.assertTrue(restored_extension["context_latent_path"].endswith(".safetensors"))

    def test_pruned_continuation_parent_preserves_saved_lineage_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "projects.json")
            document = default_document()
            extension = {
                "id": "generation-late", "prompt_id": "prompt-late", "status": "complete",
                "kind": "extension", "parent_generation_id": "generation-pruned",
                "root_generation_id": "generation-original", "depth": 27,
                "document": document, "outputs": [{"filename": "cumulative.mp4", "type": "output"}],
                "segment_outputs": [{"filename": "segment.mp4", "type": "output"}],
                "continuation": {
                    "source_segments": [{"filename": "base.mp4", "type": "output"}],
                },
                "created_at": 27, "updated_at": 27,
            }
            update_project_store(path, {
                "version": 2, "revision": 0, "active_project_id": "project-1",
                "projects": [{
                    "id": "project-1", "name": "Long continuation", "brief": "",
                    "document": document, "workflow_id": "", "generations": [extension],
                    "created_at": 1, "updated_at": 27,
                }],
            })

            restored = read_project_store(path)["projects"][0]["generations"][0]
            self.assertEqual(restored["root_generation_id"], "generation-original")
            self.assertEqual(restored["depth"], 27)
            self.assertEqual(restored["kind"], "extension")

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
