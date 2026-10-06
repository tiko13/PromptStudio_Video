import json
from pathlib import Path
import unittest

from video.sampling_profiles import select_profile, profile_catalog
from video import default_setup


class SamplingProfileTests(unittest.TestCase):
    def test_fl_and_reference_shifts_are_not_interchangeable(self):
        for mode in ("t2va", "i2va", "fl2va", "l2va"):
            profile = select_profile(mode, 1344, 768, "balanced")
            self.assertEqual((profile.steps, profile.shift_video, profile.shift_audio), (8, 6, 3))
        profile = select_profile("ref2va", 1344, 768, "balanced")
        self.assertEqual((profile.steps, profile.shift_video, profile.shift_audio), (8, 12, 3))
        self.assertIn("ref2v", profile.lora)

    def test_experiments_reject_unsupported_conditioning(self):
        with self.assertRaisesRegex(ValueError, "REF2VA"):
            select_profile("ref2va", 1344, 768, "experimental_fasth3")
        with self.assertRaisesRegex(ValueError, "T2VA"):
            select_profile("i2va", 1344, 768, "experimental_taomate")
        fast = select_profile("i2va", 1344, 768, "experimental_fasth3")
        self.assertEqual((fast.steps, fast.sampler, fast.shift_video, fast.lora), (8, "res_multistep", 10, ""))

    def test_invalid_canvas_and_preset_fail_closed(self):
        for dims in ((0, 768), (1345, 768), (1920, 1088), (1344.5, 768), (True, 768), (float("nan"), 768)):
            with self.assertRaises(ValueError):
                select_profile("t2va", *dims)
        with self.assertRaises(ValueError):
            select_profile("t2va", 1344, 768, "typo")

    def test_quality_has_no_acceleration_lora(self):
        profile = select_profile("ref2va", 1344, 768, "full_quality")
        self.assertEqual((profile.steps, profile.lora), (25, ""))
        self.assertEqual(profile_catalog()["version"], 2)

    def test_taomate_uses_its_distilled_state_indices(self):
        profile = select_profile("t2va", 1344, 768, "experimental_taomate")
        self.assertEqual(profile.scheduler, "taomate_50grid")
        self.assertEqual(len(profile.sigmas), 4)
        self.assertAlmostEqual(profile.sigmas[1], 396 / 412)
        self.assertAlmostEqual(profile.sigmas[2], 192 / 225)
        graph = default_setup.load_bundled_workflow("[PSV] MiniMax H3 TaoMate experiment.json")
        node = next(node for node in graph["nodes"] if node["id"] == 14)
        self.assertEqual(node["type"], "ManualSigmas")
        for actual, expected in zip(map(float, node["widgets_values"][0].split(",")), profile.sigmas):
            self.assertAlmostEqual(actual, expected)

    def test_optional_workflows_have_consistent_bidirectional_links(self):
        for bundle in default_setup.WORKFLOW_BUNDLES.values():
            for name in bundle["workflows"]:
                graph = default_setup.load_bundled_workflow(name)
                nodes = {node["id"]: node for node in graph["nodes"]}
                for link, source, slot, target, target_slot, kind in graph["links"]:
                    self.assertIn(link, nodes[source]["outputs"][slot]["links"], name)
                    self.assertEqual(nodes[target]["inputs"][target_slot]["link"], link, name)
                    self.assertEqual(nodes[source]["outputs"][slot]["type"], kind, name)

    def test_optional_assets_are_pinned_and_separate_from_default_downloads(self):
        self.assertEqual(len(default_setup.MODEL_ASSETS), 9)
        for asset in default_setup.OPTIONAL_MODEL_ASSETS:
            self.assertRegex(asset["url"], r"/resolve/[a-f0-9]{40}/")
            self.assertRegex(asset["sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(asset["size"], 0)
        self.assertNotIn("fasth3", default_setup.WORKFLOW_BUNDLES["modern"]["assets"])
