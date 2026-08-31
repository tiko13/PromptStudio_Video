import unittest
from unittest.mock import patch

from video.contracts import default_document
from video.director import (
    _complete_rewrite_requested,
    _validate_extension_plan_timeline,
    build_provider_messages,
    document_fingerprint,
    normalize_project_changeset,
)
from video.extension_planner import _planning_instruction, plan_extension


class ExtensionPlannerTests(unittest.TestCase):
    def test_planner_instruction_triggers_complete_replacement_semantics(self):
        instruction = _planning_instruction("The courier opens the door.")

        self.assertTrue(_complete_rewrite_requested({
            "messages": [{"role": "user", "content": instruction}],
        }))

    def test_planner_uses_project_continuation_policy_and_exact_tail_limit(self):
        request = {
            "document": default_document(),
            "brief": "Continue walking.",
            "scope": "project",
            "extension_planning": True,
            "require_proposal": True,
            "continuation_context": {
                "type": "native_h3_soft_av_extension",
                "authored_tail_duration": 119 / 24,
                "source_final_shot": {},
            },
            "messages": [{"role": "user", "content": "Completely rewrite the entire extension."}],
        }

        messages, _usage = build_provider_messages(request)

        self.assertIn("NATIVE EXTENSION PLANNING", messages[0]["content"])
        self.assertNotIn("Return only selected-shot operations", messages[0]["content"])
        self.assertIn('"authored_tail_duration":4.958333333333333', messages[1]["content"])

    def test_backend_plan_structures_verbatim_dialogue_for_minimax(self):
        parent = default_document()
        parent["shots"][0]["steps"] = [{
            "id": "source-action", "type": "action", "text": "The masked courier starts rising.",
        }]
        brief = 'The masked courier finishes standing and says "We move now." while opening the map.'
        captured = {}

        def fake_director(request, progress_callback=None):
            captured.update(request)
            seed = request["document"]
            changeset = normalize_project_changeset({
                "summary": "Plan the extension",
                "operations": [
                    {
                        "op": "update_project",
                        "replace": True,
                        "fields": {
                            "main_description": brief,
                            "style": "3D CG with stable source lighting.",
                            "overall_soundscape": "Café room tone continues under the map rustle.",
                            "non_diegetic_music": "N/A",
                        },
                    },
                    {
                        "op": "update_shot",
                        "shot_id": seed["shots"][0]["id"],
                        "replace": True,
                        "fields": {
                            "start": 0,
                            "transition": "The same shot continues without a cut.",
                            "composition": "The medium café composition continues from the exact boundary.",
                            "subjects": "The masked courier remains aligned with the table and map.",
                            "environment": "The established café geometry remains unchanged.",
                            "lighting": "The established exposure and practical lighting remain unchanged.",
                            "camera": {
                                "type": "Static Shot", "amplitude": "default",
                                "speed": "default", "target": "the masked courier",
                            },
                            "steps": [
                                {"type": "action", "text": "The masked courier completes the ongoing rise while opening the map."},
                                {
                                    "type": "dialogue", "speaker": "The masked courier",
                                    "speaker_id": "S1", "language": "English", "performance": "speech",
                                    "text": "We move now.", "delivery": "firmly while opening the map",
                                    "voiceover": False, "offscreen": False,
                                    "crosses_cut": False, "cutoff": False,
                                },
                            ],
                            "sounds": [
                                "The paper map rustles in synchronization with the courier opening it."
                            ],
                            "visible_text": [],
                            "notes": "",
                        },
                    },
                ],
            }, document_fingerprint(seed))
            return {
                "status": "ready", "message": "Extension planned.",
                "proposal": changeset, "context_usage": {"prompt_guides": ["base-t2va"]},
            }

        with patch("video.extension_planner.director_chat", side_effect=fake_director):
            result = plan_extension({
                "document": parent,
                "brief": brief,
                "duration_seconds": 5,
                "llm_provider": "koboldcpp",
            })

        self.assertTrue(captured["require_proposal"])
        self.assertTrue(captured["extension_planning"])
        self.assertEqual(captured["scope"], "project")
        self.assertEqual(captured["document"]["references"], [])
        self.assertIn(brief, captured["messages"][0]["content"])
        self.assertIn("<d>[English] We move now.</d>", result["compiled_prompt"])
        self.assertIn("(S1)", result["compiled_prompt"])

    def test_backend_plan_rejects_missing_proposal(self):
        with patch("video.extension_planner.director_chat", return_value={
            "status": "ready", "message": "No plan", "proposal": None,
        }):
            with self.assertRaisesRegex(ValueError, "No plan"):
                plan_extension({
                    "document": default_document(),
                    "brief": "Continue walking.",
                    "duration_seconds": 5,
                })

    def test_planner_validation_rejects_cuts_in_trimmed_away_frames(self):
        document = default_document()
        document["duration_seconds"] = 5
        second = dict(document["shots"][0])
        second["id"] = "shot-2"
        second["start"] = 4.98
        document["shots"].append(second)

        with self.assertRaisesRegex(ValueError, "post-trim authored-tail"):
            _validate_extension_plan_timeline(document, {
                "extension_planning": True,
                "continuation_context": {"authored_tail_duration": 119 / 24},
            })


if __name__ == "__main__":
    unittest.main()
