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
    def test_manual_prompt_override_is_exact_and_can_be_ignored_for_rebuild(self):
        value = base_document(prompt_override="CUSTOM PROMPT\n\nwith exact formatting")
        self.assertEqual(compile_prompt(value), "CUSTOM PROMPT\n\nwith exact formatting")
        rebuilt = compile_prompt(value, use_override=False)
        self.assertTrue(rebuilt.startswith("integrated_multimodal_description:"))

    def test_t2va_has_three_core_fields_and_no_alignment_instruction(self):
        prompt = compile_prompt(base_document())
        self.assertTrue(prompt.startswith("integrated_multimodal_description: [Shot 1]"))
        self.assertNotIn("A baker opens the street bakery before sunrise.", prompt)
        self.assertIn("The camera pushes in with small amplitude at slow speed toward the bread.", prompt)
        self.assertIn("<d>[English] First batch of the morning.</d>", prompt)
        self.assertIn("\noverall_soundscape:", prompt)
        self.assertIn("\nnon_diegetic_music:", prompt)

    def test_every_camera_type_compiles_to_distinct_physical_motion(self):
        expected = {
            "Zoom In": "camera zooms in", "Zoom Out": "camera zooms out",
            "Push In": "camera pushes in", "Pull Out": "camera pulls out",
            "Pan Left": "camera pans left", "Pan Right": "camera pans right",
            "Truck Left": "camera trucks left", "Truck Right": "camera trucks right",
            "Tilt Up": "camera tilts up", "Tilt Down": "camera tilts down",
            "Pedestal Up": "moves upward on a pedestal",
            "Pedestal Down": "moves downward on a pedestal",
            "Arc Shot": "moves in an arc around the subject",
            "Tracking Shot": "follows the moving subject in a tracking shot",
            "Static Shot": "holds a static shot", "POV": "adopts a POV perspective",
            "Shake Slightly": "shakes slightly", "Shake Strongly": "shakes strongly",
            "Roll Clockwise": "rolls clockwise around the lens axis",
            "Roll Counterclockwise": "rolls counterclockwise around the lens axis",
        }
        for camera_type, phrase in expected.items():
            with self.subTest(camera_type=camera_type):
                value = base_document()
                value["shots"][0]["camera"] = {
                    "type": camera_type, "amplitude": "small", "speed": "slow",
                    "target": "the baker",
                }
                prompt = compile_prompt(value)
                self.assertIn(phrase, prompt)
                self.assertIn("toward the baker", prompt)

    def test_common_transitions_compile_at_exact_cut_time(self):
        transitions = {
            "the camera cuts to": "the camera cuts to",
            "the shot cross-dissolves to": "the shot cross-dissolves to",
            "the shot fades to": "the shot fades to",
            "the shot wipes to": "the shot wipes to",
            "the shot transitions to": "the shot transitions to",
            "the shot changes to": "the shot changes to",
            "the shot switches to": "the shot switches to",
        }
        for transition, phrase in transitions.items():
            with self.subTest(transition=transition):
                value = base_document(duration_seconds=8)
                value["shots"].append({
                    "id": "shot-2", "start": 3.25, "transition": transition,
                    "composition": "A close-up frames the letter.",
                    "subjects": "The baker holds the letter.",
                    "environment": "The bakery counter remains behind it.",
                    "lighting": "Warm counter light.",
                    "steps": [{"type": "action", "text": "The letter opens."}],
                })
                prompt = compile_prompt(value)
                self.assertIn(f"At 00:03.250, {phrase}", prompt)

    def test_base_modes_reject_reference_tokens_without_ref2va_sources(self):
        value = base_document()
        value["shots"][0]["composition"] = "<Subject 1> opens the shutters."
        with self.assertRaisesRegex(PromptDocumentError, "require REF2VA source definitions"):
            compile_prompt(value)

    def test_i2va_instruction_is_first_and_dialogue_is_verbatim(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
        }])
        value["shots"][0]["dialogue"][0]["text"] = "Don't rewrite THIS!"
        prompt = compile_prompt(value)
        self.assertTrue(prompt.startswith("For the target video, at 0.00 seconds"))
        self.assertIn("<d>[English] Don't rewrite THIS!</d>", prompt)

    def test_ref2va_external_style_overrides_only_first_frame_treatment_layer(self):
        value = base_document(
            mode="ref2va",
            references=[
                {"kind": "image", "path": "first.png", "roles": ["first_frame"]},
                {"kind": "image", "path": "last.png", "roles": ["last_frame"]},
                {"kind": "image", "path": "style.png", "roles": ["style"]},
            ],
            task_types=["keyframe completion", "reference generation"],
            subject_definitions=[
                {"label": "Picture 1", "text": "is the supplied first frame of [Shot 1]."},
                {"label": "Picture 2", "text": "is the supplied last frame of [Shot 1]."},
                {"label": "Subject 3", "text": "is the flat visual style shown in <Picture 3>."},
            ],
            summary="The target interpolates <Picture 1> to <Picture 2> using <Subject 3>.",
            retention_analysis=[
                {
                    "label": "<Picture 1>", "where": "[Shot 1]", "relationship": "fully_preserved",
                    "detail": "The opening composition is retained.",
                },
                {
                    "label": "<Picture 2>", "where": "[Shot 1]", "relationship": "fully_preserved",
                    "detail": "The ending composition is retained.",
                },
                {
                    "label": "<Subject 3>", "where": "[Shot 1]", "relationship": "attribute_transfer",
                    "detail": "The visual treatment is transferred.",
                },
            ],
        )
        value["shots"][0]["steps"] = [{
            "type": "action", "text": "The motion follows <Subject 3> and lands on <Picture 2>."
        }]
        value["shots"][0].pop("dialogue")
        prompt = compile_prompt(value)
        self.assertIn(
            "The subjects, composition, scene, lighting, clothing, colors, key objects, and spatial "
            "relationships established by <Picture 1> remain fully preserved.",
            prompt,
        )
        self.assertIn("The visual treatment throughout the target video follows <Subject 3>.", prompt)
        self.assertNotIn("The style, subjects, composition", prompt)

    def test_i2va_generic_onscreen_speaker_is_anchored_to_sole_visible_subject(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
            "subject_candidates": [{"name": "young woman", "location": "center"}],
        }])
        value["shots"][0]["dialogue"][0]["speaker"] = "The speaker"

        prompt = compile_prompt(value)

        self.assertIn(
            "The young woman shown in <Picture 1> (S1) says: "
            "<d>[English] First batch of the morning.</d>",
            prompt,
        )
        self.assertNotIn("The speaker (S1)", prompt)

    def test_i2va_generic_voiceover_is_not_bound_to_visible_subject(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
            "subject_candidates": [{"name": "young woman", "location": "center"}],
        }])
        value["shots"][0]["dialogue"][0].update({
            "speaker": "The speaker", "voiceover": True,
        })

        prompt = compile_prompt(value)

        self.assertIn("The speaker (S1) says in an off-screen voiceover", prompt)
        self.assertNotIn("The young woman shown in <Picture 1>", prompt)

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

    def test_i2va_reanchors_same_person_and_exact_wardrobe_after_cut(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
            "subject_candidates": [{
                "name": "young woman", "location": "center",
                "grounded_attributes": {
                    "hair": "long, straight, brown",
                    "face": "fair skin",
                    "clothing": "red mini dress",
                    "footwear": "barefoot",
                },
            }],
        }])
        value["shots"].append({
            "id": "shot-2", "start": 2.5, "transition": "the camera cuts to",
            "composition": "A medium-wide shot in a luxury bedroom.",
            "subjects": "The same girl stands centrally in the room.",
            "environment": "A luxury bedroom with plush furnishings.",
            "lighting": "Soft diffused ambient light.",
            "camera": {"type": "Arc Shot", "amplitude": "default", "speed": "slow", "target": "the girl"},
            "steps": [{"type": "action", "text": "She stands gracefully."}],
            "sounds": [
                "Her silk robe rustles softly as the camera circles around her.",
                "Her high heels click with each step.",
                "Her silver bracelet jingles as she raises one hand.",
            ],
        })

        prompt = compile_prompt(value)

        shot_two = prompt.split("[Shot 2]", 1)[1]
        self.assertIn("the same person shown in <Picture 1>", shot_two)
        self.assertIn("hair (long, straight, brown)", shot_two)
        self.assertIn("clothing (red mini dress)", shot_two)
        self.assertIn("footwear (barefoot)", shot_two)
        self.assertIn("exact wardrobe design, fit, sleeve and hem lengths", shot_two)
        self.assertIn("there is no wardrobe change", shot_two)
        self.assertIn("Her red mini dress rustles softly", shot_two)
        self.assertNotIn("silk robe", shot_two)
        self.assertIn("Her bare footsteps land softly in exact sync with each step", shot_two)
        self.assertNotIn("high heels", shot_two)
        self.assertNotIn("bracelet", shot_two)
        shot_one = prompt.split("[Shot 1]", 1)[1].split("[Shot 2]", 1)[0]
        self.assertNotIn("hair (long, straight, brown)", shot_one)
        self.assertNotIn("clothing (red mini dress)", shot_one)

    def test_i2va_explicit_wardrobe_change_keeps_identity_without_old_clothes(self):
        value = base_document(references=[{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
            "subject_candidates": [{
                "name": "young woman",
                "grounded_attributes": {
                    "hair": "long brown hair", "clothing": "red mini dress",
                },
            }],
        }])
        value["shots"].append({
            "id": "shot-2", "start": 2.5,
            "subjects": (
                "The same woman preserves her face and body but deliberately changes only her "
                "wardrobe from the red mini dress to a new blue suit and black dress shoes."
            ),
            "steps": [{"type": "action", "text": "She adjusts the jacket."}],
        })

        shot_two = compile_prompt(value).split("[Shot 2]", 1)[1]

        self.assertIn("the same person shown in <Picture 1>", shot_two)
        self.assertIn("hair (long brown hair)", shot_two)
        self.assertNotIn("clothing (red mini dress)", shot_two)
        self.assertNotIn("there is no wardrobe change", shot_two)

    def test_complete_silence_suppresses_every_audio_layer(self):
        value = base_document(complete_silence=True)
        value["shots"][0]["action"] = "The baker opens the shutters."
        value["shots"][0]["sounds"] = ["The shutters scrape loudly."]

        prompt = compile_prompt(value)

        self.assertIn("The baker opens the shutters.", prompt)
        self.assertNotIn("First batch of the morning", prompt)
        self.assertNotIn("shutters scrape", prompt.casefold())
        self.assertIn("overall_soundscape: N/A", prompt)
        self.assertIn("non_diegetic_music: N/A", prompt)

    def test_ref2va_first_frame_keeps_anchored_visuals_pixel_owned(self):
        value = base_document(
            references=[
                {"kind": "image", "path": "first.png", "roles": ["first_frame"]},
                {"kind": "image", "path": "woman.png", "roles": ["subject"]},
            ],
            task_types=["keyframe completion", "reference generation"],
            subject_definitions=[
                {"label": "Picture 1", "text": "is the supplied opening frame for [Shot 1]."},
                {"label": "Subject 1", "text": "is the woman in <Picture 2>."},
            ],
            summary="The target begins from <Picture 1> as <Subject 1> enters [Shot 1].",
            retention_analysis=[
                {"label": "<Picture 1>", "where": "anchors [Shot 1]", "relationship": "fully_preserved", "detail": "Its opening pixels are retained."},
                {"label": "<Subject 1>", "where": "appears in [Shot 1]", "relationship": "fully_preserved", "detail": "Her identity is retained."},
            ],
        )
        value["shots"][0].update({
            "composition": "Invented composition that competes with the opening frame.",
            "subjects": "Invented description of the opening subject.",
            "environment": "Invented opening environment.",
            "lighting": "Invented opening lighting.",
            "action": "<Subject 1> enters from frame right.",
        })

        prompt = compile_prompt(value)

        self.assertIn("established by <Picture 1> remain fully preserved", prompt)
        self.assertIn(
            "<Picture 1> remains the sole source for this shot's background, environment, lighting, "
            "composition, camera framing, and spatial relationships",
            prompt,
        )
        self.assertIn(
            "<Picture 2> supplies only <Subject 1>'s identity and appearance",
            prompt,
        )
        self.assertIn("with no source-picture scene or framing transferred", prompt)
        self.assertIn("<Subject 1> enters from frame right", prompt)
        self.assertNotIn("Invented composition", prompt)
        self.assertNotIn("Invented opening environment", prompt)

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
        self.assertIn("<Picture 2> (from [Shot 2]) aligns with the 5.17-second mark", prompt)
        self.assertIn("[Shot 2] At 00:03.000, the camera cuts to a new view.", prompt)
        self.assertNotIn("Live-action, cinematic", prompt)
        self.assertNotIn("The cyclist starts beside a bicycle", prompt)
        self.assertIn("established by <Picture 1> remain fully preserved", prompt)

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

    def test_ref2va_rejects_undefined_subject_tokens_in_prompt_content(self):
        value = base_document(
            references=[{"kind": "image", "path": "subject.png", "roles": ["subject"]}],
            subject_definitions=[{
                "label": "Subject 1", "text": "is the baker in <Picture 1>.",
            }],
            summary="The target follows <Subject 1>.",
            retention_analysis=[{
                "label": "<Subject 1>", "where": "appears in [Shot 1]",
                "relationship": "fully_preserved", "detail": "The baker is retained.",
            }],
        )
        value["shots"][0]["action"] = "<Subject 3> opens the shutters beside <Subject 1>."
        with self.assertRaisesRegex(PromptDocumentError, "undefined reference labels.*subject 3"):
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

    def test_video_edit_replacement_starts_immediately_and_uses_source_motion(self):
        value = base_document(
            mode="ref2va",
            main_description=(
                "The target video is an edited version of <Video 1>, replacing the original performer with "
                "<Subject 1> throughout. The camera work is preserved exactly."
            ),
            references=[
                {"kind": "video", "path": "source.mp4", "roles": ["video_edit"]},
                {"kind": "image", "path": "soldier.png", "roles": ["subject"]},
            ],
            task_types=["reference generation", "video editing"],
            subject_definitions=[
                {"label": "Video 1", "text": "is the referenced video source."},
                {"label": "Subject 1", "text": "is only the person in <Picture 1>."},
            ],
            summary="The target video uses <Video 1> and <Subject 1>.",
            retention_analysis=[
                {"label": "<Video 1>", "where": "appears in [Shot 1]", "relationship": "fully_preserved", "detail": "The video is retained."},
                {"label": "<Subject 1>", "where": "appears in [Shot 1]", "relationship": "fully_preserved", "detail": "The subject is retained."},
            ],
        )
        value["shots"][0].update({
            "composition": "A medium-wide shot frames <Subject 1> in <Video 1>.",
            "subjects": "<Subject 1>: a tactical operator with a rifle, standing still with the right arm raised.",
            "camera": {"type": "Static Shot", "amplitude": "default", "speed": "default", "target": ""},
            "steps": [{
                "type": "action",
                "text": "<Subject 1> performs the same movement as the source performer in <Video 1>.",
            }],
        })

        prompt = compile_prompt(value)

        self.assertIn("The target video is an edited version of <Video 1>.", prompt)
        self.assertIn("From the first visible frame through the final frame", prompt)
        self.assertIn("<Video 1> supplies every body motion, pose change, gesture", prompt)
        self.assertIn("the subject reference supplies no action or static pose", prompt)
        self.assertIn("<Subject 1> (appears in [Shot 1]): attribute_transfer", prompt)
        self.assertIn("static pose, held objects, and action do not transfer", prompt)
        self.assertNotIn("rifle", prompt)
        self.assertNotIn("right arm raised", prompt)
        self.assertNotIn("camera holds a static shot", prompt)

    def test_voiceover_closes_lips_and_supports_cutoff(self):
        value = base_document()
        value["shots"][0]["dialogue"][0].update({"voiceover": True, "cutoff": True})
        prompt = compile_prompt(value)
        self.assertIn("says in an off-screen voiceover", prompt)
        self.assertIn("<cutoff></d>", prompt)
        self.assertIn("lips remain completely closed", prompt)

    def test_singing_uses_the_dialogue_block_and_singing_verb(self):
        value = base_document()
        value["shots"][0]["dialogue"][0].update({"performance": "singing", "text": "Keep the light on!"})
        prompt = compile_prompt(value)
        self.assertIn("(S1) sings: <d>[English] Keep the light on!</d>", prompt)

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
