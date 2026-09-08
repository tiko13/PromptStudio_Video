"""Storage paths keep the same meaning on Windows and POSIX hosts."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video import audio_mix, continuation, director_vision


class PathPortabilityTests(unittest.TestCase):
    def test_input_media_resolves_both_separator_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "media" / "sample.wav"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"test")
            folders = SimpleNamespace(get_input_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folders}):
                for resolver in (audio_mix._input_path, director_vision._input_path):
                    for value in ("nested/media/sample.wav", r"nested\media\sample.wav"):
                        with self.subTest(resolver=resolver.__module__, value=value):
                            self.assertEqual(Path(resolver(value)), target.resolve())

    def test_input_media_rejects_absolute_and_traversal_paths_from_either_os(self):
        folders = SimpleNamespace(get_input_directory=tempfile.gettempdir)
        with patch.dict(sys.modules, {"folder_paths": folders}):
            for resolver in (audio_mix._input_path, director_vision._input_path):
                for value in ("/outside.wav", r"C:\outside.wav", "C:outside.wav",
                              r"\\server\share\outside.wav", "../outside.wav", r"..\outside.wav"):
                    with self.subTest(resolver=resolver.__module__, value=value):
                        with self.assertRaisesRegex(ValueError, "relative|escapes"):
                            resolver(value)

    def test_output_descriptor_resolves_both_separator_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "video" / "sample.mp4"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"test")
            folders = SimpleNamespace(get_output_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folders}):
                for subfolder in ("nested/video", r"nested\video"):
                    with self.subTest(subfolder=subfolder):
                        path, descriptor = continuation.resolve_output_path({
                            "filename": target.name, "subfolder": subfolder, "type": "output",
                        })
                        self.assertEqual(Path(path), target.resolve())
                        self.assertEqual(descriptor["subfolder"], "nested/video")

    def test_output_descriptor_rejects_foreign_path_filenames_and_absolute_subfolders(self):
        for filename in ("nested/video.mp4", r"nested\video.mp4", "C:video.mp4"):
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, "filename"):
                continuation.normalize_output_descriptor({"filename": filename})
        for subfolder in ("/video", r"C:\video", "C:video", r"\\server\video", r"..\video"):
            with self.subTest(subfolder=subfolder), self.assertRaisesRegex(ValueError, "subfolder"):
                continuation.normalize_output_descriptor({"filename": "video.mp4", "subfolder": subfolder})


if __name__ == "__main__":
    unittest.main()
