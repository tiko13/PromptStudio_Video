import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch

_SPEC = importlib.util.spec_from_file_location(
    "promptstudio_video_h3_motion_context",
    Path(__file__).parents[1] / "nodes" / "h3_motion_context.py",
)
_MOTION_CONTEXT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOTION_CONTEXT)
PromptStudioH3MotionContext = _MOTION_CONTEXT.PromptStudioH3MotionContext

PromptStudioH3TrimContext = _MOTION_CONTEXT.PromptStudioH3TrimContext
PromptStudioH3SaveContext = _MOTION_CONTEXT.PromptStudioH3SaveContext
_apply_native_guides = _MOTION_CONTEXT._apply_native_guides
_audio_start_offset_steps = _MOTION_CONTEXT._audio_start_offset_steps
_continuation_guides = _MOTION_CONTEXT._continuation_guides
_load_saved_tail = _MOTION_CONTEXT._load_saved_tail
_drop_prefix_guides = _MOTION_CONTEXT._drop_prefix_guides
soft_av_masks = _MOTION_CONTEXT.soft_av_masks
av_clock_metadata = _MOTION_CONTEXT.av_clock_metadata
compact_tail = _MOTION_CONTEXT.compact_tail
context_relative_path = _MOTION_CONTEXT.context_relative_path
pixel_frames_for_steps = _MOTION_CONTEXT.pixel_frames_for_steps
step_offsets = _MOTION_CONTEXT.step_offsets
steps_for_frames = _MOTION_CONTEXT.steps_for_frames


