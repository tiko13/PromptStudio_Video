import importlib.util
from pathlib import Path
import unittest

import torch

_SPEC = importlib.util.spec_from_file_location(
    "promptstudio_video_h3_motion_context",
    Path(__file__).parents[1] / "nodes" / "h3_motion_context.py",
)
_MOTION_CONTEXT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOTION_CONTEXT)

PromptStudioH3TrimContext = _MOTION_CONTEXT.PromptStudioH3TrimContext
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

    def test_context_path_is_deterministic_and_rejects_traversal(self):
        self.assertEqual(
            context_relative_path("project-1", "generation-2"),
            "video/PromptStudio_Video/latents/project-1/generation-2.safetensors",
        )
        with self.assertRaises(ValueError):
            context_relative_path("../outside", "generation-2")


if __name__ == "__main__":
    unittest.main()
