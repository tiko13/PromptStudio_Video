import unittest

from video.compiler import compile_prompt


def base_document(**overrides):
    value = {
        "version": 1,
        "mode": "auto",
        "duration_seconds": 5,
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
        self.assertIn("[Shot 2] At 00:03.000, the camera cuts to", prompt)

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
        prompt = compile_prompt(value)
        positions = [prompt.index(name) for name in (
            "subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("[reference generation]", prompt)
        self.assertIn("<Subject 1> (appears in [Shot 1]): fully_preserved", prompt)

    def test_voiceover_closes_lips_and_supports_cutoff(self):
        value = base_document()
        value["shots"][0]["dialogue"][0].update({"voiceover": True, "cutoff": True})
        prompt = compile_prompt(value)
        self.assertIn("says in an off-screen voiceover", prompt)
        self.assertIn("<cutoff></d>", prompt)
        self.assertIn("lips remain completely closed", prompt)


if __name__ == "__main__":
    unittest.main()