class MotionContextTests(unittest.TestCase):
    def test_native_h3_temporal_grid_maps_22_frames_to_seven_steps(self):
        self.assertEqual(steps_for_frames(22), 7)
        self.assertEqual(pixel_frames_for_steps(7), 22)
        self.assertEqual(step_offsets(7), [0, 1, 5, 9, 13, 17, 18])
        self.assertEqual(steps_for_frames(39), 12)
        self.assertEqual(pixel_frames_for_steps(12), 39)
        self.assertEqual(
            step_offsets(12), [0, 1, 5, 9, 13, 17, 18, 22, 26, 30, 34, 35],
        )

    def test_compact_tail_preserves_exact_video_and_audio_latent_tail(self):
        video = torch.arange(1 * 24 * 37 * 2 * 2, dtype=torch.float32).reshape(1, 24, 37, 2, 2)
        audio = torch.arange(1 * 32 * 2 * 180, dtype=torch.float32).reshape(1, 32, 2, 180)

        video_tail, audio_tail = compact_tail({"samples": [video, audio]}, 39)

        self.assertEqual(tuple(video_tail.shape), (1, 24, 12, 2, 2))
        self.assertEqual(tuple(audio_tail.shape), (1, 32, 2, 65))
        self.assertTrue(torch.equal(video_tail, video[:, :, -12:]))
        self.assertTrue(torch.equal(audio_tail, audio[..., -65:]))

    def test_trim_removes_repeated_audiovisual_head_and_aligns_duration(self):
        images = torch.zeros((158, 2, 2, 3), dtype=torch.float32)
        sample_rate = 48000
        waveform = torch.zeros((1, 2, round(158 / 24 * sample_rate) + 64), dtype=torch.float32)

        trimmed_images, trimmed_audio, assembly_images, assembly_audio = PromptStudioH3TrimContext().trim(
            images, {"waveform": waveform, "sample_rate": sample_rate}, 39, 24,
        )

        self.assertEqual(len(trimmed_images), 119)
        self.assertEqual(trimmed_audio["waveform"].shape[-1], round(119 / 24 * sample_rate))
        self.assertEqual(len(assembly_images), 158)
        self.assertEqual(
            assembly_audio["waveform"].shape[-1], round(158 / 24 * sample_rate),
        )

    def test_clock_metadata_preserves_parent_grid_overhang(self):
        video = torch.zeros((1, 24, 37, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 207), dtype=torch.float32)

        metadata = av_clock_metadata({"samples": [video, audio]})

        self.assertEqual(metadata["source_video_frames"], 124)
        self.assertEqual(metadata["source_audio_steps"], 207)
        self.assertAlmostEqual(metadata["audio_overhang_steps"], 1 / 3)

    def test_saved_context_records_v3_soft_av_metadata_and_exact_tail(self):
        video = torch.zeros((1, 24, 37, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 207), dtype=torch.float32)
        folder_paths = SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            folder_paths.get_output_directory = lambda: directory
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                relative = PromptStudioH3SaveContext().save(
                    {"samples": [video, audio]}, "project", "generation", 39,
                )[0]
                saved = _load_saved_tail(relative)

        saved_video, saved_audio = saved["samples"]
        self.assertEqual(tuple(saved_video.shape), (1, 24, 12, 2, 2))
        self.assertEqual(tuple(saved_audio.shape), (1, 32, 2, 65))
        self.assertEqual(saved["metadata"]["format"], "promptstudio_h3_av_tail_v3")
        self.assertEqual(saved["metadata"]["transition_recipe"], "soft_av_39_exact_video_half_cosine_audio_8")
        self.assertEqual(saved["metadata"]["context_frames"], "39")
        self.assertAlmostEqual(float(saved["metadata"]["audio_overhang_steps"]), 1 / 3)

    def test_audio_start_offset_preserves_all_parent_clock_phases(self):
        self.assertAlmostEqual(_audio_start_offset_steps(22, 37, 1 / 3), 0)
        self.assertAlmostEqual(_audio_start_offset_steps(22, 37, 0), -1 / 3)
        self.assertAlmostEqual(_audio_start_offset_steps(22, 37, -1 / 3), -2 / 3)

    def test_native_guides_keep_video_at_zero_and_apply_audio_start_offset(self):
        video = torch.zeros((1, 24, 7, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 37), dtype=torch.float32)

        early_audio = _continuation_guides(video, audio, -2 / 3)
        aligned = _continuation_guides(video, audio, 0)

        self.assertAlmostEqual(early_audio[1]["resolved_frame_index"], -0.4)
        self.assertEqual(len(aligned), 1)
        self.assertIs(aligned[0]["audio_latent"], audio)
        self.assertIs(early_audio[0]["latent"], video)
        self.assertIs(early_audio[1]["audio_latent"], audio)

    def test_native_guides_replace_only_conflicting_head_anchors(self):
        old_head = {"resolved_frame_index": 0, "latent": object()}
        old_tail = {"resolved_frame_index": 100, "latent": object()}
        new_guide = {"resolved_frame_index": 0, "latent": object()}
        conditioning = [[torch.zeros((1, 1)), {"minimax_keyframes": [old_head, old_tail]}]]

        output = _apply_native_guides(conditioning, [new_guide], 22)

        self.assertEqual(output[0][1]["minimax_keyframes"], [old_tail, new_guide])


    def test_soft_av_keeps_picture_exact_and_releases_last_eight_audio_ticks(self):
        video = torch.zeros((1, 24, 47, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 263), dtype=torch.float32)

        video_mask, audio_mask = soft_av_masks({"samples": [video, audio]}, 12, 65)

        self.assertTrue(torch.all(video_mask[:, :, :12] == 0))
        self.assertTrue(torch.all(video_mask[:, :, 12:] == 1))
        self.assertTrue(torch.all(audio_mask[..., :57] == 0))
        expected = 0.5 - 0.5 * torch.cos(torch.pi * torch.arange(1, 9) / 8)
        torch.testing.assert_close(audio_mask[0, 0, 0, 57:65], expected)
        self.assertTrue(torch.all(audio_mask[..., 65:] == 1))

    def test_soft_av_drops_only_guides_inside_protected_prefix(self):
        old_head = {"resolved_frame_index": 0, "latent": object()}
        old_boundary = {"resolved_frame_index": 39, "latent": object()}
        old_tail = {"resolved_frame_index": 100, "latent": object()}
        conditioning = [[
            torch.zeros((1, 1)),
            {"minimax_keyframes": [old_head, old_boundary, old_tail]},
        ]]

        output = _drop_prefix_guides(conditioning, 39)

        self.assertEqual(
            output[0][1]["minimax_keyframes"], [old_boundary, old_tail],
        )
    def test_context_path_is_deterministic_and_rejects_traversal(self):
        self.assertEqual(
            context_relative_path("project-1", "generation-2"),
            "video/PromptStudio_Video/latents/project-1/generation-2.safetensors",
        )
        with self.assertRaises(ValueError):
            context_relative_path("../outside", "generation-2")

    def test_motion_context_copies_saved_av_prefix_and_emits_nested_native_masks(self):
        class NestedTensor:
            def __init__(self, tensors):
                self.tensors = list(tensors)
                self.is_nested = True

            def unbind(self):
                return self.tensors

        comfy = ModuleType("comfy")
        nested = ModuleType("comfy.nested_tensor")
        nested.NestedTensor = NestedTensor
        comfy.nested_tensor = nested
        target_video = torch.zeros((1, 24, 47, 2, 2), dtype=torch.float32)
        target_audio = torch.zeros((1, 32, 2, 263), dtype=torch.float32)
        source_video = torch.ones((1, 24, 12, 2, 2), dtype=torch.float32)
        source_audio = torch.full((1, 32, 2, 65), 2.0, dtype=torch.float32)
        conditioning = [[torch.zeros((1, 1)), {"minimax_keyframes": [
            {"resolved_frame_index": 0, "latent": object()},
            {"resolved_frame_index": 39, "latent": object()},
        ]}]]
        saved = {
            "samples": [source_video, source_audio],
            "metadata": {"format": "promptstudio_h3_av_tail_v3"},
        }

        with patch.dict(sys.modules, {"comfy": comfy, "comfy.nested_tensor": nested}):
            with patch.object(_MOTION_CONTEXT, "require_native_masks"):
                with patch.object(_MOTION_CONTEXT, "_load_saved_tail", return_value=saved):
                    output_conditioning, output_latent, trim = PromptStudioH3MotionContext().apply(
                        conditioning, {"samples": [target_video, target_audio]},
                        object(), object(), "saved.safetensors", "", 39,
                    )

        output_video, output_audio = output_latent["samples"].unbind()
        video_mask, audio_mask = output_latent["noise_mask"].unbind()
        self.assertEqual(trim, 39)
        self.assertTrue(torch.equal(output_video[:, :, :12], source_video))
        self.assertTrue(torch.equal(output_audio[..., :65], source_audio))
        self.assertTrue(torch.all(output_video[:, :, 12:] == 0))
        self.assertTrue(torch.all(video_mask[:, :, :12] == 0))
        self.assertTrue(torch.all(audio_mask[..., :57] == 0))
        self.assertEqual(
            [item["resolved_frame_index"] for item in output_conditioning[0][1]["minimax_keyframes"]],
            [39],
        )


if __name__ == "__main__":
    unittest.main()
