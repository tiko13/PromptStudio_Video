import base64
import io
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from video.director_vision import load_vision_images, normalize_attachments
from video.llm_provider import generate_chat


class DirectorVisionTests(unittest.TestCase):
    def test_action_is_a_supported_grounding_usage(self):
        normalized = normalize_attachments([{"path": "motion.png", "usage": "action"}])
        self.assertEqual(normalized[0]["usage"], "action")

    def test_attachment_usage_is_normalized_and_limited(self):
        normalized = normalize_attachments([{
            "id": "image-1",
            "path": "director/example.png",
            "name": "Example",
            "usage": "SUBJECT",
            "source_width": 800,
            "source_height": 600,
        }])
        self.assertEqual(normalized[0]["usage"], "subject")
        with self.assertRaisesRegex(ValueError, "at most 4"):
            normalize_attachments([{"path": f"{index}.png"} for index in range(5)])
        with self.assertRaisesRegex(ValueError, "unsupported usage"):
            normalize_attachments([{"path": "x.png", "usage": "execute"}])

    def test_image_is_loaded_only_from_comfy_input_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            subfolder = os.path.join(directory, "director")
            os.makedirs(subfolder)
            path = os.path.join(subfolder, "example.png")
            Image.new("RGB", (8, 6), (20, 40, 60)).save(path, format="PNG")
            folder_paths = types.SimpleNamespace(get_input_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                attachments, images = load_vision_images([{
                    "path": "director/example.png", "usage": "describe",
                }])
                self.assertEqual(attachments[0]["path"], "director/example.png")
                self.assertTrue(images[0]["data_uri"].startswith("data:image/png;base64,"))
                with Image.open(io.BytesIO(base64.b64decode(images[0]["base64"]))) as decoded:
                    self.assertEqual(decoded.size, (8, 6))
                    self.assertEqual(decoded.mode, "RGB")
                with self.assertRaisesRegex(ValueError, "escapes"):
                    load_vision_images([{"path": "../outside.png"}])

    def test_webp_is_transcoded_to_png_for_local_vision_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reference.webp")
            Image.new("RGB", (12, 10), (220, 180, 120)).save(path, format="WEBP")
            folder_paths = types.SimpleNamespace(get_input_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                _attachments, images = load_vision_images([{
                    "path": "reference.webp", "usage": "subject",
                }])
            self.assertEqual(images[0]["mime_type"], "image/png")
            self.assertTrue(images[0]["data_uri"].startswith("data:image/png;base64,"))

    def test_provider_payload_is_delegated_to_primary_prompt_studio(self):
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Earlier"},
            {"role": "assistant", "content": "Answer"},
            {"role": "user", "content": "Inspect this"},
        ]
        images = [{"data_uri": "data:image/png;base64,AAAA", "base64": "AAAA"}]
        shared = SimpleNamespace(shared_llm_generate=Mock(return_value="response"))

        with patch("video.llm_provider._primary_routes", return_value=shared):
            result = generate_chat({"llm_provider": "llamacpp"}, messages, images)

        self.assertEqual(result, "response")
        shared.shared_llm_generate.assert_called_once_with(
            {"llm_provider": "llamacpp"}, messages, images
        )


if __name__ == "__main__":
    unittest.main()
