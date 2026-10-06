import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


root = Path(__file__).resolve().parents[1]
package = types.ModuleType("sampling_test_package")
package.__path__ = [str(root)]
node_package = types.ModuleType("sampling_test_package.nodes")
node_package.__path__ = [str(root / "nodes")]
sys.modules[package.__name__] = package
sys.modules[node_package.__name__] = node_package
spec = importlib.util.spec_from_file_location(node_package.__name__ + ".minimax_h3_sampling_profile", root / "nodes" / "minimax_h3_sampling_profile.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SamplingNodeTests(unittest.TestCase):
    def run_node(self, preset="full_quality", attention="safe_dense", kernel=True, fast=False):
        events = []
        model = types.SimpleNamespace(get_model_object=lambda name: types.SimpleNamespace(blocks=[types.SimpleNamespace(attn=types.SimpleNamespace(to_gate_compress=object() if fast else None))]))
        def native(name):
            def execute(**values):
                events.append((name, values))
                return types.SimpleNamespace(result=(model,))
            return types.SimpleNamespace(execute=execute)
        comfy = types.ModuleType("comfy")
        comfy.samplers = types.SimpleNamespace(sampler_object=lambda name: name)
        comfy.model_management = types.SimpleNamespace(get_torch_device=lambda: "gpu")
        registry = {key: native(key) for key in ("ModelAttentionBackend", "BlockSparseAttention", "MiniMaxH3SigmaShift")}
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.samplers": comfy.samplers,
            "comfy.model_management": comfy.model_management, "comfy_kitchen": types.SimpleNamespace(sol_attn_is_available=lambda device: kernel),
            "folder_paths": types.SimpleNamespace(), "nodes": types.SimpleNamespace(NODE_CLASS_MAPPINGS=registry, LoraLoaderModelOnly=object)}):
            result = module.PromptStudioMiniMaxH3SamplingProfile().apply(model, "t2va", 1344, 768, preset, attention)
        return result, events

    def test_safe_baseline_does_not_install_sparse_patch(self):
        result, events = self.run_node()
        self.assertEqual([name for name, _ in events], ["ModelAttentionBackend"])
        self.assertEqual(result[-1], "euler")

    def test_sparse_thresholds_are_installed_after_shifts_and_preserve_audio(self):
        _, events = self.run_node(attention="experimental_sol")
        self.assertEqual([name for name, _ in events], ["ModelAttentionBackend", "MiniMaxH3SigmaShift", "BlockSparseAttention"])
        self.assertEqual(events[-1][1]["sink_conditioning"], "exact_kv_and_rows")

    def test_missing_kernel_and_mismatched_checkpoint_fail(self):
        with self.assertRaisesRegex(RuntimeError, "kernel is unavailable"):
            self.run_node(attention="experimental_sol", kernel=False)
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.run_node(preset="experimental_fasth3")

    def test_fasth3_uses_vsa_schedule_and_sampler(self):
        result, events = self.run_node(preset="experimental_fasth3", fast=True)
        self.assertEqual(result[-1], "res_multistep")
        self.assertEqual(events[-1][1]["selection"], {"selection": "vsa", "keep_percent": 10.0})
        self.assertEqual(events[-1][1]["extra_tokens"], 0)
