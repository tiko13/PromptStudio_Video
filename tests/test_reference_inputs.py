import tempfile
import unittest
from pathlib import Path

from video.contracts import default_document
from video.store import read_project_store, update_project_store


class WorkflowImageInputTests(unittest.TestCase):
    def test_named_inputs_round_trip_without_qwen_count_limit(self):
        references = {"workflow-a": {f"subgraph:{i}": {"filename": f"image-{i}.png", "type": "input", "subfolder": "refs"} for i in range(12)},
                      "workflow-b": {"subgraph:0": None}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            update_project_store(path, {"revision": 0, "projects": [{
                "id": "inputs", "document": default_document(), "workflowReferences": references,
            }]})
            restored = read_project_store(path)["projects"][0]
            self.assertEqual(restored["workflowReferences"], references)

    def test_invalid_slot_shapes_fail_without_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            with self.assertRaisesRegex(ValueError, "image slots"):
                update_project_store(path, {"revision": 0, "projects": [{
                    "id": "inputs", "document": default_document(), "workflowReferences": {"workflow": []},
                }]})
