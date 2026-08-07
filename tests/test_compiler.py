import unittest

from video.compiler import compile_prompt
from video.contracts import PromptDocumentError


def base_document(**overrides):
    value = {
        "version": 1,
        "mode": "auto",
        "duration_seconds": 5,
        "main_description": "A baker opens the street bakery before sunrise",
        "style": "Live-action, cinematic",
        "shots": [{
            "id": "shot-1",
            "start": 0,
            "composition": "A medium-wide shot frames a baker opening the shutters",
            "camera": {"type": "Push In", "amplitude": "small", "speed": "slow", "target": "the bread"},
            "dialogue": [{
                "speaker": "The baker with a calm, raspy voice",
                "speaker_id": "S1",
                "language": "English",
                "text": "First batch of the morning.",
            }],
        }],
        "references": [],
        "overall_soundscape": "Wooden shutters scrape open over a quiet street.",
        "non_diegetic_music": "A soft acoustic-guitar pattern at a moderate tempo.",
    }
    value.update(overrides)
    return value


class CompilerTests(unittest.TestCase):
    def test_t2va_has_three_core_fields_and_no_alignment_instruction(self):
        prompt = compile_prompt(base_document())
        self.assertTrue(prompt.startswith("integrated_multimodal_description: [Shot 1]"))
        self.assertNotIn("A baker opens the street bakery before sunrise.", prompt)
        self.assertIn("The camera pushes in with small amplitude at slow speed toward the bread.", prompt)
        self.assertIn("<d>[English] First batch of the morning.</d>", prompt)
        self.assertIn("\noverall_soundscape:", prompt)
        self.assertIn("\nnon_diegetic_music:", prompt)

    def test_i2va_instruction_is_first_and_dialogue_is_verbatim(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
        }])
        value["shots"][0]["dialogue"][0]["text"] = "Don't rewrite THIS!"
        prompt = compile_prompt(value)
        self.assertTrue(prompt.startswith("For the target video, at 0.00 seconds"))
        self.assertIn("<d>[English] Don't rewrite THIS!</d>", prompt)

    def test_i2va_first_shot_uses_pixels_for_anchored_visual_details(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
        }])
        value["shots"][0].update({
            "subjects": "An invented subject description",
            "environment": "An invented setting",
            "lighting": "Invented sunset lighting",
            "action": "The subject raises one hand.",
        })

        prompt = compile_prompt(value)

        self.assertNotIn("Live-action, cinematic", prompt)
        self.assertNotIn("A medium-wide shot", prompt)
        self.assertNotIn("An invented subject description", prompt)
        self.assertNotIn("An invented setting", prompt)
        self.assertNotIn("Invented sunset lighting", prompt)
        self.assertIn("established by <Picture 1> remain fully preserved", prompt)
        self.assertIn("The subject raises one hand.", prompt)
        self.assertIn("The camera pushes in", prompt)

    def test_main_description_is_planning_only_and_not_compiled(self):
        prompt = compile_prompt(base_document())
        self.assertNotIn("A baker opens the street bakery before sunrise.", prompt)
        self.assertIn(
            "integrated_multimodal_description: [Shot 1] Live-action, cinematic.",
            prompt,
        )

    def test_fl2va_uses_effective_duration_and_final_shot(self):
        value = base_document(
            duration_seconds=5,
            references=[
                {"kind": "image", "path": "first.png", "roles": ["first_frame"]},
                {"kind": "image", "path": "last.png", "roles": ["last_frame"]},
            ],
            shots=[
                {"start": 0, "composition": "The cyclist starts beside a bicycle."},
                {"start": 3, "transition": "the camera cuts to", "action": "The umbrella opens."},
            ],
        )
        prompt = compile_prompt(value)
        self.assertIn("Picture 2 (from Shot 2) aligns with the 5.17-second mark", prompt)
        self.assertIn("[Shot 2] At 00:03.000, the camera cuts to a new view.", prompt)

    def test_l2va_uses_last_frame_instruction(self):
        prompt = compile_prompt(base_document(references=[{
            "kind": "image", "path": "last.png", "roles": ["last_frame"],
        }]))
        self.assertTrue(prompt.startswith("How the reference pictures align"))
        self.assertIn("<Picture 1> (from [Shot 1])", prompt)

    def test_ref2va_uses_six_ordered_sections(self):
        value = base_document(
            references=[{"kind": "image", "path": "subject.png", "roles": ["subject"]}],
            subject_definitions=[{"label": "Subject 1", "text": "is the baker in <Picture 1>."}],
            summary="The target video follows <Subject 1> through the bakery.",
            retention_analysis=[{
                "label": "<Subject 1>", "where": "appears in [Shot 1]",
                "relationship": "fully_preserved", "detail": "The baker's identity is retained.",
            }],
        )
        value["shots"][0]["composition"] = "A medium-wide shot frames <Subject 1> opening the shutters."
        prompt = compile_prompt(value)
        positions = [prompt.index(name) for name in (
            "subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("[reference generation]", prompt)
        self.assertIn("<Subject 1> (appears in [Shot 1]): fully_preserved", prompt)
        self.assertIn("<Subject 1> is the baker in <Picture 1>.", prompt)

    def test_ref2va_rejects_hollow_reference_sections(self):
        value = base_document(
            references=[{"kind": "image", "path": "subject.png", "roles": ["subject"]}],
        )
        with self.assertRaisesRegex(PromptDocumentError, "subject_definitions is empty"):
            compile_prompt(value)

    def test_ref2va_rejects_unresolved_visual_trait_placeholders(self):
        value = base_document(
            references=[{"kind": "image", "path": "subject.png", "roles": ["subject"]}],
            subject_definitions=[{
                "label": "Subject 1",
                "text": "is the woman in <Picture 1>, with the observed identity traits.",
            }],
            summary="The target video follows <Subject 1>.",
            retention_analysis=[{
                "label": "<Subject 1>", "where": "appears in [Shot 1]",
                "relationship": "fully_preserved", "detail": "Her appearance is retained.",
            }],
        )
        value["shots"][0]["subjects"] = "<Subject 1>, the woman from <Picture 1>."
        with self.assertRaisesRegex(PromptDocumentError, "unresolved visual-trait placeholder"):
            compile_prompt(value)

    def test_ref2va_rejects_structured_reference_to_missing_shot(self):
        value = base_document(
            references=[{"kind": "image", "path": "subject.png", "roles": ["subject"]}],
            subject_definitions=[{
                "label": "Subject 1",
                "text": "is the blonde woman in <Picture 1>, wearing a white dress.",
            }],
            summary="The target video follows <Subject 1> in [Shot 1].",
            retention_analysis=[{
                "label": "<Subject 1>", "where": "appears in [Shot 1] and [Shot 2]",
                "relationship": "fully_preserved", "detail": "Her appearance is retained.",
            }],
        )
        value["shots"][0]["subjects"] = "<Subject 1> stands beside the chair."
        with self.assertRaisesRegex(PromptDocumentError, r"references missing \[Shot 2\]"):
            compile_prompt(value)

    def test_ref2va_tracks_multiple_references_across_multiple_shots(self):
        value = base_document(
            references=[
                {"kind": "image", "path": "woman.png", "roles": ["subject"]},
                {"kind": "image", "path": "office.png", "roles": ["scene"]},
                {"kind": "image", "path": "board.png", "roles": ["storyboard"]},
            ],
            subject_definitions=[
                {"label": "Subject 1", "text": "is the blonde woman in <Picture 1>, wearing a white dress."},
                {"label": "Subject 2", "text": "is the modern office environment in <Picture 2>."},
                {"label": "Picture 3", "text": "is the storyboard reference for [Shot 1] and [Shot 2]."},
            ],
            summary=(
                "The target video follows <Subject 1> through <Subject 2>, using <Picture 3> "
                "for the two-shot composition."
            ),
            retention_analysis=[
                {"label": "<Subject 1>", "where": "appears in [Shot 1] and [Shot 2]", "relationship": "fully_preserved", "detail": "Her identity and white dress are retained."},
                {"label": "<Subject 2>", "where": "appears in [Shot 1] and [Shot 2]", "relationship": "fully_preserved", "detail": "The office layout and materials are retained."},
                {"label": "<Picture 3>", "where": "maps [Shot 1] and [Shot 2]", "relationship": "fully_preserved", "detail": "Its viewpoint and shot order are retained."},
            ],
            shots=[
                {
                    "id": "shot-1", "start": 0,
                    "composition": "<Picture 3> establishes a medium shot of <Subject 1> inside <Subject 2>.",
                    "subjects": "<Subject 1> sits in a red armchair.",
                    "environment": "<Subject 2> surrounds her.",
                },
                {
                    "id": "shot-2", "start": 3,
                    "composition": "The second panel of <Picture 3> frames <Subject 1> in <Subject 2>.",
                    "subjects": "<Subject 1> stands beside the chair.",
                    "environment": "The same <Subject 2> remains visible.",
                },
            ],
        )
        prompt = compile_prompt(value)
        self.assertIn("<Subject 1> is the blonde woman in <Picture 1>", prompt)
        self.assertIn("<Subject 2> is the modern office environment in <Picture 2>", prompt)
        self.assertIn("<Picture 3> is the storyboard reference", prompt)
        self.assertGreaterEqual(prompt.count("[Shot 1]"), 5)
        self.assertIn("[Shot 2] At 00:03.000", prompt)

    def test_ref2va_tracks_video_and_audio_reference_sources(self):
        value = base_document(
            references=[
                {"kind": "video", "path": "source.mp4", "roles": ["video_edit"]},
                {"kind": "audio", "path": "voice.wav", "roles": ["audio_reference"]},
            ],
            task_types=["video editing", "audio reference"],
            subject_definitions=[
                {"label": "Video 1", "text": "is the source video whose original action and cut timing are retained."},
                {"label": "Audio 1", "text": "is the speaker's low, calm voice-timbre reference."},
            ],
            summary=(
                "The target edits <Video 1> while preserving its action and timing, "
                "and gives the speaker the low, calm timbre from <Audio 1>."
            ),
            retention_analysis=[
                {"label": "<Video 1>", "where": "guides [Shot 1]", "relationship": "fully_preserved", "detail": "Its action and cut timing are retained."},
                {"label": "<Audio 1>", "where": "guides the speaker in [Shot 1]", "relationship": "reference", "detail": "Its low, calm vocal timbre is reused."},
            ],
            main_description="The target edits <Video 1> while following its existing action.",
            overall_soundscape="The speaker uses the low, calm vocal timbre from <Audio 1>.",
        )
        value["shots"][0]["action"] = "<Video 1> supplies the source action and cut timing."
        prompt = compile_prompt(value)
        self.assertNotIn("The target edits <Video 1> while following its existing action.", prompt)
        self.assertIn("[video editing + audio reference]", prompt)
        self.assertIn("<Video 1> is the source video", prompt)
        self.assertIn("<Audio 1> is the speaker's low, calm voice-timbre reference", prompt)
        self.assertIn("<Video 1> (guides [Shot 1]): fully_preserved", prompt)
        self.assertIn("<Audio 1> (guides the speaker in [Shot 1]): reference", prompt)

    def test_voiceover_closes_lips_and_supports_cutoff(self):
        value = base_document()
        value["shots"][0]["dialogue"][0].update({"voiceover": True, "cutoff": True})
        prompt = compile_prompt(value)
        self.assertIn("says in an off-screen voiceover", prompt)
        self.assertIn("<cutoff></d>", prompt)
        self.assertIn("lips remain completely closed", prompt)

    def test_shot_steps_compile_in_their_exact_chronological_order(self):
        value = base_document()
        value["shots"][0].pop("dialogue")
        value["shots"][0]["steps"] = [
            {"type": "action", "text": "Character A picks up the letter."},
            {
                "type": "dialogue", "speaker": "Character A", "speaker_id": "S1",
                "language": "English", "text": "This came yesterday.",
            },
            {"type": "action", "text": "She recognizes the handwriting."},
            {
                "type": "dialogue", "speaker": "Character A", "speaker_id": "S1",
                "language": "English", "text": "I thought he was dead.",
            },
        ]
        prompt = compile_prompt(value)
        ordered = [
            "Character A picks up the letter.",
            "<d>[English] This came yesterday.</d>",
            "She recognizes the handwriting.",
            "<d>[English] I thought he was dead.</d>",
        ]
        positions = [prompt.index(item) for item in ordered]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
