import os
from fractions import Fraction
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import av
import numpy as np

from video.audio_mix import MIX_SAMPLE_RATE, mix_document_audio, mux_video_with_audio, probe_input_audio


class ExactAudioMixTests(unittest.TestCase):
    @staticmethod
    def _write_video(path):
        with av.open(path, mode="w", format="mp4") as container:
            stream = container.add_stream("h264", rate=24)
            stream.width = 64
            stream.height = 64
            stream.pix_fmt = "yuv420p"
            image = np.zeros((64, 64, 3), dtype=np.uint8)
            for _index in range(24):
                container.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
            container.mux(stream.encode(None))

    @staticmethod
    def _write_audio(path):
        samples = MIX_SAMPLE_RATE
        time = np.arange(samples, dtype=np.float32) / MIX_SAMPLE_RATE
        mono = 0.35 * np.sin(2 * np.pi * 440 * time)
        stereo = np.ascontiguousarray(np.stack([mono, mono]), dtype=np.float32)
        with av.open(path, mode="w", format="wav") as container:
            stream = container.add_stream("pcm_f32le", rate=MIX_SAMPLE_RATE)
            stream.layout = "stereo"
            frame = av.AudioFrame.from_ndarray(stereo, format="fltp", layout="stereo")
            frame.sample_rate = MIX_SAMPLE_RATE
            frame.time_base = Fraction(1, MIX_SAMPLE_RATE)
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)

    def test_exact_clip_is_placed_in_silence_and_muxed_with_video(self):
        with tempfile.TemporaryDirectory() as directory:
            source_video = os.path.join(directory, "source.mp4")
            source_audio = os.path.join(directory, "sting.wav")
            target = os.path.join(directory, "mixed.mp4")
            self._write_video(source_video)
            self._write_audio(source_audio)
            document = {
                "version": 1,
                "duration_seconds": 1,
                "references": [{
                    "id": "exact-1", "kind": "audio", "path": "sting.wav",
                    "roles": ["exact_audio"],
                }],
                "shots": [{
                    "id": "shot-1", "start": 0,
                    "camera": {"type": "Static Shot", "amplitude": "default", "speed": "default"},
                    "audio_clips": [{
                        "id": "clip-1", "reference_id": "exact-1",
                        "start": 0.25, "end": 0.75,
                        "source_start": 0, "source_end": 0.5,
                    }],
                }],
            }
            folder_paths = types.SimpleNamespace(get_input_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                metadata = probe_input_audio("sting.wav")
                audio, applied = mix_document_audio(source_video, document)

            self.assertAlmostEqual(metadata["duration_seconds"], 1.0, places=3)
            self.assertEqual(metadata["sample_rate"], MIX_SAMPLE_RATE)
            self.assertTrue(applied)
            self.assertLess(float(np.max(np.abs(audio[:, :10_000]))), 1e-6)
            self.assertGreater(float(np.max(np.abs(audio[:, 14_000:30_000]))), 0.2)

            mux_video_with_audio(source_video, target, audio)
            with av.open(target, mode="r") as container:
                self.assertEqual(len(container.streams.video), 1)
                self.assertEqual(len(container.streams.audio), 1)
                self.assertGreater(sum(frame.samples for frame in container.decode(audio=0)), 40_000)


if __name__ == "__main__":
    unittest.main()
