"""Readable workflow source and release contract checks; no models are loaded."""
import ast
import io
import json
from pathlib import Path
import re
import unittest
import zipfile

from video import default_setup
from video.continuation import CONTINUATION_CONTEXT_FRAMES
from video.contracts import normalize_document

ROOT = Path(__file__).resolve().parents[1]


class WorkflowPackagingTests(unittest.TestCase):
    def test_bundled_director_documents_have_no_example_content_or_media(self):
        for name in default_setup.DEFAULT_WORKFLOW_NAMES:
            workflow = default_setup.load_bundled_workflow(name)
            director = next(n for n in workflow["nodes"] if n["type"] == "PSV_MiniMaxH3Director")
            values = [director["widgets_values"][0]]
            if "widgets_values_named" in director:
                values.append(director["widgets_values_named"]["document_json"])
            for value in values:
                with self.subTest(name=name):
                    document = json.loads(value)
                    normalized = normalize_document(document)
                    self.assertEqual(normalized["duration_seconds"], document["duration_seconds"])
                    self.assertEqual(normalized["width"], document["width"])
                    self.assertEqual(normalized["height"], document["height"])
                    for field in ("style", "main_description", "prompt_override", "overall_soundscape", "summary",
                                  "references", "subject_definitions", "retention_analysis"):
                        self.assertFalse(document.get(field), field)
                    for shot in document["shots"]:
                        for field in ("composition", "subjects", "environment", "lighting", "action", "notes",
                                      "dialogue", "visible_text", "sounds"):
                            self.assertFalse(shot.get(field), field)
            self.assertNotIn("ds", workflow.get("extra", {}))
            output = next(n for n in workflow["nodes"] if n["type"] == "SaveVideo")
            self.assertEqual(output["widgets_values"][0], "video/PromptStudio_Video/MiniMaxH3")

    def test_readable_sources_are_canonical_and_round_trip_without_compression(self):
        self.assertEqual(len(default_setup.DEFAULT_WORKFLOW_NAMES), 2)
        for name in default_setup.DEFAULT_WORKFLOW_NAMES:
            with self.subTest(name=name):
                source = (ROOT / "workflows" / name).read_text(encoding="utf-8")
                workflow = default_setup.load_bundled_workflow(name)
                self.assertEqual(source, default_setup.serialize_workflow_source(workflow))
                self.assertEqual(workflow, json.loads(default_setup.serialize_workflow_source(workflow)))
                mutated = default_setup.load_bundled_workflow(name)
                mutated["nodes"].clear()
                self.assertTrue(default_setup.load_bundled_workflow(name)["nodes"])
        with self.assertRaises(ValueError):
            default_setup.load_bundled_workflow("../private.json")

    def test_source_archive_contains_native_nodes_and_no_private_absolute_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in default_setup.DEFAULT_WORKFLOW_NAMES:
                bundle.writestr("workflows/" + name, default_setup.serialize_workflow_source(default_setup.load_bundled_workflow(name)))
        archive.seek(0)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(len(bundle.namelist()), 2)
            for name in bundle.namelist():
                workflow = json.loads(bundle.read(name))
                nodes = workflow["nodes"]
                self.assertEqual(sum(node["type"] == "PSV_MiniMaxH3Director" for node in nodes), 1)
                self.assertEqual(sum(node["type"] == "SaveVideo" for node in nodes), 1)
                self.assertEqual(sum(node["type"] == "PSV_MiniMaxH3TurboProfile" for node in nodes), int("Turbo" in name))
                self.assertEqual(workflow["version"], 0.4)
                def check(value):
                    if isinstance(value, dict):
                        for item in value.values():
                            check(item)
                    elif isinstance(value, list):
                        for item in value:
                            check(item)
                    elif isinstance(value, str):
                        self.assertIsNone(re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})", value), value)
                check(workflow)

    def test_continuation_capability_constants_and_architecture_agree(self):
        def assignment(path, name):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
        self.assertEqual(CONTINUATION_CONTEXT_FRAMES, 39)
        self.assertEqual(assignment(ROOT / "nodes/h3_motion_context.py", "DEFAULT_CONTEXT_FRAMES"), CONTINUATION_CONTEXT_FRAMES)
        self.assertIn("native_h3_soft_av_39", assignment(ROOT / "routes.py", "CAPABILITY")["features"])
        architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("39 frames", architecture)
        self.assertNotIn("22-frame", architecture)

    def test_published_metadata_references_readable_documentation(self):
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('readme = "README.md"', metadata)
        self.assertRegex(metadata, r'version = "\d+\.\d+\.\d+"')
        self.assertIn('requires-python = ">=3.9"', metadata)


if __name__ == "__main__":
    unittest.main()
