import importlib.util
from pathlib import Path
import sys
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

PromptStudioH3TrimContext = _MOTION_CONTEXT.PromptStudioH3TrimContext
PromptStudioH3SaveContext = _MOTION_CONTEXT.PromptStudioH3SaveContext
_apply_native_guides = _MOTION_CONTEXT._apply_native_guides
_audio_start_offset_steps = _MOTION_CONTEXT._audio_start_offset_steps
_continuation_guides = _MOTION_CONTEXT._continuation_guides
_load_saved_tail = _MOTION_CONTEXT._load_saved_tail
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

    def test_compact_tail_preserves_exact_video_and_audio_latent_tail(self):
        video = torch.arange(1 * 24 * 37 * 2 * 2, dtype=torch.float32).reshape(1, 24, 37, 2, 2)
        audio = torch.arange(1 * 32 * 2 * 180, dtype=torch.float32).reshape(1, 32, 2, 180)

        video_tail, audio_tail = compact_tail({"samples": [video, audio]}, 22)

        self.assertEqual(tuple(video_tail.shape), (1, 24, 7, 2, 2))
        self.assertEqual(tuple(audio_tail.shape), (1, 32, 2, 37))
        self.assertTrue(torch.equal(video_tail, video[:, :, -7:]))
        self.assertTrue(torch.equal(audio_tail, audio[..., -37:]))

    def test_trim_removes_repeated_audiovisual_head_and_aligns_duration(self):
        images = torch.zeros((141, 2, 2, 3), dtype=torch.float32)
        sample_rate = 48000
        waveform = torch.zeros((1, 2, round(141 / 24 * sample_rate)), dtype=torch.float32)

        trimmed_images, trimmed_audio = PromptStudioH3TrimContext().trim(
            images, {"waveform": waveform, "sample_rate": sample_rate}, 22, 24,
        )

        self.assertEqual(len(trimmed_images), 119)
        self.assertEqual(trimmed_audio["waveform"].shape[-1], round(119 / 24 * sample_rate))

    def test_clock_metadata_preserves_parent_grid_overhang(self):
        video = torch.zeros((1, 24, 37, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 207), dtype=torch.float32)

        metadata = av_clock_metadata({"samples": [video, audio]})

        self.assertEqual(metadata["source_video_frames"], 124)
        self.assertEqual(metadata["source_audio_steps"], 207)
        self.assertAlmostEqual(metadata["audio_overhang_steps"], 1 / 3)

    def test_saved_context_records_v2_clock_metadata_and_exact_tail(self):
        video = torch.zeros((1, 24, 37, 2, 2), dtype=torch.float32)
        audio = torch.zeros((1, 32, 2, 207), dtype=torch.float32)
        folder_paths = SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            folder_paths.get_output_directory = lambda: directory
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                relative = PromptStudioH3SaveContext().save(
                    {"samples": [video, audio]}, "project", "generation", 22,
                )[0]
                saved = _load_saved_tail(relative)

        saved_video, saved_audio = saved["samples"]
        self.assertEqual(tuple(saved_video.shape), (1, 24, 7, 2, 2))
        self.assertEqual(tuple(saved_audio.shape), (1, 32, 2, 37))
        self.assertEqual(saved["metadata"]["format"], "promptstudio_h3_av_tail_v2")
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

    def test_context_path_is_deterministic_and_rejects_traversal(self):
        self.assertEqual(
            context_relative_path("project-1", "generation-2"),
            "video/PromptStudio_Video/latents/project-1/generation-2.safetensors",
        )
        with self.assertRaises(ValueError):
            context_relative_path("../outside", "generation-2")


if __name__ == "__main__":
    unittest.main()
