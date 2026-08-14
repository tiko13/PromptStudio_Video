import importlib.util
from pathlib import Path
from unittest.mock import patch
import sys
import types
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "promptstudio_video_minimax_h3_turbo_profile",
    Path(__file__).parents[1] / "nodes" / "minimax_h3_turbo_profile.py",
)
_TURBO = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _TURBO
_SPEC.loader.exec_module(_TURBO)

PromptStudioMiniMaxH3TurboProfile = _TURBO.PromptStudioMiniMaxH3TurboProfile
resolve_installed_lora = _TURBO.resolve_installed_lora
select_turbo_profile = _TURBO.select_turbo_profile


class TurboProfileTests(unittest.TestCase):
    def test_auto_quality_uses_768p_profile_for_exact_training_canvas(self):
        selection = select_turbo_profile("i2va", 1344, 768)

        self.assertEqual(selection.profile_id, "fl2va_768p_4step_v1.0")
        self.assertEqual((selection.steps, selection.shift_video, selection.shift_audio), (4, 6.0, 3.0))

    def test_auto_quality_uses_mixed_8step_profile_for_other_base_canvases(self):
        selection = select_turbo_profile("fl2va", 960, 544)

        self.assertEqual(selection.profile_id, "fl2va_mixed_8step_v1.0")
        self.assertEqual((selection.steps, selection.shift_video, selection.shift_audio), (8, 12.0, 3.0))

    def test_fast_preset_uses_mixed_4step_profile_away_from_768p(self):
        selection = select_turbo_profile("t2va", 864, 480, "fast_4step")

        self.assertEqual(selection.profile_id, "fl2va_mixed_4step_v0.1")
        self.assertEqual(selection.steps, 4)

    def test_ref2va_always_uses_its_task_specific_profile(self):
        selection = select_turbo_profile("ref2va", 1344, 768, "fast_4step")

        self.assertEqual(selection.profile_id, "ref2va_4step_v0.1")
        self.assertEqual((selection.steps, selection.shift_video, selection.shift_audio), (4, 12.0, 3.0))

    def test_last_frame_mode_stays_in_the_fl2va_family(self):
        selection = select_turbo_profile("l2va", 960, 544)

        self.assertEqual(selection.profile_id, "fl2va_mixed_8step_v1.0")

    def test_invalid_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported MiniMax H3 Turbo mode"):
            select_turbo_profile("unknown", 960, 544)

    def test_installed_lora_can_move_to_an_unambiguous_subdirectory(self):
        expected = resolve_installed_lora(
            "MiniMax3\\adapter.safetensors",
            ["custom\\adapter.safetensors"],
        )

        self.assertEqual(expected, "custom\\adapter.safetensors")

    def test_ambiguous_lora_basename_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_installed_lora(
                "MiniMax3\\adapter.safetensors",
                ["first\\adapter.safetensors", "second\\adapter.safetensors"],
            )

    def test_profile_applies_only_the_selected_lora(self):
        calls = []

        class FakeLoader:
            def load_lora_model_only(self, model, lora_name, strength_model):
                calls.append((model, lora_name, strength_model))
                return ("patched-model",)

        installed = "custom\\minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors"
        folder_paths = types.SimpleNamespace(get_filename_list=lambda kind: [installed])
        core_nodes = types.SimpleNamespace(LoraLoaderModelOnly=FakeLoader)
        node = PromptStudioMiniMaxH3TurboProfile()

        with patch.dict(sys.modules, {"folder_paths": folder_paths, "nodes": core_nodes}):
            result = node.apply_profile(
                "base-model",
                "ref2va",
                960,
                544,
                "auto_quality",
                "unused-8step.safetensors",
                "unused-4step.safetensors",
                "unused-768p.safetensors",
                "MiniMax3\\minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
                0.75,
                True,
            )

        self.assertEqual(result, ("patched-model", 4, 12.0, 3.0, "ref2va_4step_v0.1", installed))
        self.assertEqual(calls, [("base-model", installed, 0.75)])

    def test_disabled_profile_returns_the_existing_full_step_settings(self):
        result = PromptStudioMiniMaxH3TurboProfile().apply_profile(
            "base-model", "t2va", 960, 544, "auto_quality", "", "", "", "", 0.75, False,
        )

        self.assertEqual(result, ("base-model", 20, 11.0, 4.0, "disabled", ""))


if __name__ == "__main__":
    unittest.main()
