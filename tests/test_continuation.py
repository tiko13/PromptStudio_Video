import os
import tempfile
import unittest

import av
import numpy as np
from video.compiler import compile_prompt
from video.continuation import (
    CONTINUATION_CONTEXT_FRAMES,
    annotated_output_path,
    build_continuation_document,
    concatenate_media_files,
    continuation_frame_plan,
    normalize_output_descriptor,
    probe_video,
)
from video.contracts import default_document


class ContinuationTests(unittest.TestCase):
    def test_builds_native_motion_context_prompt_without_reference_tokens(self):
        parent = default_document()
        parent["shots"][0]["camera"] = {
            "type": "Tracking Shot", "amplitude": "small", "speed": "slow", "target": "the woman"
        }
        parent["shots"][0]["steps"] = [
            {"id": "step-1", "type": "action", "text": "She reaches the already-opening door."}
        ]
        document = build_continuation_document(
            parent,
            "She opens the door and steps into the rain.",
            5,
        )
        prompt = compile_prompt(document)

        self.assertEqual(document["resolved_mode"], "t2va")
        self.assertEqual(document["references"], [])
        self.assertEqual(document["shots"][0]["camera"]["type"], "Tracking Shot")
        self.assertNotRegex(prompt, r"<(?:Picture|Video|Audio|Subject)\s+\d+>")
        self.assertNotIn("At 2.00 seconds", prompt)
        self.assertIn("She opens the door and steps into the rain.", prompt)
        self.assertIn("currently visible phase", prompt.lower())
        self.assertIn("without a pause, reset, reversal", prompt.lower())
        self.assertNotIn("holds completely still", prompt.lower())

    def test_continuation_frame_plan_accounts_for_trimmed_context(self):
        timing = continuation_frame_plan(5)
        self.assertEqual(timing["context_frames"], CONTINUATION_CONTEXT_FRAMES)
        self.assertEqual(timing["sample_frames"], 141)
        self.assertEqual(timing["delivered_frames"], 119)
        self.assertAlmostEqual(timing["delivered_duration"], 119 / 24)

    def test_parent_reference_tokens_do_not_leak_into_segment_prompt(self):
        parent = default_document()
        parent["style"] = "Match <Picture 9> exactly."
        parent["overall_soundscape"] = "Continue <Audio 7>."
        parent["non_diegetic_music"] = "Music from <Video 4>."
        parent["shots"][0]["visible_text"] = ["A sign beside <Subject 3>"]
        document = build_continuation_document(
            parent,
            "The camera follows her outside.",
            5,
        )

        prompt = compile_prompt(document)
        self.assertNotIn("<Picture 9>", prompt)
        self.assertNotIn("<Audio 7>", prompt)
        self.assertNotIn("<Video 4>", prompt)
        self.assertNotIn("<Subject 3>", prompt)

    def test_structured_extension_offsets_cuts_and_preserves_dialogue_and_sound(self):
        parent = default_document()
        parent["shots"][0]["steps"] = [{
            "id": "parent-action", "type": "action", "text": "She reaches the already-opening door."
        }]
        extension = default_document()
        extension["duration_seconds"] = 5
        extension["shots"] = [
            {
                "id": "extension-shot-1", "start": 0,
                "composition": "The tracking composition continues through the doorway.",
                "camera": {"type": "Tracking Shot", "amplitude": "small", "speed": "slow"},
                "steps": [{
                    "id": "next-action", "type": "action", "text": "She steps into the rain.",
                    "start": 0.25, "end": 1.25,
                }],
                "sounds": ["Rain strikes the metal awning."],
                "sound_cues": [{
                    "id": "rain-sound", "text": "Rain strikes the metal awning.",
                    "start": 0.5, "end": 1.5,
                }],
            },
            {
                "id": "extension-shot-2", "start": 2,
                "transition": "the camera cuts to",
                "camera": {"type": "Static Shot", "amplitude": "default", "speed": "default"},
                "steps": [{
                    "id": "reply", "type": "dialogue", "speaker": "The woman", "speaker_id": "S1",
                    "language": "English", "performance": "speech", "text": "Don't follow me.",
                    "delivery": "softly", "voiceover": False, "offscreen": False,
                    "crosses_cut": False, "cutoff": False,
                }],
                "sounds": ["A distant train horn sounds."],
            },
        ]

        document = build_continuation_document(
            parent, "", 5, extension_document=extension,
        )
        prompt = compile_prompt(document)

        self.assertEqual(len(document["shots"]), 2)
        self.assertAlmostEqual(document["shots"][1]["start"], 2 + CONTINUATION_CONTEXT_FRAMES / 24)
        self.assertAlmostEqual(document["shots"][0]["steps"][1]["start"], 0.25 + CONTINUATION_CONTEXT_FRAMES / 24)
        rain = next(item for item in document["shots"][0]["sound_cues"] if item["text"].startswith("Rain strikes"))
        self.assertAlmostEqual(rain["start"], 0.5 + CONTINUATION_CONTEXT_FRAMES / 24)
        self.assertIn("She steps into the rain.", prompt)
        self.assertIn("<d>[English] Don't follow me.</d>", prompt)
        self.assertIn("Rain strikes the metal awning.", prompt)
        self.assertIn("A distant train horn sounds.", prompt)
        self.assertIn("without a pause, reset, reversal", prompt.lower())

    def test_structured_extension_rejects_new_media_references(self):
        extension = default_document()
        extension["references"] = [{
            "id": "picture-1", "kind": "image", "path": "subject.png", "roles": ["subject"],
        }]

        with self.assertRaisesRegex(Exception, "not new media references"):
            build_continuation_document(
                default_document(), "", 5, extension_document=extension,
            )

    def test_output_descriptor_rejects_directory_escape(self):
        with self.assertRaises(ValueError):
            normalize_output_descriptor({
                "filename": "base.mp4",
                "subfolder": "../outside",
                "type": "output",
            })
        self.assertEqual(
            annotated_output_path({"filename": "base.mp4", "subfolder": "video", "type": "output"}),
            "video/base.mp4 [output]",
        )

    @staticmethod
    def _write_video(path, color):
        with av.open(path, mode="w", format="mp4") as container:
            stream = container.add_stream("h264", rate=24)
            stream.width = 64
            stream.height = 64
            stream.pix_fmt = "yuv420p"
            image = np.zeros((64, 64, 3), dtype=np.uint8)
            image[:, :] = color
            for _index in range(24):
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                container.mux(stream.encode(frame))
            container.mux(stream.encode(None))

    def test_lossless_segment_assembly_keeps_every_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "first.mp4")
            second = os.path.join(directory, "second.mp4")
            joined = os.path.join(directory, "joined.mp4")
            self._write_video(first, (255, 0, 0))
            self._write_video(second, (0, 0, 255))

            self.assertAlmostEqual(probe_video(first)["duration"], 1.0, places=3)

            concatenate_media_files([first, second], joined)

            with av.open(joined, mode="r") as container:
                self.assertEqual(container.streams.video[0].frames, 48)
                self.assertAlmostEqual(float(container.duration / av.time_base), 2.0, places=2)


if __name__ == "__main__":
    unittest.main()
