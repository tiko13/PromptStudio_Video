import io
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from unittest import mock

from video import default_setup


class FakeFolderPaths:
    def __init__(self, roots, names=None):
        self.roots = roots
        self.names = names or {}

    def get_folder_paths(self, category):
        return [self.roots[category]]

    def get_filename_list(self, category):
        return list(self.names.get(category, ()))

    def get_full_path(self, category, name):
        path = os.path.join(self.roots[category], str(name).replace("\\", os.sep))
        return path if os.path.isfile(path) else None


class DefaultSetupTests(unittest.TestCase):
    def test_bundle_contains_current_normal_and_turbo_workflows(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = {category: os.path.join(directory, category) for category in {item["category"] for item in default_setup.MODEL_ASSETS}}
            plan = default_setup.workflow_setup_plan(FakeFolderPaths(roots))

        self.assertEqual(
            [workflow["path"] for workflow in plan["workflows"]],
            ["[PSV] MiniMax H3.json", "[PSV] MiniMax H3 Turbo.json"],
        )
        normal, turbo = [workflow["data"] for workflow in plan["workflows"]]
        self.assertEqual(sum(node["type"] == "PSV_MiniMaxH3Director" for node in normal["nodes"]), 1)
        self.assertEqual(sum(node["type"] == "PSV_MiniMaxH3TurboProfile" for node in turbo["nodes"]), 1)
        self.assertEqual(sum(node["type"] == "SaveVideo" for node in normal["nodes"]), 1)
        self.assertEqual(sum(node["type"] == "SaveVideo" for node in turbo["nodes"]), 1)

    def test_catalog_covers_all_models_and_turbo_loras(self):
        self.assertEqual(len(default_setup.MODEL_ASSETS), 9)
        self.assertEqual(sum(item["category"] == "loras" for item in default_setup.MODEL_ASSETS), 4)
        self.assertEqual(sum(item["category"] == "diffusion_models" for item in default_setup.MODEL_ASSETS), 2)
        self.assertTrue(all(item["url"].startswith("https://huggingface.co/") for item in default_setup.MODEL_ASSETS))
        for item in default_setup.MODEL_ASSETS:
            self.assertRegex(item["url"], r"/resolve/[0-9a-f]{40}/")
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(sum(item["size"] for item in default_setup.MODEL_ASSETS), 64_868_086_855)

    def test_existing_model_by_basename_is_reused_and_workflow_is_retargeted(self):
        assets = [dict(item) for item in default_setup.MODEL_ASSETS]
        assets[0]["size"] = 4
        assets[0]["sha256"] = hashlib.sha256(b"test").hexdigest()
        asset = assets[0]
        with tempfile.TemporaryDirectory() as directory:
            categories = {item["category"] for item in default_setup.MODEL_ASSETS}
            roots = {category: os.path.join(directory, category) for category in categories}
            basename = PureWindowsPath(asset["relative_path"]).name
            relative = Path("already_here") / basename
            alternate = Path(roots[asset["category"]]) / relative
            os.makedirs(os.path.dirname(alternate), exist_ok=True)
            with open(alternate, "wb") as file:
                file.write(b"test")
            names = {asset["category"]: [str(relative)]}
            with mock.patch.object(default_setup, "MODEL_ASSETS", tuple(assets)):
                plan = default_setup.workflow_setup_plan(FakeFolderPaths(roots, names))

        found = next(item for item in plan["models"] if item["id"] == asset["id"])
        self.assertTrue(found["installed"])
        self.assertEqual(found["name"], basename)
        self.assertEqual(found["resolved_path"], str(relative))
        unet = next(node for node in plan["workflows"][0]["data"]["nodes"] if node["id"] == 1)
        self.assertEqual(unet["widgets_values"][0], str(relative))

    def test_existing_model_keeps_exact_registered_name_for_both_path_styles(self):
        asset = {"category": "diffusion_models", "relative_path": r"Models\model.safetensors",
                 "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.safetensors"
            target.write_bytes(b"test")
            for name in ("custom/model.safetensors", r"custom\model.safetensors"):
                with self.subTest(name=name):
                    folders = mock.Mock()
                    folders.get_filename_list.return_value = [name]
                    folders.get_full_path.return_value = str(target)
                    self.assertEqual(default_setup._find_existing_asset(asset, folders), name)
                    folders.get_full_path.assert_called_once_with("diffusion_models", name)

    def test_missing_models_and_workflows_use_native_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = {item["category"]: str(Path(directory) / item["category"])
                     for item in default_setup.MODEL_ASSETS}
            plan = default_setup.workflow_setup_plan(FakeFolderPaths(roots))
        model = next(item for item in plan["models"] if item["id"] == "fl2va")
        expected = str(Path("MiniMax3") / model["name"])
        self.assertEqual(model["resolved_path"], expected)
        unet = next(node for node in plan["workflows"][0]["data"]["nodes"] if node["id"] == 1)
        self.assertEqual(unet["widgets_values"][0], expected)

    def test_resumable_download_appends_and_atomically_finishes(self):
        payload = b"abcdefghij"
        response = io.BytesIO(payload[4:])
        response.status = 206
        response.getcode = lambda: 206
        response.headers = {"Content-Range": "bytes 4-9/10", "Content-Length": "6", "ETag": '"tiny-v1"'}
        asset = {"name": "tiny.safetensors", "url": "https://huggingface.co/example/tiny", "size": len(payload),
                 "sha256": hashlib.sha256(payload).hexdigest()}
        progress = []
        stages = []
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "tiny.safetensors")
            with open(f"{target}.part", "wb") as file:
                file.write(payload[:4])
            with open(f"{target}.part.json", "w", encoding="utf-8") as file:
                json.dump({key: asset[key] for key in ("url", "size", "sha256")} | {"etag": '"tiny-v1"'}, file)
            with mock.patch.object(default_setup.urllib.request, "urlopen", return_value=response) as urlopen:
                default_setup._download_asset(asset, target, progress.append, stages.append)
            with open(target, "rb") as file:
                self.assertEqual(file.read(), payload)
        self.assertEqual(urlopen.call_args.args[0].headers["Range"], "bytes=4-")
        self.assertEqual(progress[-1], len(payload))
        self.assertIn("Verifying", stages)

    def test_same_size_corrupt_asset_and_complete_partial_are_rejected(self):
        asset = {"name": "tiny", "url": "https://example.invalid/tiny", "size": 5,
                 "sha256": hashlib.sha256(b"RIGHT").hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "tiny")
            with open(target, "wb") as file:
                file.write(b"WRONG")
            self.assertFalse(default_setup._valid_asset_file(target, 5, asset["sha256"]))
            with mock.patch.object(default_setup.urllib.request, "urlopen") as urlopen:
                with self.assertRaisesRegex(RuntimeError, "Existing model checksum"):
                    default_setup._download_asset(asset, target, lambda _done: None)
                os.replace(target, target + ".part")
                with self.assertRaisesRegex(RuntimeError, "checksum did not match"):
                    default_setup._download_asset(asset, target, lambda _done: None)
            urlopen.assert_not_called()
            self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
