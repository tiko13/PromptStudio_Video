import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import time
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
import torch
from safetensors.torch import save_file

from video import media_budget as budget
from video.continuation import concatenate_media_files, _iter_owned_audio
from video.media import reference_inputs


def make_video(path, seconds, fps=24):
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('libx264', rate=fps)
        stream.width = stream.height = 64
        stream.pix_fmt = 'yuv420p'
        stream.options = {'crf': '0', 'preset': 'ultrafast', 'g': str(fps)}
        for index in range(round(seconds * fps)):
            frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), index % 200, dtype=np.uint8), format='rgb24')
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def make_audio(path, seconds=12, rate=48000):
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('pcm_f32le', rate=rate)
        stream.layout = 'stereo'
        for start in range(0, seconds * rate, 4096):
            count = min(4096, seconds * rate - start)
            wave = np.sin((start + np.arange(count)) * 2 * np.pi * 437 / rate).astype(np.float32) * .3
            frame = av.AudioFrame.from_ndarray(np.stack([wave, wave]), format='fltp', layout='stereo')
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


class MediaBudgetTests(unittest.TestCase):
    def test_fallback_tail_encodes_only_the_final_39_frames_and_matching_audio(self):
        spec = importlib.util.spec_from_file_location('budget_fallback', Path(__file__).parents[1] / 'nodes/h3_motion_context.py')
        motion = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(motion)
        from video.audio_mix import mux_video_with_audio
        with tempfile.TemporaryDirectory() as directory:
            video = str(Path(directory) / 'video.mp4')
            source = str(Path(directory) / 'source.mp4')
            make_video(video, 3)
            wave = np.sin(np.arange(144000) * 2 * np.pi * 437 / 48000).astype(np.float32) * .3
            mux_video_with_audio(video, source, np.stack([wave, wave]))
            captured = {}
            def encode_video(value):
                captured['video'] = value
                return torch.zeros((1, 24, 12, 4, 4))
            def encode_audio(value):
                captured['audio'] = value
                return torch.zeros((1, 32, 2, 65))
            with patch.dict(sys.modules, {'folder_paths': SimpleNamespace(get_annotated_filepath=lambda _: source)}):
                result = motion._fallback_tail('source [output]', SimpleNamespace(encode=encode_video), SimpleNamespace(audio_sample_rate=48000, encode=encode_audio), torch.zeros((1, 24, 37, 4, 4)), 39)
            expected, _, _ = budget.video_range(source, 3 - 39 / 24, 3)
            self.assertTrue(torch.equal(captured['video'], expected))
            full_audio = budget.audio_array(source, 48000, end=3)
            np.testing.assert_allclose(captured['audio'][0].numpy().T, full_audio[:, -78000:], atol=1e-5)
            self.assertEqual(result['metadata']['source_video_frames'], 39)

    def test_short_and_long_lineage_assembly_benchmark(self):
        results = []
        for count in (2, 20):
            completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--benchmark', str(count)], env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1])}, capture_output=True, text=True, timeout=90, check=True)
            results.append(json.loads(completed.stdout.strip().splitlines()[-1]))
        self.assertEqual([item['output_frames'] for item in results], [153, 1179])
        self.assertLess(results[1]['python_peak_bytes'], results[0]['python_peak_bytes'] * 3)
        print('MEDIA_ASSEMBLY_BENCHMARK ' + json.dumps(results))

    def test_seeked_reference_matches_native_nearest_frame_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'range.mp4'
            make_video(path, 8, fps=30)
            with av.open(str(path)) as source:
                all_frames = np.stack([frame.to_ndarray(format='rgb24') for frame in source.decode(video=0)])
            frames, start, end = budget.video_range(str(path), 5.25, 7.25)
            indices = np.rint((5.25 + np.arange(48) / 24) * 30).astype(int)
            np.testing.assert_array_equal(frames.numpy(), all_frames[indices].astype(np.float32) / np.float32(255))
            self.assertEqual((start, end), (5.25, 7.25))

    def test_audio_seek_is_sample_exact_and_chunks_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'range.wav')
            make_audio(path)
            full = budget.audio_array(path)
            selected = budget.audio_array(path, start=10.125, end=10.625)
            np.testing.assert_array_equal(selected, full[:, 486000:510000])
            chunks = list(budget.iter_audio(path, start=10.125, end=10.625, chunk_size=1024))
            self.assertTrue(all(chunk.shape[1] <= 1024 for chunk in chunks))
            np.testing.assert_array_equal(np.concatenate(chunks, axis=1), selected)

    def test_resampled_seek_retains_timing_and_silence_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'range.wav')
            make_audio(path, seconds=3, rate=44100)
            full = budget.audio_array(path, 48000)
            selected = budget.audio_array(path, 48000, 2, 3.25)
            np.testing.assert_allclose(selected[:, :48000], full[:, 96000:144000], atol=1e-4)
            self.assertTrue(np.all(selected[:, 48000:] == 0))

    def test_count_and_declared_budgets_reject_before_decode(self):
        refs = [{'kind': 'video', 'use_embedded_audio': False} for _ in range(4)]
        with patch('video.media.model_references', return_value=refs), patch('video.media._video_components') as decode:
            with self.assertRaises(ValueError):
                reference_inputs({})
            decode.assert_not_called()
        for value in [float('nan'), float('inf'), 0, -1, 86401]:
            with self.assertRaises(ValueError):
                budget.duration(value)
        with self.assertRaises(ValueError):
            budget.geometry(16384, 16384)
        with self.assertRaises(ValueError):
            budget.geometry(1920, 1080, 360)
        with self.assertRaises(ValueError):
            budget.trim_range(20, float('nan'), 10)

    def test_streaming_audio_ownership_retains_exact_seam_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'range.wav')
            make_audio(path, seconds=2)
            chunks = list(_iter_owned_audio([path, path], [0, 39], [{'frames': 48}, {'frames': 48}], 48000, 'stereo', 114000))
            result = np.concatenate(chunks, axis=1)
            original = budget.audio_array(path)
            np.testing.assert_array_equal(result, np.concatenate([original[:, :18000], original], axis=1))
            self.assertTrue(all(chunk.shape[1] <= 1024 for chunk in chunks))

    def test_cancellation_cleans_atomic_assembly_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / 'source.mp4')
            target = Path(directory) / 'target.mp4'
            make_video(source, 4)
            target.write_bytes(b'previous valid output')
            for overlaps in (None, [0, 39]):
                calls = []
                def cancelled():
                    calls.append(1)
                    return len(calls) > 100
                with budget.media_operation(cancellation_check=cancelled):
                    with self.assertRaises(InterruptedError):
                        concatenate_media_files([source, source], str(target), overlap_frames=overlaps)
                self.assertEqual(target.read_bytes(), b'previous valid output')
                self.assertFalse(list(Path(directory).glob('*.tmp.mp4')))

    def test_invalid_saved_tail_is_rejected_before_tensor_materialization(self):
        spec = importlib.util.spec_from_file_location('budget_motion', Path(__file__).parents[1] / 'nodes/h3_motion_context.py')
        motion = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(motion)
        handle = SimpleNamespace(metadata=lambda: {'format': 'promptstudio_h3_av_tail_v3'}, keys=lambda: ['video', 'audio'])
        handle.get_slice = lambda key: SimpleNamespace(get_shape=lambda: [10000] * (5 if key == 'video' else 4))
        from unittest.mock import MagicMock
        handle.get_tensor = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = handle
        with patch.object(motion.os.path, 'getsize', return_value=1), patch.object(motion, '_output_path', return_value='unused'), patch.object(motion, 'safe_open', return_value=context):
            with self.assertRaisesRegex(ValueError, 'shape budget'):
                motion._load_saved_tail('invalid')
            handle.get_tensor.assert_not_called()

    def test_long_source_short_trim_has_constant_decode_work(self):
        measurements = []
        with tempfile.TemporaryDirectory() as directory:
            for seconds in (3, 30):
                path = str(Path(directory) / f'{seconds}.mp4')
                make_video(path, seconds)
                calls = []
                tracemalloc.start()
                started = time.perf_counter()
                with budget.media_operation(progress=lambda done, total: calls.append(done)):
                    frames, _, _ = budget.video_range(path, seconds - 2, seconds)
                elapsed = time.perf_counter() - started
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                measurements.append({'source_seconds': seconds, 'decoded_callbacks': len(calls), 'seconds': round(elapsed, 4), 'python_peak_bytes': peak, 'tensor_bytes': frames.nelement() * frames.element_size()})
        self.assertLessEqual(measurements[1]['decoded_callbacks'], measurements[0]['decoded_callbacks'] + 26)
        self.assertEqual(measurements[0]['tensor_bytes'], measurements[1]['tensor_bytes'])
        print('MEDIA_RANGE_BENCHMARK ' + json.dumps(measurements))


def benchmark_lineage(count):
    def peak_working_set():
        if os.name != 'nt':
            return None
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        query = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.PeakWorkingSetSize
    with tempfile.TemporaryDirectory() as directory:
        source = str(Path(directory) / 'source.mp4')
        target = str(Path(directory) / 'target.mp4')
        make_video(source, 4)
        before = peak_working_set()
        tracemalloc.start()
        started = time.perf_counter()
        concatenate_media_files([source] * count, target, overlap_frames=[0] + [39] * (count - 1))
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with av.open(target) as result:
            frames = result.streams.video[0].frames
        return {'segments': count, 'output_frames': frames, 'seconds': round(elapsed, 4), 'python_peak_bytes': peak, 'process_peak_before_bytes': before, 'process_peak_after_bytes': peak_working_set()}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--benchmark':
        print(json.dumps(benchmark_lineage(int(sys.argv[2]))))
    else:
        unittest.main()
