import io
import os
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
        self.assertEqual(sum(item["size"] for item in default_setup.MODEL_ASSETS), 64_868_086_855)

    def test_existing_model_by_basename_is_reused_and_workflow_is_retargeted(self):
        assets = [dict(item) for item in default_setup.MODEL_ASSETS]
        assets[0]["size"] = 4
        asset = assets[0]
        with tempfile.TemporaryDirectory() as directory:
            categories = {item["category"] for item in default_setup.MODEL_ASSETS}
            roots = {category: os.path.join(directory, category) for category in categories}
            alternate = os.path.join(roots[asset["category"]], "already_here", os.path.basename(asset["relative_path"]))
            os.makedirs(os.path.dirname(alternate), exist_ok=True)
            with open(alternate, "wb") as file:
                file.write(b"test")
            names = {asset["category"]: [os.path.join("already_here", os.path.basename(asset["relative_path"]))]}
            with mock.patch.object(default_setup, "MODEL_ASSETS", tuple(assets)):
                plan = default_setup.workflow_setup_plan(FakeFolderPaths(roots, names))

        found = next(item for item in plan["models"] if item["id"] == asset["id"])
        self.assertTrue(found["installed"])
        self.assertEqual(found["resolved_path"].replace("/", "\\"), f"already_here\\{found['name']}")
        unet = next(node for node in plan["workflows"][0]["data"]["nodes"] if node["id"] == 1)
        self.assertEqual(unet["widgets_values"][0].replace("/", "\\"), found["resolved_path"].replace("/", "\\"))

    def test_resumable_download_appends_and_atomically_finishes(self):
        payload = b"abcdefghij"
        response = io.BytesIO(payload[4:])
        response.status = 206
        response.getcode = lambda: 206
        asset = {"name": "tiny.safetensors", "url": "https://huggingface.co/example/tiny", "size": len(payload)}
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "tiny.safetensors")
            with open(f"{target}.part", "wb") as file:
                file.write(payload[:4])
            with mock.patch.object(default_setup.urllib.request, "urlopen", return_value=response) as urlopen:
                default_setup._download_asset(asset, target, progress.append)
            with open(target, "rb") as file:
                self.assertEqual(file.read(), payload)
        self.assertEqual(urlopen.call_args.args[0].headers["Range"], "bytes=4-")
        self.assertEqual(progress[-1], len(payload))


if __name__ == "__main__":
    unittest.main()
