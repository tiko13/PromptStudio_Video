import tempfile
from pathlib import Path
import unittest
from video.contracts import default_document
from video.store import update_project_store, read_project_store


class HistoryRetentionTests(unittest.TestCase):
    def test_201st_generation_preserves_first_and_immutable_envelope(self):
        generations = [{"id":f"g{i}", "status":"complete", "created_at":i + 1,
                        "workflow_snapshot":{"workflow":{"marker":i},"output":{"1":{"class_type":"Example","inputs":{"seed":i}}}},
                        "outputs":[]} for i in range(201)]
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "projects.json")
            update_project_store(path, {"revision":0,"projects":[{"id":"project", "name":"Retained history",
                "document":default_document(),"generations":generations,"created_at":1,"updated_at":202}]})
            restored = read_project_store(path)["projects"][0]["generations"]
            self.assertEqual(len(restored),201)
            self.assertEqual({item["id"] for item in restored},{f"g{i}" for i in range(201)})
            self.assertEqual(next(item for item in restored if item["id"]=="g0")["workflow_snapshot"],generations[0]["workflow_snapshot"])


if __name__ == "__main__": unittest.main()
