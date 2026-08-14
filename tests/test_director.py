import copy
import unittest
from unittest.mock import patch
import json

from video.contracts import normalize_document
from video.director import (
    CHANGESET_BEGIN,
    CHANGESET_END,
    PROJECT_SYSTEM_MESSAGE,
    SHOT_SYSTEM_MESSAGE,
    VISION_GROUNDING_SYSTEM_MESSAGE,
    build_provider_messages,
    compact_project_context,
    director_chat,
    document_fingerprint,
    parse_director_response,
    preview_changeset,
    _prompt_writing_guide,
    _reference_prompt_writing_guide,
    _base_prompt_writing_guide_for_mode,
    _shared_base_prompt_writing_guide,
    _validate_reference_only_preservation,
    _validate_first_frame_proposal_lock,
    _validate_i2va_anchor_preservation,
    _validate_protected_sequence_content,
    _validate_requested_project_result,
    _validate_speaker_id_prose,
    _validate_action_sound_separation,
    _validate_structured_shot_grammar,
    _validate_requested_reference_coverage,
    _validate_reference_prompt_length,
    _validate_sound_entries,
    _canonicalize_retention_shot_mentions,
    _canonicalize_reference_definition_grammar,
    _audible_sound,
    _validate_requested_dialogue_mechanics,
    _validate_requested_camera_mechanics,
    _validate_requested_exact_literals,
    _validate_complete_project_placeholders,
    _canonicalize_placeholder_camera_targets,
    _subject_definitions,
    _retention_analysis,
    _complete_grounded_reference_semantics,
    _validate_subject_only_reference_prose,
    _validate_requested_step_order,
    _enrich_reference_definition_placeholders,
    _parse_vision_grounding,
    _preserve_reference_only_request,
    _proposal_retry_messages,
    _proposal_requested,
    _restrict_first_frame_proposal,
    _restrict_reference_only_proposal,
    _restrict_ungrounded_audio_proposal,
    _generate_with_context_fallback,
    _canonicalize_requested_dialogue_literals,
    _enforce_requested_reference_coverage,
)


def director_document():
    return normalize_document({
        "version": 1,
        "mode": "auto",
        "duration_seconds": 8,
        "style": "Live-action, cinematic",
        "shots": [
            {
                "id": "shot-1",
                "start": 0,
                "steps": [{"type": "action", "text": "The woman unfolds a letter."}, {
                    "type": "dialogue",
                    "speaker": "The woman",
                    "speaker_id": "S1",
                    "language": "English",
                    "text": "Do not rewrite this.",
                }],
                "visible_text": ["Central Station"],
            },
            {"id": "shot-2", "start": 4, "steps": [
                {"type": "action", "text": "She looks toward the window."},
            ]},
        ],
        "references": [],
    })


def action_text(shot):
    return " ".join(
        step["text"] for step in shot.get("steps") or []
        if step.get("type") == "action" and step.get("text")
    )


def dialogue_steps(shot):
    return [step for step in shot.get("steps") or [] if step.get("type") == "dialogue"]


def provider_context(messages):
    marker = "Current production context (reference data):\n"
    return json.loads(messages[0]["content"].split(marker, 1)[1])


class DirectorTests(unittest.TestCase):
    @staticmethod
    def i2va_document():
        value = director_document()
        value["references"] = [{
            "kind": "image", "path": "first.png", "roles": ["first_frame"],
        }]
        return normalize_document(value)

    def test_reference_contract_has_no_literal_appearance_example(self):
        for system_message in (SHOT_SYSTEM_MESSAGE, PROJECT_SYSTEM_MESSAGE):
            self.assertNotIn("with the observed identity traits.", system_message)
            self.assertNotIn("concrete visible identity traits observed in that image", system_message)
            self.assertNotIn("shoulder-length wavy black hair", system_message)
            self.assertNotIn("teal linen blouse", system_message)
            self.assertIn("subject_registry", system_message)
            self.assertIn("explicit_subject_attributes", system_message)
            self.assertNotIn("private_subject_bindings", system_message)
            self.assertIn("When exactly one compatible reference exists", system_message)
            self.assertIn("When multiple compatible references exist, never guess", system_message)
            self.assertIn("steps array is its only writable performance sequence", system_message)
            self.assertIn("Never emit the legacy action or dialogue mirror fields", system_message)

    def test_context_exhaustion_retries_once_with_low_thinking(self):
        with patch(
            "video.director.generate_chat",
            side_effect=[
                RuntimeError("KoboldCpp exhausted the available-context Director response budget."),
                "valid response",
            ],
        ) as generate:
            result = _generate_with_context_fallback(
                {"thinking_mode": "High"}, [{"role": "user", "content": "Build it."}], []
            )
        self.assertEqual(result, "valid response")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args_list[0].args[0]["thinking_mode"], "High")
        self.assertEqual(generate.call_args_list[1].args[0]["thinking_mode"], "Low")

    def test_non_context_provider_errors_do_not_retry(self):
        with patch(
            "video.director.generate_chat", side_effect=RuntimeError("KoboldCpp is unreachable")
        ) as generate:
            with self.assertRaisesRegex(RuntimeError, "unreachable"):
                _generate_with_context_fallback(
                    {"thinking_mode": "High"}, [{"role": "user", "content": "Build it."}], []
                )
        self.assertEqual(generate.call_count, 1)

    def test_both_directors_make_concurrency_and_sync_explicit(self):
        for system_message in (SHOT_SYSTEM_MESSAGE, PROJECT_SYSTEM_MESSAGE):
            self.assertIn("TEMPORAL INTENT, CONCURRENCY, AND SYNCHRONIZATION", system_message)
            self.assertIn("She smiles throughout the wave", system_message)
            self.assertIn("Do not split concurrent behaviors into adjacent action steps", system_message)
            self.assertIn("Dialogue and singing are timeline events, not pauses in the visuals", system_message)
            self.assertIn("MiniMax speaker IDs are annotations, never character names", system_message)
            self.assertIn("a silent character receives no speaker ID", system_message)
            self.assertIn("Never use bare `S1`", system_message)
            self.assertIn("Every synchronized sounds item must identify its audible source", system_message)
            self.assertIn("Do not rely on the sounds array's position to imply timing", system_message)
            self.assertIn("Use relative synchronization cues rather than per-event timestamps", system_message)

    def test_speaker_ids_are_rejected_outside_dialogue_steps(self):
        document = director_document()
        document["shots"][0]["subjects"] = "A silent baker (S1)."
        document["shots"][0]["steps"][0]["text"] = "S1 opens the shutters."
        with self.assertRaisesRegex(ValueError, "speaker IDs may appear only"):
            _validate_speaker_id_prose(document)

        document = director_document()
        _validate_speaker_id_prose(document)

    def test_action_and_sound_cannot_duplicate_the_same_sentence(self):
        document = director_document()
        document["shots"][0]["sounds"] = ["The wooden shutter creaks open."]
        document["shots"][0]["steps"][0]["text"] = "The wooden shutter creaks open."
        with self.assertRaisesRegex(ValueError, "duplicates the same event sentence"):
            _validate_action_sound_separation(document)

        document["shots"][0]["steps"][0]["text"] = "The baker opens the wooden shutter."
        _validate_action_sound_separation(document)

    def test_structured_shot_fields_reject_compiled_fragments_and_bare_sources(self):
        document = director_document()
        document["shots"][0]["steps"][0]["text"] = "[Shot 1] She turns toward <Picture 1>."
        with self.assertRaisesRegex(ValueError, "embeds compiled prompt grammar"):
            _validate_structured_shot_grammar(document)

        document["shots"][0]["steps"][0]["text"] = "She lands on Picture 1."
        with self.assertRaisesRegex(ValueError, "bare source label"):
            _validate_structured_shot_grammar(document)

        document["shots"][0]["steps"][0]["text"] = "She lands on <Picture 1>."
        _validate_structured_shot_grammar(document)

    def test_non_audible_states_are_rejected_from_sounds(self):
        document = director_document()
        document["shots"][0]["sounds"] = ["The chair creaks, followed by silence."]
        with self.assertRaisesRegex(ValueError, "only audible events"):
            _validate_sound_entries(document, {})

        self.assertEqual(
            _audible_sound("Footsteps continue while the umbrella remains closed and silent."),
            "Footsteps continue",
        )
        self.assertEqual(_audible_sound("Exactly as her weight shifts to standing."), "")

    def test_sound_validation_rejects_ungrounded_stock_foley_and_ambience(self):
        document = normalize_document(director_document())
        document["shots"][0]["sounds"] = ["Her silver bracelet jingles as she waves."]
        with self.assertRaisesRegex(ValueError, "ungrounded.*jewelry"):
            _validate_sound_entries(document, {
                "messages": [{"role": "user", "content": "Have the baker wave."}],
            })

        document = normalize_document(director_document())
        document["overall_soundscape"] = "A breeze rustles unseen curtains."
        with self.assertRaisesRegex(ValueError, "overall_soundscape.*curtains|overall_soundscape.*wind"):
            _validate_sound_entries(document, {
                "messages": [{"role": "user", "content": "Keep the existing bakery sounds."}],
            })

    def test_sound_validation_accepts_sources_grounded_in_request_and_shots(self):
        document = normalize_document(director_document())
        document["shots"][0]["environment"] = "Rain falls outside the bakery window."
        document["shots"][0]["sounds"] = [
            "Rain taps against the window throughout her movement."
        ]
        document["overall_soundscape"] = "Rain and the window tapping continue under the action."
        _validate_sound_entries(document, {
            "messages": [{"role": "user", "content": "Add rain at the bakery window."}],
        })

    def test_ungrounded_audio_sanitizer_drops_stock_foley_and_soundscape_clauses(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"}, "summary": "Clean the audio",
            "operations": [{
                "op": "update_project", "fields": {
                    "overall_soundscape": (
                        "Paper rustles as she opens the letter. A breeze rustles unseen curtains."
                    ),
                },
            }, {
                "op": "update_shot", "shot_id": "shot-1", "fields": {
                    "sounds": [
                        "Paper rustles as she opens the letter.",
                        "Her silver bracelet jingles.",
                    ],
                },
            }],
        }
        cleaned = _restrict_ungrounded_audio_proposal(document, proposal, {
            "messages": [{"role": "user", "content": "Keep the letter's paper rustle."}],
        })
        self.assertEqual(
            cleaned["operations"][1]["fields"]["sounds"],
            ["Paper rustles as she opens the letter."],
        )
        self.assertEqual(
            cleaned["operations"][0]["fields"]["overall_soundscape"],
            "Paper rustles as she opens the letter.",
        )

        shutter_document = normalize_document(director_document())
        shutter_document["shots"][0]["steps"][0]["text"] = "The baker opens the shutters."
        shutter_proposal = {
            "base_document_hash": document_fingerprint(shutter_document),
            "scope": {"type": "project"}, "summary": "Sound the shutters",
            "operations": [{
                "op": "update_shot", "shot_id": "shot-1", "fields": {
                    "sounds": ["The wooden doors scrape and creak as they open."],
                },
            }],
        }
        repaired = _restrict_ungrounded_audio_proposal(
            shutter_document, shutter_proposal,
            {"messages": [{"role": "user", "content": "The baker opens the shutters."}]},
        )
        self.assertEqual(
            repaired["operations"][0]["fields"]["sounds"],
            ["The wooden shutters scrape and creak as they open."],
        )

        latch_document = normalize_document(director_document())
        latch_document["shots"][0]["steps"][0]["text"] = (
            "The baker unlatches the shutters and pushes them open."
        )
        latch_proposal = {
            "base_document_hash": document_fingerprint(latch_document),
            "scope": {"type": "project"}, "summary": "Sound the latch",
            "operations": [{
                "op": "update_shot", "shot_id": "shot-1", "fields": {
                    "sounds": ["As the latches release, two metal clicks ring out."],
                },
            }],
        }
        repaired = _restrict_ungrounded_audio_proposal(
            latch_document, latch_proposal,
            {"messages": [{"role": "user", "content": "The baker opens the shutters."}]},
        )
        self.assertEqual(
            repaired["operations"][0]["fields"]["sounds"],
            ["As the latches release, two metal clicks ring out."],
        )

    def test_pan_around_subject_requires_arc_shot(self):
        document = normalize_document(director_document())
        document["shots"][1]["camera"]["type"] = "Pan Right"
        data = {"messages": [{
            "role": "user", "content": "After the cut, the camera will pan around her."
        }]}
        with self.assertRaisesRegex(ValueError, "use Arc Shot"):
            _validate_requested_camera_mechanics(document, data)

        document["shots"][1]["camera"]["type"] = "Arc Shot"
        _validate_requested_camera_mechanics(document, data)

    def test_explicit_all_reference_coverage_requires_every_shot(self):
        document = normalize_document({
            **director_document(),
            "mode": "ref2va",
            "references": [{
                "id": "reference-1", "kind": "image", "path": "style.png",
                "label": "<Picture 1>", "roles": ["style"],
            }],
            "subject_definitions": [{
                "label": "Subject 1", "text": "is the style shown in <Picture 1>."
            }],
            "retention_analysis": [{
                "label": "<Subject 1>", "where": "appears in [Shot 1]",
                "relationship": "fully_preserved", "detail": "The style is retained.",
            }],
        })
        document["shots"][0]["composition"] += " <Subject 1>."
        with self.assertRaisesRegex(ValueError, "missing from"):
            _validate_requested_reference_coverage(document, {
                "messages": [{"role": "user", "content": "Apply all references throughout all shots."}]
            })

        for shot_value in document["shots"]:
            shot_value["composition"] += " <Subject 1>."
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"}, "summary": "Apply throughout",
            "operations": [{"op": "update_project", "fields": {
                "retention_analysis": [{
                    "label": "<Subject 1>", "where": "appears in [Shot 1]",
                    "relationship": "fully_preserved", "detail": "The style is retained.",
                }],
            }}],
        }
        repaired = _enforce_requested_reference_coverage(document, proposal, {
            "messages": [{"role": "user", "content": "Apply all references throughout all shots."}]
        })
        self.assertEqual(
            repaired["operations"][0]["fields"]["retention_analysis"][0]["where"],
            "appears in [Shot 1], [Shot 2]",
        )

    def test_reference_prompt_length_allows_concise_full_reference_output(self):
        document = normalize_document({
            **director_document(),
            "mode": "ref2va",
            "references": [{
                "id": "reference-1", "kind": "image", "path": "subject.png",
                "roles": ["subject"],
            }],
            "task_types": ["reference generation"],
            "subject_definitions": [{
                "label": "Subject 1", "text": "is the person shown in <Picture 1>."
            }],
            "summary": "The target video uses <Subject 1> in [Shot 1] and [Shot 2].",
            "retention_analysis": [{
                "label": "<Subject 1>", "where": "appears in [Shot 1] and [Shot 2]",
                "relationship": "fully_preserved", "detail": "The identity is retained.",
            }],
        })
        for shot_value in document["shots"]:
            shot_value["subjects"] = "<Subject 1> appears in the shot."
        _validate_reference_prompt_length(document, {
            "messages": [{"role": "user", "content": "Create the full prompt."}]
        })

    def test_retention_location_strips_model_supplied_outer_parentheses(self):
        document = director_document()
        document["retention_analysis"] = [{
            "label": "<Subject 1>", "where": "((appears in Shot 1 and Shot 2))",
            "relationship": "fully_preserved", "detail": "Retained.",
        }]
        _canonicalize_retention_shot_mentions(document)
        self.assertEqual(
            document["retention_analysis"][0]["where"],
            "appears in [Shot 1] and [Shot 2]",
        )

    def test_dialogue_delivery_does_not_duplicate_compiler_speech_verbs(self):
        document = director_document()
        document["shots"][0]["steps"][1]["delivery"] = "says in an off-screen voiceover"
        document["shots"][0]["steps"][1]["voiceover"] = True
        normalized = normalize_document(document)
        self.assertEqual(normalized["shots"][0]["steps"][1]["delivery"], "")

    def test_requested_cross_cut_dialogue_requires_structured_flag(self):
        document = normalize_document(director_document())
        request = {"messages": [{
            "role": "user", "content": "Her spoken line continues across the cut."
        }]}
        with self.assertRaisesRegex(ValueError, "crosses_cut true"):
            _validate_requested_dialogue_mechanics(document, request)
        document["shots"][0]["steps"][1]["crosses_cut"] = True
        _validate_requested_dialogue_mechanics(document, request)

    def test_requested_dialogue_literal_rejects_markup_or_rewording(self):
        document = normalize_document(director_document())
        request = {"messages": [{
            "role": "user", "content": 'She says exactly "Do not rewrite this."'
        }]}
        _validate_requested_exact_literals(document, request)
        document["shots"][0]["steps"][1]["text"] = "<scenetrans>Do not rewrite this.<scenetrans>"
        with self.assertRaisesRegex(ValueError, "missing exact text"):
            _validate_requested_exact_literals(document, request)

    def test_requested_dialogue_literal_repairs_punctuation_only_drift(self):
        proposal = {
            "scope": {"type": "project"}, "summary": "Keep the line",
            "operations": [{
                "op": "update_shot", "shot_id": "shot-1", "fields": {"steps": [{
                    "type": "dialogue", "speaker": "The woman", "speaker_id": "S1",
                    "language": "English", "text": "Come here, dear.",
                }]},
            }],
        }
        repaired = _canonicalize_requested_dialogue_literals(proposal, {
            "messages": [{"role": "user", "content": 'The woman says "Come here dear".'}],
        })
        self.assertEqual(
            repaired["operations"][0]["fields"]["steps"][0]["text"],
            "Come here dear",
        )
        rewritten = copy.deepcopy(proposal)
        rewritten["operations"][0]["fields"]["steps"][0]["text"] = "Please come here."
        unchanged = _canonicalize_requested_dialogue_literals(rewritten, {
            "messages": [{"role": "user", "content": 'The woman says "Come here dear".'}],
        })
        self.assertEqual(
            unchanged["operations"][0]["fields"]["steps"][0]["text"],
            "Please come here.",
        )

    def test_requested_visible_text_literal_is_exact(self):
        document = normalize_document(director_document())
        document["shots"][0]["visible_text"] = ["EXIT 7"]
        request = {"messages": [{
            "role": "user", "content": 'The neon sign visibly reads "EXIT 7".'
        }]}
        _validate_requested_exact_literals(document, request)
        document["shots"][0]["visible_text"] = ["Exit 7."]
        with self.assertRaisesRegex(ValueError, "missing exact visible text"):
            _validate_requested_exact_literals(document, request)

    def test_requested_visible_text_repairs_punctuation_only_drift(self):
        proposal = {
            "scope": {"type": "project"}, "summary": "Keep the sign",
            "operations": [{
                "op": "update_shot", "shot_id": "shot-1",
                "fields": {"visible_text": ["EXIT 7."]},
            }],
        }
        repaired = _canonicalize_requested_dialogue_literals(proposal, {
            "messages": [{
                "role": "user", "content": 'The neon sign visibly reads "EXIT 7".'
            }],
        })
        self.assertEqual(
            repaired["operations"][0]["fields"]["visible_text"],
            ["EXIT 7"],
        )

    def test_complete_project_rejects_schema_placeholders(self):
        document = normalize_document(director_document())
        document["shots"][0]["composition"] = "composition"
        with self.assertRaisesRegex(ValueError, "placeholder production fields"):
            _validate_complete_project_placeholders(document, {
                "messages": [{"role": "user", "content": "Create the complete production."}]
            })

        document = normalize_document(director_document())
        document["shots"][0]["composition"] = "medium-wide composition"
        document["shots"][0]["steps"][0]["text"] = "action"
        with self.assertRaisesRegex(ValueError, "composition|action step"):
            _validate_complete_project_placeholders(document, {
                "messages": [{"role": "user", "content": "Completely rewrite the production."}]
            })
        retry = _proposal_retry_messages(
            [{"role": "user", "content": "Completely rewrite the production."}],
            "project",
            proposal_error=(
                "The complete project still contains placeholder production fields: "
                "shot-1 composition, shot-1 action step 1"
            ),
        )
        self.assertIn("not merely 'medium-wide composition'", retry[-1]["content"])
        self.assertIn("not 'action'", retry[-1]["content"])
        retry_with_draft = _proposal_retry_messages(
            [{"role": "user", "content": "Completely rewrite the production."}],
            "project",
            proposal_error="placeholder production fields",
            draft_proposal={"scope": {"type": "project"}, "operations": []},
        )
        self.assertIn("Invalid structured draft to repair", retry_with_draft[-2]["content"])
        self.assertIn('"operations":[]', retry_with_draft[-2]["content"])
        sound_retry = _proposal_retry_messages(
            [{"role": "user", "content": "Add synchronized sounds."}],
            "project",
            proposal_error="The complete project proposal omitted requested synchronized sounds for: shot-1",
        )
        self.assertIn("shutters scrape", sound_retry[-1]["content"])
        self.assertIn("keep the same source noun", sound_retry[-1]["content"])

    def test_placeholder_camera_target_uses_grounded_subject_tokens(self):
        document = normalize_document(director_document())
        document["shots"][0]["camera"]["target"] = "the primary subject"
        proposal = {
            "scope": {"type": "project"},
            "summary": "Build the shot.",
            "operations": [{
                "op": "update_shot",
                "shot_id": "shot-1",
                "fields": {
                    "subjects": "<Subject 1> waits while <Subject 2> enters.",
                    "camera": {
                        "type": "Static Shot", "amplitude": "default",
                        "speed": "default", "target": "the primary subject",
                    },
                },
            }],
        }
        repaired = _canonicalize_placeholder_camera_targets(document, proposal)
        self.assertEqual(
            repaired["operations"][0]["fields"]["camera"]["target"],
            "<Subject 1> and <Subject 2>",
        )
        proposal["operations"][0]["fields"].pop("camera")
        repaired = _canonicalize_placeholder_camera_targets(document, proposal)
        self.assertEqual(
            repaired["operations"][0]["fields"]["camera"]["target"],
            "<Subject 1> and <Subject 2>",
        )

    def test_reference_collections_accept_json_encoded_mappings(self):
        self.assertEqual(
            _subject_definitions('{"Subject 1":"is the pose in <Picture 1>."}'),
            [{"label": "Subject 1", "text": "is the pose in <Picture 1>."}],
        )
        self.assertEqual(
            _retention_analysis(
                '{"Subject 1":{"where":"[Shot 1]","relationship":"attribute_transfer",'
                '"detail":"The pose is transferred."}}'
            ),
            [{
                "label": "<Subject 1>", "where": "[Shot 1]",
                "relationship": "attribute_transfer", "detail": "The pose is transferred.",
            }],
        )

    def test_reference_summary_drops_model_supplied_task_prefix(self):
        document = director_document()
        document["task_types"] = ["video editing", "audio reference"]
        document["summary"] = (
            "[video editing + audio reference] The target video is an edited version of <Video 1>."
        )
        _canonicalize_reference_definition_grammar(document)
        self.assertEqual(
            document["summary"], "The target video is an edited version of <Video 1>."
        )

    def test_document_fingerprint_ignores_cached_vision_grounding(self):
        document = normalize_document({
            **director_document(),
            "references": [{
                "id": "reference-1",
                "kind": "image",
                "path": "woman.png",
                "roles": ["subject"],
            }],
        })
        fingerprint = document_fingerprint(document)
        document["references"][0]["observed_visual_facts"] = "One woman is seated near a window."
        document["references"][0]["subject_candidates"] = [{
            "name": "young woman",
            "location": "",
        }]
        self.assertEqual(document_fingerprint(document), fingerprint)

    def test_vision_grounding_contract_is_reference_role_focused(self):
        self.assertIn("Follow assigned_usage strictly", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("For scene usage", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("omitting a visible person's identity and wardrobe", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("For style usage", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("For action usage", VISION_GROUNDING_SYSTEM_MESSAGE)

    def test_vision_grounding_contract_separates_pixels_from_requested_action(self):
        self.assertIn("report only concrete facts visible in their pixels", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("do not add the user's requested action", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("Return JSON only", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("grounded_attributes", VISION_GROUNDING_SYSTEM_MESSAGE)

    def test_vision_grounding_parser_preserves_attachment_order(self):
        attachments = [
            {"id": "a", "name": "First.webp", "usage": "subject"},
            {"id": "b", "name": "Second.png", "usage": "scene"},
        ]
        parsed = _parse_vision_grounding(
            '{"images":[{"index":2,"observations":"Blue tiled room.","subjects":[]},'
            '{"index":1,"observations":"Platinum-blonde hair and pink clothing.",'
            '"subjects":[{"name":"young woman"}]}]}',
            attachments,
        )
        self.assertEqual([item["attachment_id"] for item in parsed], ["a", "b"])
        self.assertIn("Platinum-blonde", parsed[0]["observations"])
        self.assertIn("Blue tiled room", parsed[1]["observations"])
        self.assertEqual(parsed[0]["subject_candidates"][0]["name"], "young woman")

    def test_non_identity_grounding_roles_discard_depicted_subject_candidates(self):
        parsed = _parse_vision_grounding(
            '{"images":[{"index":1,"observations":"A red chair behind a seated woman.",'
            '"subjects":[{"name":"young woman"},{"name":"red chair"}]}]}',
            [{"id": "scene", "name": "Room", "usage": "scene"}],
        )
        self.assertEqual(parsed[0]["subject_candidates"], [])

    def test_vision_grounding_requires_locations_for_same_type_subjects(self):
        attachments = [{"id": "a", "name": "People.png", "usage": "subject"}]
        with self.assertRaisesRegex(ValueError, "distinct locations"):
            _parse_vision_grounding(
                '{"images":[{"index":1,"observations":"Two people.","subjects":['
                '{"name":"person","visual_selectors":["person in black"]},'
                '{"name":"person","visual_selectors":["person in white"]}]}]}',
                attachments,
            )

    def test_scene_grounding_filters_person_identity_and_wardrobe(self):
        parsed = _parse_vision_grounding(
            '{"images":[{"index":1,"observations":"A brunette woman in a white shirt sits in a burgundy chair. '
            'The deep red velvet chair has rolled arms and dark wooden legs. The background is a pale grey wall."}]}',
            [{"id": "scene", "name": "Chair", "usage": "scene"}],
        )

        observations = parsed[0]["observations"]
        self.assertNotIn("brunette woman", observations)
        self.assertNotIn("white shirt", observations)
        self.assertIn("deep red velvet chair", observations)
        self.assertIn("pale grey wall", observations)

    def test_visual_trait_placeholder_retry_demands_source_observations(self):
        retry = _proposal_retry_messages(
            [{"role": "user", "content": "Use her as the identity reference."}],
            "shot",
            proposal_error=(
                "REF2VA reference semantics are incomplete: definition 1 contains unresolved "
                "visual-trait placeholder language"
            ),
        )
        correction = retry[-1]["content"]
        self.assertIn("subject_candidates", correction)
        self.assertIn("minimal subject-to-source binding", correction)
        self.assertIn("Do not copy image observations", correction)

    def test_invalid_retention_relationship_retry_lists_exact_allowed_values(self):
        retry = _proposal_retry_messages(
            [{"role": "user", "content": "Apply the reference to the full video."}],
            "project",
            proposal_error="retention_analysis item 1 has an invalid relationship",
        )
        correction = retry[-1]["content"]
        self.assertIn(
            "Visual: fully_preserved, partially_preserved, attribute_transfer, or weak_reference",
            correction,
        )
        self.assertIn(
            "Audio: fully_copy, partially_copy, reference, or weak_reference",
            correction,
        )

    def test_mixed_steps_retry_demands_one_chronological_representation(self):
        retry = _proposal_retry_messages(
            [{"role": "user", "content": "She speaks, then another woman enters."}],
            "shot",
            proposal_error="steps cannot be combined with action or dialogue in one shot update",
        )
        correction = retry[-1]["content"]
        self.assertIn("return steps only", correction)
        self.assertIn("omit the action and dialogue fields", correction)

    def test_ordered_mixed_request_demands_steps_even_after_unrelated_failure(self):
        retry = _proposal_retry_messages(
            [{"role": "user", "content": 'She says "Come here", then another woman enters.'}],
            "project",
            proposal_error="First-frame lock forbids Director changes to project style",
        )
        self.assertIn("return steps only", retry[-1]["content"])

    def test_incomplete_shot_retry_names_the_canonical_action_step(self):
        retry = _proposal_retry_messages(
            [{"role": "user", "content": "Create a complete three-shot video."}],
            "project",
            proposal_error="The complete project proposal left required shot fields empty: shot-3 (action)",
        )
        correction = retry[-1]["content"]
        self.assertIn("non-empty steps array", correction)
        self.assertIn("Do not use the legacy action field", correction)

    def test_first_frame_restriction_keeps_sequence_and_sound(self):
        document = self.i2va_document()
        proposal = {
            "operations": [
                {"op": "update_project", "fields": {
                    "style": "Invented watercolor style",
                    "overall_soundscape": "Soft room tone.",
                }},
                {"op": "update_shot", "shot_id": "shot-1", "fields": {
                    "composition": "Invented composition",
                    "subjects": "Invented subject description",
                    "environment": "Invented room",
                    "lighting": "Invented lighting",
                    "steps": [{"type": "action", "text": "She raises one hand."}],
                    "sounds": ["Fabric rustles."],
                }},
            ],
        }
        restricted = _restrict_first_frame_proposal(document, proposal)
        self.assertEqual(
            set(restricted["operations"][0]["fields"]),
            {"overall_soundscape"},
        )
        shot_fields = restricted["operations"][1]["fields"]
        self.assertEqual(set(shot_fields), {"steps", "sounds"})

    def test_provider_context_exposes_only_canonical_sequence(self):
        document = normalize_document(director_document())
        _document, context = compact_project_context({
            "scope": "project",
            "document": document,
        })
        shot = context["shots"][0]
        self.assertIn("steps", shot)
        self.assertNotIn("action", shot)
        self.assertNotIn("dialogue", shot)
        self.assertEqual(shot["token"], "[Shot 1]")

    def test_director_canonicalizes_retention_relationship_formatting(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Use the identity reference","operations":[{"op":"update_project","fields":{'
            '"task_types":["reference generation"],'
            '"subject_definitions":[{"label":"Subject 1","text":"is the woman in <Picture 1>."}],'
            '"summary":"The target video follows <Subject 1>.",'
            '"retention_analysis":[{"label":"<Subject 1>","where":"appears in [Shot 1]",'
            '"relationship":"Fully Preserved","detail":"Her appearance is retained."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "project")
        relationship = parsed["proposal"]["operations"][0]["fields"]["retention_analysis"][0]["relationship"]
        self.assertEqual(relationship, "fully_preserved")

    def test_non_reference_proposal_accepts_echoed_empty_reference_arrays(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Rewrite I2VA shot","operations":['
            '{"op":"update_project","fields":{"task_types":[],"subject_definitions":[],"retention_analysis":[]} },'
            '{"op":"update_shot","shot_id":"shot-1","fields":{"steps":['
            '{"type":"action","text":"She opens a red umbrella."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        preview = preview_changeset(document, parsed["proposal"])

        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(preview["document"]["task_types"], [])
        self.assertEqual(action_text(preview["document"]["shots"][0]), "She opens a red umbrella.")

    def test_t2va_proposal_discards_invented_reference_semantics(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage the baker","operations":['
            '{"op":"update_project","fields":{"task_types":["reference generation"],'
            '"subject_definitions":[{"label":"Subject 1","text":"is the baker."}],'
            '"retention_analysis":[{"label":"<Subject 1>","where":"appears in [Shot 1]",'
            '"relationship":"fully_preserved","detail":"The baker remains consistent."}]}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"<Subject 1> opens the shutters."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        preview = preview_changeset(document, parsed["proposal"])

        self.assertEqual(preview["document"]["subject_definitions"], [])
        self.assertNotIn("<Subject 1>", preview["compiled_prompt"])
        self.assertIn("The subject opens the shutters", preview["compiled_prompt"])

    def test_director_can_replace_ordered_steps_and_visible_text(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose the exact shot sequence","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"visible_text":["LAST TRAIN"],"steps":['
            '{"type":"action","text":"She raises the letter."},'
            '{"type":"dialogue","speaker":"The woman","speaker_id":"S1","language":"English",'
            '"performance":"singing","text":"Wait for me!"},'
            '{"type":"action","text":"The carriage lights fade."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        preview = preview_changeset(document, parsed["proposal"])
        shot = preview["document"]["shots"][0]
        self.assertEqual([step["type"] for step in shot["steps"]], ["action", "dialogue", "action"])
        self.assertEqual(shot["visible_text"], ["LAST TRAIN"])
        self.assertIn("(S1) sings: <d>[English] Wait for me!</d>", preview["compiled_prompt"])

    def test_director_rejects_legacy_sequence_mirrors(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage dialogue before an entrance","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{'
            '"action":"<Subject 2> enters from frame right and smiles.",'
            '"dialogue":[{"speaker":"<Subject 1>","speaker_id":"S1","language":"English",'
            '"text":"Come here dear"}],'
            '"steps":[{"type":"dialogue","speaker":"<Subject 1>","speaker_id":"S1",'
            '"language":"English","text":"Come here dear"},'
            '{"type":"action","text":"<Subject 2> enters from frame right and smiles."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "shot")

        self.assertIsNone(parsed["proposal"])
        self.assertIn("unsupported shot field 'action'", parsed["proposal_error"])

    def test_director_canonicalizes_common_step_text_aliases(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage dialogue before an entrance","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"dialogue","speaker":"<Subject 1>","speaker_id":"S1",'
            '"language":"English","text":"","line":"Come here dear"},'
            '{"type":"action","text":"","description":"<Subject 2> enters from frame right and smiles."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "shot")

        self.assertFalse(parsed["proposal_error"])
        steps = parsed["proposal"]["operations"][0]["fields"]["steps"]
        self.assertEqual([step["text"] for step in steps], [
            "Come here dear",
            "<Subject 2> enters from frame right and smiles.",
        ])

    def test_director_canonicalizes_subject_tokens_to_minimax_speaker_ids(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage the exchange","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"dialogue","speaker":"<Subject 1>","speaker_id":"<Subject 1>",'
            '"language":"English","text":"Hello there"},'
            '{"type":"dialogue","speaker":"<Subject 2>","speaker_id":"Subject 2",'
            '"language":"English","text":"General Kenobi"}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "shot")

        self.assertFalse(parsed["proposal_error"])
        steps = parsed["proposal"]["operations"][0]["fields"]["steps"]
        self.assertEqual([step["speaker_id"] for step in steps], ["S1", "S2"])

    def test_director_infers_speaker_id_from_subject_speaker_name(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add a reply","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"dialogue","speaker":"<Subject 2>","speaker_id":"character two",'
            '"language":"English","text":"General Kenobi"}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "shot")

        self.assertFalse(parsed["proposal_error"])
        event = parsed["proposal"]["operations"][0]["fields"]["steps"][0]
        self.assertEqual(event["speaker_id"], "S2")

    def test_i2va_director_binds_generic_onscreen_speaker_to_sole_keyframe_subject(self):
        document = self.i2va_document()
        document["shots"][0]["steps"] = [
            step for step in document["shots"][0]["steps"] if step["type"] == "action"
        ]
        document["references"][0]["subject_candidates"] = [{
            "name": "young woman", "location": "center",
            "visual_selectors": ["woman in white shirt"],
        }]
        response = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add synchronized dialogue","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"dialogue","speaker":"The speaker","speaker_id":"S1",'
            '"language":"English","text":"I can do this","voiceover":false,"offscreen":false},'
            '{"type":"action","text":"She raises both hands while speaking."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        with patch("video.director.generate_chat", return_value=response):
            result = director_chat({
                "scope": "project",
                "document": document,
                "require_proposal": True,
                "messages": [{
                    "role": "user",
                    "content": 'She says "I can do this" while raising both hands.',
                }],
            })

        self.assertEqual(result["status"], "ready", result)
        preview = preview_changeset(document, result["proposal"])
        dialogue = dialogue_steps(preview["document"]["shots"][0])[0]
        self.assertEqual(dialogue["speaker"], "The young woman shown in <Picture 1>")
        self.assertIn(
            "The young woman shown in <Picture 1> (S1) says",
            preview["compiled_prompt"],
        )

    def test_director_rejects_divergent_legacy_field_beside_steps(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Conflicting representations","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"action":"She exits.",'
            '"steps":[{"type":"action","text":"She enters."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", document_fingerprint(document), "shot")

        self.assertIsNone(parsed["proposal"])
        self.assertIn("unsupported shot field 'action'", parsed["proposal_error"])

    def test_parser_repairs_a_missing_operation_closing_brace(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Repair structure","operations":['
            '{"op":"update_project","fields":{"style":"Cinematic"},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She turns."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")

        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(len(parsed["proposal"]["operations"]), 2)

    def test_reference_placeholders_are_enriched_from_visible_observations(self):
        proposal = {
            "operations": [{
                "op": "update_project",
                "fields": {"subject_definitions": [{
                    "label": "Subject 1",
                    "text": "is the woman in <Picture 1>, with the observed identity traits.",
                }]},
            }],
        }
        enriched = _enrich_reference_definition_placeholders(
            proposal,
            "Her visible traits include long blonde hair, a white strapless dress, and a pearl necklace.",
        )
        text = enriched["operations"][0]["fields"]["subject_definitions"][0]["text"]
        self.assertIn("long blonde hair", text)
        self.assertIn("pearl necklace", text)

    def test_reference_only_requests_cannot_replace_existing_actions(self):
        original = normalize_document(director_document())
        changed = copy.deepcopy(original)
        changed["shots"][0]["steps"] = [{"type": "action", "text": "She poses without moving."}]
        request = {
            "attachments": [{"path": "woman.png", "usage": "subject"}],
            "messages": [{"role": "user", "content": "Make this the identity reference."}],
        }
        with self.assertRaisesRegex(ValueError, "preserve shot-1 action steps verbatim"):
            _validate_reference_only_preservation(original, changed, request)
        changed["shots"][0]["steps"] = [{
            "type": "action",
            "text": action_text(original["shots"][0]) + " <Subject 1> keeps the same identity.",
        }]
        _validate_reference_only_preservation(original, changed, request)

    def test_complete_reference_rewrite_is_not_misclassified_by_keep_wording(self):
        request = {
            "attachments": [{"path": "woman.png", "usage": "subject"}],
            "messages": [{
                "role": "user",
                "content": (
                    "Completely rewrite the full production into exactly three resulting shots. "
                    "Keep every reference assignment distinct across all shots."
                ),
            }],
        }

        self.assertFalse(_preserve_reference_only_request(request))

    def test_reference_story_request_is_not_misclassified_as_reference_assignment(self):
        request = {
            "attachments": [{"path": "woman.png", "usage": "subject"}],
            "messages": [{
                "role": "user",
                "content": "Create a single continuous shot of the girl from the reference picture dancing joyfully in the rain.",
            }],
        }

        self.assertFalse(_preserve_reference_only_request(request))

    def test_action_reference_assignment_preserves_canonical_steps(self):
        document = normalize_document(director_document())
        request = {
            "attachments": [{"path": "action.png", "usage": "action"}],
            "messages": [{"role": "user", "content": "Use this as the action reference."}],
        }
        proposal = {
            "operations": [{
                "op": "update_shot",
                "shot_id": "shot-1",
                "fields": {"steps": [{"type": "action", "text": "<Subject 1> turns."}]},
            }],
        }

        self.assertTrue(_preserve_reference_only_request(request))
        restricted = _restrict_reference_only_proposal(document, proposal, request)
        self.assertEqual(
            restricted["operations"][0]["fields"],
            proposal["operations"][0]["fields"],
        )

    def test_non_identity_role_definitions_are_rebuilt_from_their_own_images(self):
        document = normalize_document({
            **director_document(),
            "mode": "ref2va",
            "references": [
                {
                    "id": "reference-1", "kind": "image", "path": "action.png",
                    "roles": ["action"],
                },
                {
                    "id": "reference-2", "kind": "image", "path": "pose.png",
                    "roles": ["pose"],
                },
            ],
        })
        proposal = {
            "operations": [{
                "op": "update_project",
                "fields": {
                    "subject_definitions": [
                        {"label": "Subject 1", "text": "is the standing pose in <Picture 2>."},
                        {"label": "Subject 2", "text": "is the seated action in <Picture 1>."},
                    ],
                    "summary": "Uses <Subject 1> and <Subject 2>.",
                    "retention_analysis": [],
                },
            }],
        }
        completed = _complete_grounded_reference_semantics(document, proposal, {
            "messages": [{"role": "user", "content": "Use both references."}],
            "attachments": [
                {
                    "id": "attachment-1", "path": "action.png", "usage": "action",
                    "reference_id": "reference-1",
                },
                {
                    "id": "attachment-2", "path": "pose.png", "usage": "pose",
                    "reference_id": "reference-2",
                },
            ],
            "_vision_observations": [
                {
                    "attachment_id": "attachment-1", "usage": "action",
                    "observations": "A person lowers into a chair and crosses both legs.",
                    "subject_candidates": [],
                },
                {
                    "attachment_id": "attachment-2", "usage": "pose",
                    "observations": "A person stands upright with arms at both sides.",
                    "subject_candidates": [],
                },
            ],
        })
        definitions = {
            item["label"]: item["text"]
            for item in completed["operations"][0]["fields"]["subject_definitions"]
        }
        self.assertIn("<Picture 1>", definitions["Subject 1"])
        self.assertIn("lowers into a chair", definitions["Subject 1"])
        self.assertNotIn("<Picture 2>", definitions["Subject 1"])
        self.assertIn("<Picture 2>", definitions["Subject 2"])
        self.assertIn("stands upright", definitions["Subject 2"])

    def test_subject_only_reference_rejects_picture_prose_and_invented_appearance(self):
        document = normalize_document({
            **director_document(),
            "mode": "ref2va",
            "references": [{
                "id": "reference-1", "kind": "image", "path": "woman.png",
                "roles": ["subject"],
            }],
        })
        document["shots"][0]["subjects"] = "<Subject 1> stands in the rain."
        _validate_subject_only_reference_prose(document, {
            "messages": [{"role": "user", "content": "Make the girl dance in rain."}]
        })
        document["shots"][0]["subjects"] = "The dark-haired girl from <Picture 1>."
        with self.assertRaisesRegex(ValueError, "subject-only reference"):
            _validate_subject_only_reference_prose(document, {
                "messages": [{"role": "user", "content": "Make the girl dance in rain."}]
            })

    def test_explicit_dialogue_then_action_rejects_leading_action(self):
        document = normalize_document(director_document())
        request = {"messages": [{
            "role": "user",
            "content": 'The girl says "Come here". Then another girl enters.',
        }]}
        with self.assertRaisesRegex(ValueError, "dialogue before"):
            _validate_requested_step_order(document, request)
        document["shots"][0]["steps"] = [
            document["shots"][0]["steps"][1], document["shots"][0]["steps"][0]
        ]
        _validate_requested_step_order(document, request)

    def test_selected_action_edit_with_preservation_note_is_not_reference_only(self):
        request = {
            "attachments": [{"path": "first.png", "usage": "first_frame"}],
            "messages": [{
                "role": "user",
                "content": (
                    "Rewrite only the selected shot's steps, camera, and sounds. The woman turns toward camera. "
                    "Do not change pixel-owned setup fields."
                ),
            }],
        }
        self.assertFalse(_preserve_reference_only_request(request))

    def test_structured_sound_objects_become_natural_sound_sentences(self):
        self.assertEqual(
            _audible_sound({
                "source": "footstep", "timing": "As she moves",
                "description": "A soft footstep clicks.",
            }),
            "As she moves, a soft footstep clicks.",
        )

    def test_reference_definition_with_empty_where_is_grounded_into_single_shot(self):
        value = director_document()
        value["shots"] = [value["shots"][0]]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Create the rain dance",
            "operations": [
                {"op": "update_project", "fields": {
                    "task_types": ["reference generation"],
                    "subject_definitions": [{
                        "label": "Subject 1",
                        "text": "is the blonde woman in <Picture 1>, wearing a white dress.",
                    }],
                    "summary": "The video follows <Subject 1> dancing in the rain.",
                    "retention_analysis": [{
                        "label": "<Subject 1>", "where": "",
                        "relationship": "fully_preserved",
                        "detail": "Her blonde hair and white dress remain consistent.",
                    }],
                }},
                {"op": "update_shot", "shot_id": "shot-1", "fields": {
                    "subjects": "The girl stands in the rain.",
                    "steps": [{"type": "action", "text": "She spins and smiles brightly."}],
                }},
            ],
        }

        result = preview_changeset(document, proposal)

        self.assertIn("<Subject 1>", result["document"]["shots"][0]["subjects"])
        self.assertIn("<Subject 1>", result["compiled_prompt"])

    def test_reference_subject_prose_is_grammatical_and_not_duplicated_in_shot(self):
        value = director_document()
        value["shots"] = [value["shots"][0]]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Create the rain dance",
            "operations": [{"op": "update_project", "fields": {
                "task_types": ["reference generation"],
                "subject_definitions": [{
                    "label": "Subject 1",
                    "text": "A young woman defined in <Picture 1>, wearing casual clothes, with dark hair.",
                }],
                "summary": "The video follows <Subject 1> dancing in the rain.",
                "retention_analysis": [{
                    "label": "<Subject 1>", "where": "appears in [Shot 1]",
                    "relationship": "fully_preserved",
                    "detail": "Her appearance remains consistent.",
                }],
            }}, {"op": "update_shot", "shot_id": "shot-1", "fields": {
                "subjects": "<Subject 1> stands center frame.",
                "steps": [{"type": "action", "text": "The girl from <Subject 1> dances happily in the rain. She spins and smiles brightly."}],
            }}],
        }

        result = preview_changeset(document, proposal)
        shot_value = result["document"]["shots"][0]
        definition = result["document"]["subject_definitions"][0]["text"]

        self.assertEqual(
            action_text(shot_value),
            "<Subject 1> dances happily in the rain. She spins and smiles brightly.",
        )
        self.assertEqual(shot_value["subjects"].count("<Subject 1>"), 1)
        self.assertNotIn("defined in <Picture 1>", shot_value["subjects"])
        self.assertTrue(definition.startswith("is a young woman shown in <Picture 1>"))

    def test_director_synthesizes_grounded_reference_package_when_model_omits_it(self):
        value = director_document()
        value["shots"] = [value["shots"][0]]
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        response = (
            "Create a single continuous rain-dance shot.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Create the rain dance","operations":['
            '{"op":"update_project","fields":{"main_description":"A girl dances joyfully in the rain."}},'
            '{"op":"update_shot","shot_id":"shot-1","fields":{'
            '"composition":"A full-body continuous shot.","subjects":"The girl stands in rainfall.",'
            '"environment":"A rain-soaked open space.","lighting":"Soft overcast daylight.",'
            '"steps":[{"type":"action","text":"She spins and smiles brightly."}],'
            '"sounds":["Rainfall and splashing footsteps."]}}]}'
            f"\n{CHANGESET_END}"
        )
        attachments = [{
            "id": "attachment-1", "path": "woman.png", "name": "Woman",
            "usage": "subject", "reference_id": "reference-1",
            "source_width": 768, "source_height": 1024,
        }]
        observations = [{
            "index": 1,
            "attachment_id": "attachment-1",
            "source_name": "Woman",
            "usage": "subject",
            "observations": "A girl with long blonde hair wears a bright yellow raincoat and black boots.",
            "subject_candidates": [{"name": "young woman", "location": ""}],
        }]
        with patch("video.director.load_vision_images", return_value=(attachments, [{}])), patch(
            "video.director._ground_vision_images", return_value=observations
        ), patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "thinking_mode": "High",
                "messages": [{
                    "role": "user",
                    "content": "Create a single continuous shot of the girl dancing in the rain.",
                }],
                "attachments": [{"path": "woman.png", "usage": "subject"}],
            })

        self.assertIsNotNone(result["proposal"], result["proposal_error"])
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(
            result["proposal"]["base_document_hash"],
            document_fingerprint(document),
        )
        preview = preview_changeset(document, result["proposal"])
        self.assertEqual(preview["document"]["subject_definitions"][0]["label"], "Subject 1")
        definition = preview["document"]["subject_definitions"][0]["text"]
        self.assertEqual(definition, "is only the young woman in <Picture 1>.")
        self.assertNotIn("blonde", definition)
        self.assertNotIn("raincoat", definition)
        self.assertIn("<Subject 1>", preview["document"]["shots"][0]["subjects"])

    def test_context_keeps_selected_neighbors_and_bounds_history(self):
        messages = []
        for index in range(20):
            messages.append({"role": "user", "content": f"Question {index} " + "x" * 300})
            messages.append({"role": "assistant", "content": f"Answer {index} " + "y" * 300})
        messages.append({"role": "user", "content": "Improve the camera move."})
        provider_messages, usage = build_provider_messages({
            "document": director_document(),
            "selected_shot_id": "shot-2",
            "project_name": "Letter",
            "brief": "A farewell on a train.",
            "messages": messages,
            "context_budget_chars": 8000,
        })
        context = provider_messages[0]["content"]
        self.assertIn('"role":"previous"', context)
        self.assertIn('"role":"selected"', context)
        self.assertNotIn('"role":"next"', context)
        self.assertEqual([message["role"] for message in provider_messages].count("system"), 1)
        self.assertGreater(usage["omitted_messages"], 0)
        self.assertLessEqual(usage["context_chars"] + usage["history_chars"], 8000)
        self.assertEqual(provider_messages[-1]["role"], "user")

    def test_prompt_generation_optimization_controls_full_guide_context(self):
        request = {
            "document": director_document(),
            "selected_shot_id": "shot-2",
            "messages": [{"role": "user", "content": "Improve the camera move."}],
        }

        optimized_messages, optimized_usage = build_provider_messages(request)
        guide = _base_prompt_writing_guide_for_mode("t2va")
        self.assertNotIn(guide, optimized_messages[0]["content"])
        self.assertEqual(optimized_usage["prompt_guide_chars"], 0)

        full_messages, full_usage = build_provider_messages({
            **request,
            "optimize_prompt_generation": False,
        })
        self.assertIn(guide, full_messages[0]["content"])
        self.assertIn(
            "BEGIN AUTHORITATIVE MINIMAX H3 BASE-T2VA VIDEO PROMPT WRITING GUIDE",
            full_messages[0]["content"],
        )
        self.assertEqual(full_usage["prompt_guide_chars"], len(guide))
        self.assertEqual(full_usage["prompt_guides"], ["base-t2va"])
        self.assertEqual(provider_context(full_messages)["scope"], "shot")

    def test_base_guide_context_is_task_specific(self):
        expected_sections = {
            "t2va": "### Case 1: T2VA",
            "i2va": "### Case 2: I2VA",
            "fl2va": "### Case 3: FL2VA",
            "l2va": "### Case 4: L2VA",
        }
        for mode, expected_case in expected_sections.items():
            guide = _base_prompt_writing_guide_for_mode(mode)
            self.assertIn(expected_case, guide)
            for other_case in set(expected_sections.values()) - {expected_case}:
                self.assertNotIn(other_case, guide)
            self.assertNotIn("Full-Reference Mode Rewrite Output Format Guide", guide)

    def test_reference_mode_receives_base_and_reference_guides(self):
        document = director_document()
        document["references"] = [{
            "id": "reference-1",
            "kind": "image",
            "path": "subject.png",
            "roles": ["subject"],
        }]
        messages, usage = build_provider_messages({
            "document": document,
            "scope": "project",
            "messages": [{"role": "user", "content": "Use the referenced subject."}],
            "optimize_prompt_generation": False,
        })
        base_guide = _shared_base_prompt_writing_guide()
        reference_guide = _reference_prompt_writing_guide()
        self.assertIn(base_guide, messages[0]["content"])
        self.assertIn(reference_guide, messages[0]["content"])
        self.assertEqual(usage["prompt_guides"], ["reference", "base-shared"])
        self.assertEqual(usage["prompt_guide_chars"], len(base_guide) + len(reference_guide))
        self.assertNotIn("### Case 2: I2VA", messages[0]["content"])
        self.assertNotIn("### Case 3: FL2VA", messages[0]["content"])
        self.assertNotIn("### Case 4: L2VA", messages[0]["content"])

    def test_valid_proposal_updates_only_allowed_fields(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            "A slow push-in will make the reaction clearer.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Clarify the reaction","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She watches the lights pass."}],'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow",'
            '"target":"her reflection"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        result = preview_changeset(document, parsed["proposal"])
        updated = result["document"]
        self.assertEqual(action_text(updated["shots"][1]), "She watches the lights pass.")
        self.assertEqual(updated["shots"][1]["camera"]["type"], "Push In")
        self.assertEqual(dialogue_steps(updated["shots"][0])[0]["text"], "Do not rewrite this.")
        self.assertEqual(updated["shots"][0]["visible_text"], ["Central Station"])

    def test_selected_shot_accepts_minimax_display_label(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Use the subject viewpoint","operations":[{"op":"update_shot",'
            '"shot_id":"[Shot 2]","fields":{"composition":"First-person point of view.",'
            '"camera":{"type":"POV","amplitude":"small","speed":"default"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-2", document_fingerprint(document))
        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(parsed["proposal"]["operations"][0]["shot_id"], "shot-2")
        preview = preview_changeset(document, parsed["proposal"])
        self.assertEqual(preview["document"]["shots"][1]["camera"]["type"], "POV")

    def test_switch_and_convert_wording_requests_a_proposal(self):
        for instruction in ("Switch this shot to POV.", "Convert this shot to first person."):
            with self.subTest(instruction=instruction):
                self.assertTrue(_proposal_requested({
                    "messages": [{"role": "user", "content": instruction}],
                }))

    def test_dialogue_line_requests_require_an_applicable_proposal(self):
        for instruction in (
            "Create a line of dialogue for her.",
            "Give the woman a good line here.",
            "What should she say in this moment?",
        ):
            with self.subTest(instruction=instruction):
                self.assertTrue(_proposal_requested({
                    "messages": [{"role": "user", "content": instruction}],
                }))
        self.assertTrue(_proposal_requested({
            "messages": [{"role": "user", "content": "Suggest a line and change the camera to a push-in."}],
        }))
        self.assertTrue(_proposal_requested({
            "messages": [{
                "role": "user",
                "content": 'The girl from Picture 1 says "Come here dear". Then another girl enters.',
            }],
        }))

    def test_dialogue_changeset_preserves_existing_sequence(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            "This line fits the farewell.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Suggest a line","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"action","text":"The woman unfolds a letter."},'
            '{"type":"dialogue","speaker":"The woman","speaker_id":"S1","language":"English",'
            '"text":"Do not rewrite this."},'
            '{"type":"dialogue","speaker":"The woman","speaker_id":"S1","language":"English",'
            '"text":"Maybe goodbye is just another window."}]}}]}\n'
            f"{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "shot-1", fingerprint)
        preview = preview_changeset(document, parsed["proposal"])

        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(parsed["message"], "This line fits the farewell.")
        dialogue = dialogue_steps(preview["document"]["shots"][0])
        self.assertEqual([item["text"] for item in dialogue], [
            "Do not rewrite this.",
            "Maybe goodbye is just another window.",
        ])
        self.assertEqual(
            [step["type"] for step in preview["document"]["shots"][0]["steps"]],
            ["action", "dialogue", "dialogue"],
        )

    def test_steps_cannot_silently_drop_existing_dialogue(self):
        original = normalize_document(director_document())
        result = copy.deepcopy(original)
        result["shots"][0]["steps"] = [
            step for step in result["shots"][0]["steps"]
            if step["type"] != "dialogue"
        ]
        result = normalize_document(result)

        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            _validate_protected_sequence_content(
                original,
                result,
                {"messages": [{"role": "user", "content": "Add another action after she speaks."}]},
            )

    def test_explicit_dialogue_rewrite_can_replace_existing_step_text(self):
        original = normalize_document(director_document())
        result = copy.deepcopy(original)
        dialogue = next(step for step in result["shots"][0]["steps"] if step["type"] == "dialogue")
        dialogue["text"] = "A deliberately revised line."
        result = normalize_document(result)

        _validate_protected_sequence_content(
            original,
            result,
            {"messages": [{"role": "user", "content": "Rewrite the dialogue line in Shot 1."}]},
        )

    def test_steps_cannot_silently_drop_existing_visible_text(self):
        original = normalize_document(director_document())
        result = copy.deepcopy(original)
        result["shots"][0]["visible_text"] = []

        with self.assertRaisesRegex(ValueError, "protected visible text"):
            _validate_protected_sequence_content(
                original,
                result,
                {"messages": [{"role": "user", "content": "Reorder the action steps."}]},
            )

    def test_project_proposal_accepts_new_shot_dialogue_and_empty_protected_echoes(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the invitation","operations":['
            '{"op":"update_shot","shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She powers on the computer."}],"visible_text":[]}},'
            '{"op":"add_shot","shot":{"id":"invitation-shot","start":6.0,'
            '"steps":[{"type":"action","text":"She turns toward the lens."},'
            '{"type":"dialogue","speaker":"","text":"wanna play?"}],"visible_text":[]}}]}\n'
            f"{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        preview = preview_changeset(document, parsed["proposal"])

        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(len(preview["document"]["shots"]), 3)
        dialogue = dialogue_steps(preview["document"]["shots"][2])[0]
        self.assertEqual(dialogue["speaker"], "The speaker")
        self.assertEqual(dialogue["speaker_id"], "S1")
        self.assertEqual(dialogue["language"], "English")
        self.assertEqual(dialogue["text"], "wanna play?")
        self.assertIn("<d>[English] wanna play?</d>", preview["compiled_prompt"])

    def test_add_shot_keeps_canonical_steps_without_legacy_mirrors(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Add the final reaction",
            "operations": [{"op": "add_shot", "shot": {
                "id": "shot-3",
                "start": 6.0,
                "steps": [
                    {"type": "action", "text": "She unfolds the note."},
                    {"type": "dialogue", "speaker": "The baker", "speaker_id": "S1", "text": "Thank you."},
                ],
            }}],
        }

        result = preview_changeset(document, proposal)
        shot = result["document"]["shots"][-1]

        self.assertEqual([step["type"] for step in shot["steps"]], ["action", "dialogue"])
        self.assertEqual(action_text(shot), "She unfolds the note.")
        self.assertEqual(dialogue_steps(shot)[0]["text"], "Thank you.")
        replay = preview_changeset(document, result["proposal"])
        replay_shot = replay["document"]["shots"][-1]
        self.assertEqual([step["type"] for step in replay_shot["steps"]], ["action", "dialogue"])

    def test_camera_phrases_are_normalized_to_document_enums(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine camera","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"camera":{"type":"push-in",'
            '"amplitude":"with small amplitude","speed":"at slow speed"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        camera = preview_changeset(document, parsed["proposal"])["document"]["shots"][1]["camera"]
        self.assertEqual(camera["type"], "Push In")
        self.assertEqual(camera["amplitude"], "small")
        self.assertEqual(camera["speed"], "slow")

        default_raw = raw.replace("with small amplitude", "medium amplitude").replace("at slow speed", "normal speed")
        parsed = parse_director_response(default_raw, "shot-2", fingerprint)
        camera = preview_changeset(document, parsed["proposal"])["document"]["shots"][1]["camera"]
        self.assertEqual(camera["amplitude"], "default")
        self.assertEqual(camera["speed"], "default")

        alias_raw = raw.replace("push-in", "Static Hold")
        parsed = parse_director_response(alias_raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        camera = preview_changeset(document, parsed["proposal"])["document"]["shots"][1]["camera"]
        self.assertEqual(camera["type"], "Static Shot")

        vague_raw = raw.replace("with small amplitude", "gentle emphasis").replace("at slow speed", "smoothly")
        parsed = parse_director_response(vague_raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        camera = preview_changeset(document, parsed["proposal"])["document"]["shots"][1]["camera"]
        self.assertEqual(camera["amplitude"], "default")
        self.assertEqual(camera["speed"], "default")

        qualified_raw = raw.replace("push-in", "Slow Pan Right")
        parsed = parse_director_response(qualified_raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        camera = preview_changeset(document, parsed["proposal"])["document"]["shots"][1]["camera"]
        self.assertEqual(camera["type"], "Pan Right")

    def test_project_proposal_repairs_model_structure_and_display_ids(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine <Shot1>","operations":[{"op":"update_project",'
            '"fields":{"non_diegetic_music":"none","overall_soundscape":"Wheel hum. No non-diegetic music."}},'
            '{"op":"update_shot","shot_id":"<Shot1>",'
            '"fields":{"subjects":"<Subject1> holding <Picture1> (red mug)",'
            '"environment":"<Environment1> kitchen","steps":['
            '{"type":"action","text":"<Picture1> stays upright"}],'
            '"camera":{"type":"Pull Back",'
            '"amplitude":"restrained","speed":"smooth","target":"towards <Object1> (red mug)"}},'
            '"sounds":["<Audio1> (wheel hum)","Paper remains still"]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        self.assertFalse(parsed["proposal_error"])
        result = preview_changeset(document, parsed["proposal"])
        proposal = result["proposal"]
        self.assertEqual(proposal["operations"][1]["shot_id"], "shot-1")
        self.assertEqual(proposal["operations"][1]["fields"]["sounds"], ["wheel hum"])
        camera = result["document"]["shots"][0]["camera"]
        self.assertEqual(camera["type"], "Pull Out")
        self.assertEqual(camera["target"], "red mug")
        self.assertEqual(result["document"]["shots"][0]["subjects"], "the subject holding red mug")
        self.assertEqual(result["document"]["shots"][0]["environment"], "the environment kitchen")
        self.assertEqual(action_text(result["document"]["shots"][0]), "red mug stays upright")
        self.assertEqual(result["document"]["non_diegetic_music"], "N/A")
        self.assertEqual(result["document"]["overall_soundscape"], "Wheel hum.")

    def test_sound_filter_keeps_audible_event_after_visual_stillness(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the knock","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"sounds":["The woman remains still as three knocks strike the door."]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        self.assertEqual(
            parsed["proposal"]["operations"][0]["fields"]["sounds"],
            ["The woman remains still as three knocks strike the door."],
        )

    def test_sound_step_is_lifted_into_synchronized_sounds(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add synchronized sound","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She turns toward the window."},'
            '{"type":"sound","description":"A soft footstep lands on the carriage floor."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")

        self.assertFalse(parsed["proposal_error"], parsed)
        fields = parsed["proposal"]["operations"][0]["fields"]
        self.assertEqual(fields["steps"], [{"type": "action", "text": "She turns toward the window."}])
        self.assertEqual(fields["sounds"], ["A soft footstep lands on the carriage floor."])
        preview = preview_changeset(document, parsed["proposal"])
        self.assertIn("A soft footstep lands on the carriage floor.", preview["compiled_prompt"])

    def test_audible_only_action_step_is_lifted_without_losing_visible_action(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Separate action and sound","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She stops beneath the clock."},'
            '{"type":"action","text":"Footsteps echo and then stop abruptly."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        fields = parsed["proposal"]["operations"][0]["fields"]
        self.assertEqual(fields["steps"], [{"type": "action", "text": "She stops beneath the clock."}])
        self.assertEqual(fields["sounds"], ["Footsteps echo and then stop abruptly."])

    def test_project_proposal_can_update_planning_synopsis_without_compiling_it(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Clarify the general prompt","operations":[{"op":"update_project",'
            '"fields":{"main_description":"A woman reads a farewell letter during a rainy night train journey."}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        self.assertFalse(parsed["proposal_error"])
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual(
            result["document"]["main_description"],
            "A woman reads a farewell letter during a rainy night train journey.",
        )
        self.assertNotIn(result["document"]["main_description"], result["compiled_prompt"])

    def test_structured_camera_motion_is_not_duplicated_in_action(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine reveal","operations":[{"op":"update_shot","shot_id":"shot-2",'
            '"fields":{"steps":[{"type":"action","text":"She opens the letter. The camera slowly pushes in toward the text. The slow push-in emphasizes the writing."}],'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow",'
            '"target":"her movement towards the text"}}}]} '
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual(action_text(result["document"]["shots"][1]), "She opens the letter.")
        self.assertEqual(result["document"]["shots"][1]["camera"]["target"], "the text")
        self.assertEqual(result["compiled_prompt"].count("camera pushes in"), 1)

    def test_camera_only_step_deduplication_keeps_valid_camera_proposal(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Hold the first-frame composition","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":{'
            '"steps":[{"type":"action","text":"The camera holds a static shot."}],'
            '"camera":{"type":"Static Shot","amplitude":"default","speed":"default",'
            '"target":"the woman"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-1", fingerprint)
        result = preview_changeset(document, parsed["proposal"])
        fields = result["proposal"]["operations"][0]["fields"]
        self.assertNotIn("steps", fields)
        self.assertEqual(fields["camera"]["type"], "Static Shot")
        self.assertEqual(
            result["document"]["shots"][0]["steps"], document["shots"][0]["steps"]
        )

    def test_camera_deduplication_preserves_action_in_mixed_sentence(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add a wave","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":[{"type":"action",'
            '"text":"The woman waves while the camera holds a static shot."}],'
            '"camera":{"type":"Static Shot","amplitude":"default","speed":"default",'
            '"target":"the woman"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-1", fingerprint)
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual(action_text(result["document"]["shots"][0]), "The woman waves")

    def test_add_operations_for_display_ids_update_existing_shots(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Populate both shots","operations":['
            '{"op":"add_shot","shot":{"id":"Shot1","start":0,"steps":[{"type":"action","text":"Opening action."}]}},'
            '{"op":"add_shot","shot":{"id":"Shot2","start":4.5,"steps":[{"type":"action","text":"Reveal action."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual([item["op"] for item in result["proposal"]["operations"]], ["update_shot", "update_shot"])
        self.assertEqual([shot["id"] for shot in result["document"]["shots"]], ["shot-1", "shot-2"])
        self.assertEqual(result["document"]["shots"][1]["start"], 4.5)

    def test_selected_shot_proposal_accepts_visible_text_but_rejects_other_shots(self):
        fingerprint = document_fingerprint(director_document())
        protected = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Rewrite visible text","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"visible_text":["Changed"]}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(protected, "shot-2", fingerprint)
        self.assertIsNotNone(parsed["proposal"])
        self.assertEqual(parsed["proposal"]["operations"][0]["fields"]["visible_text"], ["Changed"])

        other = protected.replace('"shot_id":"shot-2"', '"shot_id":"shot-1"').replace(
            '"visible_text":["Changed"]', '"action":"Changed"'
        )
        parsed = parse_director_response(other, "shot-2", fingerprint)
        self.assertIsNone(parsed["proposal"])
        self.assertIn("another shot", parsed["proposal_error"])

    def test_stale_proposal_is_rejected(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "shot", "shot_id": "shot-2"},
            "summary": "Update action",
            "operations": [{"op": "update_shot", "shot_id": "shot-2", "fields": {
                "steps": [{"type": "action", "text": "New action"}],
            }}],
        }
        document["shots"][1]["steps"][0]["text"] = "A manual edit happened."
        with self.assertRaisesRegex(ValueError, "changed after this proposal"):
            preview_changeset(document, proposal)

    def test_chat_returns_validated_proposal_and_context_usage(self):
        response = (
            "This keeps the action feasible.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine action","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"She turns toward the passing lights."}]}}]}\n'
            f"{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "document": director_document(),
                "selected_shot_id": "shot-2",
                "project_name": "Letter",
                "brief": "A farewell on a train.",
                "thinking_mode": "High",
                "messages": [{"role": "user", "content": "Refine the action."}],
            })
        self.assertEqual(result["proposal"]["scope"]["shot_id"], "shot-2")
        self.assertEqual(result["scope"], "shot")
        self.assertGreater(result["context_usage"]["context_chars"], 0)
        provider_messages = generate.call_args.args[1]
        self.assertIn("[Shot 2]", provider_messages[0]["content"])
        self.assertEqual(generate.call_args.args[2], [])
        self.assertEqual(generate.call_args.args[0]["temperature"], 0.2)
        self.assertEqual(generate.call_args.args[0]["thinking_mode"], "High")

    def test_attachment_context_distinguishes_description_from_reference(self):
        document = director_document()
        document["references"] = [{
            "id": "reference-1", "kind": "image", "path": "subject.png",
            "name": "Subject", "roles": ["subject"],
        }]
        messages, _usage = build_provider_messages({
            "document": document,
            "selected_shot_id": "shot-2",
            "messages": [{"role": "user", "content": "Use these images."}],
            "attachments": [
                {"path": "look.png", "name": "Look", "usage": "describe"},
                {"path": "subject.png", "name": "Subject", "usage": "subject", "reference_id": "reference-1"},
            ],
        })
        context = provider_context(messages)
        self.assertEqual(context["references"][0]["name"], "<Picture 1>")
        self.assertEqual(context["references"][0]["source_name"], "Subject")
        self.assertEqual(context["attached_images"][0]["name"], "Visual context")
        self.assertEqual(context["attached_images"][0]["source_name"], "Look")
        self.assertEqual(context["attached_images"][0]["reference_token"], "")
        self.assertEqual(context["attached_images"][1]["name"], "<Picture 1>")
        self.assertEqual(context["attached_images"][1]["source_name"], "Subject")
        self.assertEqual(context["attached_images"][1]["reference_token"], "<Picture 1>")

    def test_subject_grounding_is_reduced_to_a_token_registry_for_the_director(self):
        document = director_document()
        document["references"] = [{
            "id": "reference-1", "kind": "image", "path": "subject.webp",
            "name": "Subject", "roles": ["subject"],
        }]
        messages, _usage = build_provider_messages({
            "scope": "project",
            "document": document,
            "messages": [{"role": "user", "content": "Make her dance in the rain."}],
            "attachments": [{
                "id": "attachment-1", "path": "subject.webp", "name": "Subject",
                "usage": "subject", "reference_id": "reference-1",
            }],
            "_vision_observations": [{
                "observations": (
                    "A full-body portrait of a platinum-blonde woman wearing a pink crop top and pink bottoms, "
                    "with black boots, against a plain light wall."
                ),
                "subject_candidates": [{
                    "name": "young woman", "location": "",
                    "visual_selectors": ["platinum-blonde woman", "pink crop top"],
                    "grounded_attributes": {
                        "hair": "straight platinum-blonde hair",
                        "clothing": "pink crop top and pink bottoms",
                        "footwear": "black boots",
                    },
                }],
            }],
        })
        context = provider_context(messages)
        attached = context["attached_images"][0]
        self.assertNotIn("observed_visual_facts", attached)
        self.assertNotIn("subject_candidates", context["references"][0])
        self.assertEqual(context["subject_registry"], [{
            "token": "<Subject 1>",
            "source_token": "<Picture 1>",
            "name": "young woman",
            "location": "",
        }])
        self.assertEqual(context["explicit_subject_attributes"], [])

    def test_director_grounds_images_before_structured_request(self):
        document = director_document()
        document["references"] = [{
            "id": "reference-1", "kind": "image", "path": "subject.webp",
            "name": "Subject", "roles": ["subject"],
        }]
        attachments = [{
            "id": "attachment-1", "path": "subject.webp", "name": "Subject",
            "usage": "subject", "reference_id": "reference-1",
            "source_width": 1184, "source_height": 1776,
        }]
        images = [{
            "data_uri": "data:image/png;base64,AAAA", "base64": "AAAA",
            "mime_type": "image/png", "name": "Subject", "usage": "subject",
        }]
        grounding = (
            '{"images":[{"index":1,"observations":"Platinum-blonde straight hair, pink crop top and '
            'pink bottoms, black boots, full-body pose against a plain light wall.","subjects":[{'
            '"name":"young woman","visual_selectors":["platinum-blonde woman","pink crop top"],'
            '"grounded_attributes":{"hair":"platinum-blonde straight hair",'
            '"clothing":"pink crop top and pink bottoms","footwear":"black boots"}}]}]}'
        )
        progress = []
        with patch("video.director.load_vision_images", return_value=(attachments, images)), patch(
            "video.director.generate_chat", side_effect=[grounding, "The requested action is feasible."]
        ) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "thinking_mode": "High",
                "messages": [{"role": "user", "content": "Review her dancing in the rain."}],
                "attachments": [{"path": "subject.webp", "usage": "subject"}],
            }, progress.append)

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args_list[0].args[2], images)
        self.assertEqual(generate.call_args_list[1].args[2], [])
        self.assertEqual(generate.call_args_list[0].args[0]["thinking_mode"], "High")
        self.assertEqual(generate.call_args_list[1].args[0]["thinking_mode"], "High")
        self.assertGreaterEqual(generate.call_args_list[0].args[0]["max_response_tokens"], 4_000)
        context = provider_context(generate.call_args_list[1].args[1])
        self.assertNotIn("observed_visual_facts", context["attached_images"][0])
        self.assertEqual(context["subject_registry"][0]["token"], "<Subject 1>")
        self.assertNotIn("visual_selectors", context["subject_registry"][0])
        self.assertEqual(result["vision_observations"][0]["usage"], "subject")
        self.assertEqual(progress, [
            {
                "phase": "vision_grounding",
                "attempt": 1,
                "maximum_attempts": 2,
            },
            {
                "phase": "director_generation",
                "grounded_images": 1,
            },
        ])

    def test_director_grounds_each_image_in_an_isolated_call(self):
        document = director_document()
        document["references"] = [
            {"id": "reference-1", "kind": "image", "path": "one.png", "roles": ["subject"]},
            {"id": "reference-2", "kind": "image", "path": "two.png", "roles": ["subject"]},
        ]
        attachments = [
            {"id": "a1", "path": "one.png", "name": "One", "usage": "subject", "reference_id": "reference-1"},
            {"id": "a2", "path": "two.png", "name": "Two", "usage": "subject", "reference_id": "reference-2"},
        ]
        images = [
            {"data_uri": "data:image/png;base64,ONE", "base64": "ONE"},
            {"data_uri": "data:image/png;base64,TWO", "base64": "TWO"},
        ]
        grounded_one = (
            '{"images":[{"index":1,"observations":"A woman in a red coat.","subjects":['
            '{"name":"woman","visual_selectors":["red coat"],'
            '"grounded_attributes":{"clothing":"red coat"}}]}]}'
        )
        grounded_two = (
            '{"images":[{"index":1,"observations":"A man in a blue shirt.","subjects":['
            '{"name":"man","visual_selectors":["blue shirt"],'
            '"grounded_attributes":{"clothing":"blue shirt"}}]}]}'
        )
        progress = []
        with patch("video.director.load_vision_images", return_value=(attachments, images)), patch(
            "video.director.generate_chat",
            side_effect=[grounded_one, grounded_two, "The requested action is feasible."],
        ) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "messages": [{"role": "user", "content": "Review the interaction."}],
                "attachments": attachments,
            }, progress.append)

        self.assertEqual(generate.call_count, 3)
        self.assertEqual(generate.call_args_list[0].args[2], [images[0]])
        self.assertEqual(generate.call_args_list[1].args[2], [images[1]])
        self.assertEqual([item["index"] for item in result["vision_observations"]], [1, 2])
        grounding_progress = [
            item for item in progress if item["phase"] == "vision_grounding"
        ]
        self.assertEqual([item["image_index"] for item in grounding_progress], [1, 2])
        self.assertEqual(progress[-1], {
            "phase": "director_generation",
            "grounded_images": 2,
        })

    def test_selected_shot_proposal_can_complete_reference_semantics(self):
        value = director_document()
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Use the identity reference","operations":['
            '{"op":"update_project","fields":{"task_types":["reference generation"],'
            '"subject_definitions":[{"label":"Subject 1","text":"is the blonde woman in <Picture 1>, with long hair and a white dress."}],'
            '"summary":"The target video follows <Subject 1> as she unfolds the letter.",'
            '"retention_analysis":[{"label":"<Subject 1>","where":"appears in [Shot 2]",'
            '"relationship":"fully_preserved","detail":"Her identity, hair, and white dress are retained."}]}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{'
            '"subjects":"<Subject 1>, the blonde woman from <Picture 1>, stands beside the window.",'
            '"steps":[{"type":"action","text":"<Subject 1> looks toward the window while holding the letter."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual(result["document"]["subject_definitions"][0]["label"], "Subject 1")
        self.assertIn("<Subject 1> is the blonde woman in <Picture 1>", result["compiled_prompt"])
        self.assertIn("[Shot 2] At 00:04.000", result["compiled_prompt"])

    def test_grand_director_handles_multiple_references_across_multiple_shots(self):
        value = director_document()
        value["references"] = [
            {"id": "reference-1", "kind": "image", "path": "woman.png", "name": "Woman", "roles": ["subject"]},
            {"id": "reference-2", "kind": "image", "path": "office.png", "name": "Office", "roles": ["scene"]},
        ]
        document = normalize_document(value)
        long_composition = (
            "A carefully staged medium-wide composition keeps <Subject 1> prominent while the architectural lines, "
            "furniture spacing, reflective surfaces, doorway placement, and practical objects of <Subject 2> remain "
            "clearly legible around her, preserving stable screen direction and spatial continuity."
        )
        long_subjects = (
            "<Subject 1>, the blonde woman from <Picture 1>, retains her long hair, fitted white dress, necklace, facial "
            "appearance, and body proportions while maintaining a precise position relative to the chair and desk in "
            "every moment of the shot."
        )
        long_environment = (
            "<Subject 2>, the office from <Picture 2>, preserves its wall treatment, desk materials, chair placement, "
            "window geometry, neutral palette, organized props, and realistic depth, with no unexplained changes between "
            "the two camera setups."
        )
        long_lighting = (
            "Warm directional daylight crosses the room from the same side, shaping consistent highlights on <Subject 1> "
            "and soft grounded shadows throughout <Subject 2>, while restrained practical fill keeps skin, fabric, paper, "
            "and furniture texture visible."
        )
        long_action = (
            "<Subject 1> rises smoothly, steadies her posture, keeps the paper oriented toward her body, unfolds it along "
            "the existing crease, turns it toward the camera, and holds the final readable position without changing her "
            "wardrobe, identity, or relationship to <Subject 2>."
        )
        proposal = {
            "summary": "Ground both references across both shots",
            "operations": [{
                "op": "update_project",
                "fields": {
                    "main_description": "<Subject 1> rises and reveals a note while remaining inside <Subject 2>.",
                    "style": "Realistic live-action cinematography with restrained natural color and stable continuity.",
                    "overall_soundscape": "Quiet room tone continues beneath fabric movement, footsteps, and crisp paper rustle.",
                    "non_diegetic_music": "N/A",
                    "task_types": ["reference generation"],
                    "subject_definitions": [
                        {"label": "Subject 1", "text": "is the blonde woman in <Picture 1>, with long hair, a fitted white dress, and a light necklace."},
                        {"label": "Subject 2", "text": "is the contemporary office environment in <Picture 2>, including its desk, chair, walls, windows, and neutral palette."},
                    ],
                    "summary": "The target video follows <Subject 1> as she rises and reveals a note inside <Subject 2> across two continuous shots.",
                    "retention_analysis": [
                        {"label": "<Subject 1>", "where": "appears in [Shot 1] and [Shot 2]", "relationship": "fully_preserved", "detail": "Her identity, hair, white dress, necklace, and proportions remain consistent."},
                        {"label": "<Subject 2>", "where": "appears in [Shot 1] and [Shot 2]", "relationship": "fully_preserved", "detail": "The office layout, furnishings, palette, and lighting direction remain consistent."},
                    ],
                },
            }],
        }
        for shot_id in ("shot-1", "shot-2"):
            steps = [{"type": "action", "text": long_action}]
            if shot_id == "shot-1":
                steps.extend(copy.deepcopy(dialogue_steps(document["shots"][0])))
            proposal["operations"].append({
                "op": "update_shot", "shot_id": shot_id,
                "fields": {
                    "composition": long_composition, "subjects": long_subjects,
                    "environment": long_environment, "lighting": long_lighting,
                    "steps": steps,
                    "sounds": ["Fabric shifts softly and the paper opens with a crisp rustle."],
                },
            })
        response = (
            "Both references are grounded throughout the two-shot timeline.\n"
            f"{CHANGESET_BEGIN}\n{json.dumps(proposal)}\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project", "document": document, "project_name": "Reference test",
                "brief": "Use both references across the complete two-shot video.",
                "messages": [{"role": "user", "content": "Make both references drive the complete two-shot video."}],
            })
        self.assertIsNotNone(result["proposal"])
        self.assertFalse(result["proposal_error"])
        self.assertEqual(generate.call_count, 1)
        preview = preview_changeset(document, result["proposal"])
        prompt = preview["compiled_prompt"]
        self.assertIn("<Subject 1> is the woman in <Picture 1>", prompt)
        self.assertNotIn("<Subject 1> is the blonde woman", prompt)
        self.assertIn("<Subject 2> is the contemporary office environment in <Picture 2>", prompt)
        self.assertGreaterEqual(prompt.count("<Subject 1>"), 5)
        self.assertGreaterEqual(prompt.count("<Subject 2>"), 5)
        self.assertIn("[Shot 1]", prompt)
        self.assertIn("[Shot 2] At 00:04.000", prompt)

    def test_concise_reference_proposal_binds_sole_missing_picture_source(self):
        value = director_document()
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        response = (
            "Apply the requested production changes.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Ground the subject reference","operations":['
            '{"op":"update_project","fields":{"task_types":["reference generation"],'
            '"subject_definitions":[{"label":"Subject 1","text":"is the blonde woman with long hair, a white dress, and a pearl necklace."}],'
            '"summary":"The target video follows <Subject 1> through both shots.",'
            '"retention_analysis":[{"label":"<Subject 1>","where":"appears in [Shot 1] and [Shot 2]",'
            '"relationship":"fully_preserved","detail":"Her hair, dress, necklace, and identity remain consistent."}]}},'
            '{"op":"update_shot","shot_id":"shot-1","fields":{"subjects":"<Subject 1> sits with the letter."}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"subjects":"<Subject 1> stands beside the window."}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project", "document": document,
                "messages": [{"role": "user", "content": "Apply the requested production changes."}],
            })
        self.assertIsNotNone(result["proposal"])
        self.assertFalse(result["proposal_error"])
        self.assertEqual(generate.call_count, 1)
        preview = preview_changeset(document, result["proposal"])
        self.assertIn("<Picture 1>", preview["document"]["subject_definitions"][0]["text"])
        self.assertIn("<Picture 1>", preview["compiled_prompt"])

    def test_picture_retention_alias_is_repaired_to_its_sole_subject(self):
        value = director_document()
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        proposal = {
            "summary": "Create a joyful rain dance",
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "operations": [{
                "op": "update_project",
                "fields": {
                    "task_types": ["reference generation"],
                    "subject_definitions": [{
                        "label": "Subject 1",
                        "text": "is the woman in <Picture 1>, with blonde hair and a pink outfit.",
                    }],
                    "summary": "The target video follows <Picture 1> dancing joyfully in the rain.",
                    "retention_analysis": [{
                        "label": "<Picture 1>",
                        "where": "appears in [Shot 1] and [Shot 2]",
                        "relationship": "fully_preserved",
                        "detail": "The appearance from <Picture 1> is retained.",
                    }],
                },
            }, {
                "op": "update_shot",
                "shot_id": "shot-1",
                "fields": {"subjects": "<Subject 1> dances joyfully in the rain."},
            }],
        }

        preview = preview_changeset(document, proposal)

        self.assertEqual(preview["document"]["retention_analysis"][0]["label"], "<Subject 1>")
        self.assertIn("<Subject 1>", preview["document"]["summary"])
        self.assertNotIn("<Picture 1>", preview["document"]["summary"])
        self.assertIn("<Subject 1> (appears in [Shot 1] and [Shot 2])", preview["compiled_prompt"])

    def test_direct_picture_subject_and_plain_shot_ids_are_canonicalized(self):
        value = director_document()
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        proposal = {
            "summary": "Use the subject",
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "operations": [{
                "op": "update_project",
                "fields": {
                    "task_types": ["ref2va"],
                    "subject_definitions": [{
                        "label": "Picture 1",
                        "text": "A blonde woman with a pink dress and black boots.",
                    }],
                    "summary": "A blonde woman dances.",
                    "retention_analysis": [{
                        "label": "<Picture 1>",
                        "where": "shot-1, shot-2",
                        "relationship": "fully_preserved",
                        "detail": "Her blonde hair, pink dress, and black boots remain consistent.",
                    }],
                },
            }],
        }

        preview = preview_changeset(document, proposal)

        self.assertEqual(preview["document"]["task_types"], ["reference generation"])
        self.assertEqual(preview["document"]["subject_definitions"][0]["label"], "Subject 1")
        self.assertIn("<Picture 1>", preview["document"]["subject_definitions"][0]["text"])
        self.assertEqual(preview["document"]["retention_analysis"][0]["label"], "<Subject 1>")
        self.assertEqual(preview["document"]["retention_analysis"][0]["where"], "[Shot 1], [Shot 2]")
        self.assertIn("<Subject 1>", preview["document"]["summary"])
        self.assertGreaterEqual(preview["compiled_prompt"].count("<Subject 1>"), 4)

    def test_direct_picture_definition_missing_from_summary_is_repaired_without_retry(self):
        value = director_document()
        value["shots"] = value["shots"][:1]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "woman.png",
            "name": "Woman", "roles": ["subject"],
        }]
        document = normalize_document(value)
        response = (
            "Apply the requested production changes.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Create the rain dance","operations":[{"op":"update_project","fields":{'
            '"task_types":["reference generation"],'
            '"subject_definitions":[{"label":"Picture 1","text":"is the blonde woman in a pink outfit."}],'
            '"summary":"A blonde woman dances joyfully in the rain.",'
            '"retention_analysis":[{"label":"<Picture 1>","where":"appears in [Shot 1]",'
            '"relationship":"fully_preserved","detail":"Her blonde hair and pink outfit are retained."}]}}]}'
            f"\n{CHANGESET_END}"
        )

        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "thinking_mode": "High",
                "messages": [{"role": "user", "content": "Apply the requested production changes."}],
            })

        self.assertEqual(generate.call_count, 1)
        self.assertFalse(result["proposal_error"])
        project_fields = result["proposal"]["operations"][0]["fields"]
        self.assertIn("<Subject 1>", project_fields["summary"])
        self.assertIn("<Picture 1>", project_fields["subject_definitions"][0]["text"])
        preview = preview_changeset(document, result["proposal"])
        self.assertIn("<Subject 1>", preview["document"]["summary"])
        self.assertIn("<Picture 1>", preview["compiled_prompt"])

    def test_project_context_contains_every_shot(self):
        _document, context = compact_project_context({
            "document": director_document(),
            "project_name": "Letter",
            "brief": "A farewell on a train.",
        })
        self.assertEqual(context["scope"], "project")
        self.assertEqual([shot["id"] for shot in context["shots"]], ["shot-1", "shot-2"])
        self.assertTrue(all(shot["role"] == "project" for shot in context["shots"]))

    def test_project_proposal_can_compose_multiple_shots(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            "A three-shot progression gives the farewell a clearer visual arc.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose a three-shot farewell","operations":['
            '{"op":"update_project","fields":{"overall_soundscape":"Train wheels, rain, and paper rustle."}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"start":3.0,"steps":['
            '{"type":"action","text":"She studies her reflection."}]}},'
            '{"op":"add_shot","shot":{"id":"shot-farewell","start":6.0,"transition":"the camera cuts to",'
            '"composition":"Close-up of the folded letter.","steps":['
            '{"type":"action","text":"Her hand leaves the letter on the seat."}],'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow","target":"the letter"}}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(parsed["proposal"]["scope"], {"type": "project"})
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual([shot["id"] for shot in result["document"]["shots"]], ["shot-1", "shot-2", "shot-farewell"])
        self.assertEqual(result["document"]["shots"][1]["start"], 3.0)
        self.assertIn("Train wheels", result["document"]["overall_soundscape"])
        self.assertIn("[Shot 3] At 00:06.000", result["compiled_prompt"])
        self.assertEqual(dialogue_steps(result["document"]["shots"][0])[0]["text"], "Do not rewrite this.")

    def test_project_proposal_cannot_remove_protected_dialogue(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Remove opening",
            "operations": [{"op": "remove_shot", "shot_id": "shot-1"}],
        }
        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            preview_changeset(document, proposal)

    def test_project_proposal_drops_a_placeholder_remove_operation(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Update the second shot",
            "operations": [
                {
                    "op": "update_shot",
                    "shot_id": "shot-2",
                    "fields": {"steps": [{"type": "action", "text": "She turns toward the doorway."}]},
                },
                {"op": "remove_shot", "shot_id": "unneeded shot id"},
            ],
        }

        result = preview_changeset(document, proposal)

        self.assertEqual(action_text(result["document"]["shots"][1]), "She turns toward the doorway.")
        self.assertEqual([item["op"] for item in result["proposal"]["operations"]], ["update_shot"])

    def test_conflicting_remove_and_update_keeps_the_content_update(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Keep and update Shot 1",
            "operations": [
                {"op": "remove_shot", "shot_id": "shot-1"},
                {"op": "update_shot", "shot_id": "shot-1", "fields": {
                    "steps": [{"type": "action", "text": "She opens the door."}],
                }},
            ],
        }

        result = preview_changeset(document, proposal)

        shot = next(item for item in result["document"]["shots"] if item["id"] == "shot-1")
        self.assertEqual(action_text(shot), "She opens the door.")
        self.assertFalse(any(
            operation["op"] == "remove_shot" and operation.get("shot_id") == "shot-1"
            for operation in result["proposal"]["operations"]
        ))

    def test_complete_rewrite_can_replace_every_unprotected_shot(self):
        value = director_document()
        for item in value["shots"]:
            item["steps"] = [step for step in item["steps"] if step["type"] == "action"]
            item["visible_text"] = []
        document = normalize_document(value)
        replacement = {
            "id": "replacement-shot", "start": 0,
            "composition": "A bakery counter fills the frame.",
            "subjects": "A baker stands behind the counter.",
            "environment": "A small bakery before sunrise.",
            "lighting": "Warm practical light meets cool window light.",
            "steps": [{"type": "action", "text": "The baker opens the shutters."}],
            "sounds": ["Wooden shutters scrape open."],
        }
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Replace the old timeline",
            "operations": [
                {"op": "remove_shot", "shot_id": "shot-1"},
                {"op": "remove_shot", "shot_id": "shot-2"},
                {"op": "add_shot", "shot": replacement},
            ],
        }

        preview = preview_changeset(document, proposal)

        self.assertEqual([item["id"] for item in preview["document"]["shots"]], ["replacement-shot"])

    def test_project_proposal_rejects_an_empty_resulting_timeline(self):
        value = director_document()
        for item in value["shots"]:
            item["steps"] = [step for step in item["steps"] if step["type"] == "action"]
            item["visible_text"] = []
        document = normalize_document(value)
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "project"},
            "summary": "Remove every shot",
            "operations": [
                {"op": "remove_shot", "shot_id": "shot-1"},
                {"op": "remove_shot", "shot_id": "shot-2"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "resulting timeline"):
            preview_changeset(document, proposal)

    def test_project_chat_uses_grand_director_contract(self):
        response = "The two-shot structure already reads clearly."
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "project_name": "Letter",
                "brief": "A farewell on a train.",
                "thinking_mode": "Disabled",
                "messages": [{"role": "user", "content": "Review the full video."}],
            })
        self.assertIsNone(result["proposal"])
        self.assertEqual(result["scope"], "project")
        provider_messages = generate.call_args.args[1]
        self.assertIn("Grand Director", provider_messages[0]["content"])
        self.assertIn("create multiple shots", provider_messages[0]["content"])
        context = provider_context(provider_messages)
        self.assertEqual(len(context["shots"]), 2)
        self.assertEqual(generate.call_count, 1)
        self.assertNotIn("temperature", generate.call_args.args[0])
        self.assertEqual(generate.call_args.args[0]["thinking_mode"], "Disabled")

    def test_i2va_director_context_enforces_first_frame_lock(self):
        messages, _usage = build_provider_messages({
            "scope": "project",
            "document": self.i2va_document(),
            "messages": [{"role": "user", "content": "Create the full prompt."}],
        })

        self.assertIn("FIRST-FRAME LOCK", messages[0]["content"])
        self.assertIn("A request for a complete/full prompt does not authorize", messages[0]["content"])

    def test_l2va_director_keeps_ref2va_subject_tokens_out_of_base_mode(self):
        value = director_document()
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "last.png",
            "name": "Last frame", "roles": ["last_frame"],
            "subject_candidates": [{
                "name": "young woman", "location": "center",
                "visual_selectors": ["woman in red"],
            }],
        }]
        # Simulate reference semantics left over from an earlier REF2VA role.
        value["subject_definitions"] = [{
            "label": "Subject 1", "text": "is the young woman in <Picture 1>.",
        }]
        document = normalize_document(value)
        self.assertEqual(document["resolved_mode"], "l2va")
        response = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Converge on the final frame","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"steps":['
            '{"type":"action","text":"<Subject 1> settles into the final pose in <Picture 1>."},'
            '{"type":"sound","text":"Her last footstep lands softly."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "require_proposal": True,
                "messages": [{
                    "role": "user",
                    "content": "Have the woman in red approach and settle into the last frame.",
                }],
            })

        self.assertEqual(generate.call_count, 1, result)
        self.assertIsNotNone(result["proposal"], result)
        context = provider_context(generate.call_args.args[1])
        self.assertEqual(context["subject_registry"], [])
        self.assertEqual(context["minimax_tokens"]["subjects"], [])
        self.assertIn("BASE KEYFRAME MODE", generate.call_args.args[1][0]["content"])
        self.assertNotIn("<Subject 1>", generate.call_args.args[1][-1]["content"])
        preview = preview_changeset(document, result["proposal"])
        self.assertNotIn("<Subject 1>", preview["compiled_prompt"])
        self.assertIn("The subject settles into the final pose in <Picture 1>.", preview["compiled_prompt"])
        self.assertIn("Her last footstep lands softly.", preview["compiled_prompt"])

    def test_mixed_reference_mode_rejects_first_shot_background_rewrite(self):
        value = self.i2va_document()
        value["references"].append({
            "id": "reference-2", "kind": "image", "path": "subject.png",
            "roles": ["subject"],
        })
        document = normalize_document(value)
        self.assertEqual(document["resolved_mode"], "ref2va")

        with self.assertRaisesRegex(ValueError, "First-frame lock.*environment"):
            _validate_first_frame_proposal_lock(document, {
                "operations": [{
                    "op": "update_shot",
                    "shot_id": document["shots"][0]["id"],
                    "fields": {"environment": "The background from Picture 2."},
                }],
            })

    def test_i2va_rejects_fabricated_anchor_and_inherited_scene_fields(self):
        original = self.i2va_document()
        result = copy.deepcopy(original)
        result["shots"][0]["lighting"] = "Invented orange sunset light."
        result["shots"][1]["environment"] = "An invented marble ballroom."

        with self.assertRaisesRegex(ValueError, "Shot 1 anchored visual fields"):
            _validate_i2va_anchor_preservation(
                original,
                result,
                {"messages": [{"role": "user", "content": "Create the complete prompt."}]},
            )

        result["shots"][0] = copy.deepcopy(original["shots"][0])
        with self.assertRaisesRegex(ValueError, "later shots to inherit"):
            _validate_i2va_anchor_preservation(
                original,
                result,
                {"messages": [{"role": "user", "content": "Create the complete prompt."}]},
            )

    def test_i2va_allows_explicit_later_scene_change(self):
        original = self.i2va_document()
        result = copy.deepcopy(original)
        result["shots"][1]["environment"] = "A different exterior location."
        result["shots"][1]["lighting"] = "Night lighting from street lamps."

        _validate_i2va_anchor_preservation(
            original,
            result,
            {"messages": [{
                "role": "user",
                "content": "In Shot 2, change the location to the street and switch the lighting to night.",
            }]},
        )

        _validate_i2va_anchor_preservation(
            original,
            result,
            {
                "document": original,
                "brief": "After the cut, change the scene to a street and shift the lighting to night.",
                "messages": [{"role": "user", "content": "Build the full prompt from the brief."}],
            },
        )

    def test_i2va_allows_concrete_requested_bedroom_shot(self):
        original = self.i2va_document()
        result = copy.deepcopy(original)
        result["shots"][1]["id"] = "shot-new-bedroom"
        result["shots"][1]["environment"] = "Her bedroom with a bed and nightstand."
        result["shots"][1]["lighting"] = "Warm bedside-lamp light."

        for instruction in (
            "Add a second shot in her bedroom, where she sits on the bed.",
            "Next shot: she is in the bedroom and walks toward the nightstand.",
            "The girl in picture 1 will smile and start walking towards the camera. "
            "When she reaches the camera, the shot will cut to a new shot - the girl "
            "will be standing in a luxury bedroom, and the camera will pan around her.",
            "Keep the same woman but change only her wardrobe to a black tuxedo. "
            "After the cut she adjusts one cuff in a luxury hotel lobby.",
            "She walks into an abandoned industrial warehouse in the final shot.",
        ):
            _validate_i2va_anchor_preservation(
                original,
                result,
                {"messages": [{"role": "user", "content": instruction}]},
            )

    def test_i2va_concrete_place_detection_does_not_allow_unrequested_room(self):
        original = self.i2va_document()
        result = copy.deepcopy(original)
        result["shots"][1]["environment"] = "An ornate marble ballroom."
        result["shots"][1]["lighting"] = "Crystal chandeliers cast warm light."

        with self.assertRaisesRegex(ValueError, "later shots to inherit"):
            _validate_i2va_anchor_preservation(
                original,
                result,
                {"messages": [{"role": "user", "content": "Add another shot where she waves."}]},
            )

    def test_complete_i2va_requires_action_but_not_fabricated_visual_fields(self):
        document = self.i2va_document()
        for shot in document["shots"]:
            shot["composition"] = ""
            shot["subjects"] = ""
            shot["environment"] = ""
            shot["lighting"] = ""

        _validate_requested_project_result(document, {
            "messages": [{"role": "user", "content": "Create the complete video prompt."}],
        })

    def test_complete_mixed_reference_keeps_first_frame_visual_fields_pixel_owned(self):
        document = self.i2va_document()
        document["references"].append({
            "id": "reference-subject",
            "kind": "image",
            "path": "subject.png",
            "roles": ["subject"],
        })
        document = normalize_document(document)
        self.assertEqual(document["resolved_mode"], "ref2va")
        for shot in document["shots"]:
            shot["composition"] = ""
            shot["subjects"] = ""
            shot["environment"] = ""
            shot["lighting"] = ""

        _validate_requested_project_result(document, {
            "messages": [{"role": "user", "content": "Create the complete video prompt."}],
        })

    def test_explicit_full_prompt_request_retries_missing_proposal_once(self):
        proposal_response = (
            "I have prepared the complete structured production.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose the full video","operations":['
            '{"op":"update_project","fields":{"style":"Live-action, cinematic"}},'
            '{"op":"update_shot","shot_id":"shot-1","fields":{"composition":"Medium shot",'
            '"subjects":"A woman and a letter","environment":"Train carriage","lighting":"Cool window light"}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"composition":"Close-up",'
            '"subjects":"The woman","environment":"Train carriage","lighting":"Cool window light",'
            '"steps":[{"type":"action","text":"She watches the city lights recede."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch(
            "video.director.generate_chat",
            side_effect=["Here is a complete prose answer without a change set.", proposal_response],
        ) as generate:
            progress = []
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "project_name": "Letter",
                "brief": "A farewell on a train.",
                "thinking_mode": "High",
                "messages": [{"role": "user", "content": "Create the full prompt for this video."}],
            }, progress.append)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(result["proposal"]["scope"], {"type": "project"})
        self.assertEqual(generate.call_args_list[0].args[0]["temperature"], 0.2)
        self.assertEqual(generate.call_args_list[1].args[0]["max_response_tokens"], 0)
        self.assertEqual(generate.call_args_list[1].args[0]["temperature"], 0.0)
        self.assertEqual(generate.call_args_list[0].args[0]["thinking_mode"], "High")
        self.assertEqual(generate.call_args_list[1].args[0]["thinking_mode"], "Low")
        retry_messages = generate.call_args.args[1]
        self.assertIn("machine-applicable structured proposal", retry_messages[-2]["content"])
        self.assertIn(CHANGESET_BEGIN, retry_messages[-1]["content"])
        self.assertEqual(progress, [
            {
                "phase": "director_generation",
                "grounded_images": 0,
            },
            {
                "phase": "proposal_correction",
                "attempt": 1,
                "maximum_attempts": 3,
            },
        ])

    def test_explicit_shot_count_retries_structurally_incomplete_proposal(self):
        document = normalize_document(director_document())
        document["shots"] = document["shots"][:1]
        document["shots"][0]["steps"] = [
            step for step in document["shots"][0]["steps"] if step["type"] == "action"
        ]
        first_response = (
            "I composed the requested progression.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose the progression","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"steps":[{"type":"action","text":"The woman stands and reveals the note."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        corrected_response = (
            "I separated the action and reveal into two shots.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose two distinct shots","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"steps":[{"type":"action","text":"The woman rises from the red armchair."}]}},'
            '{"op":"add_shot","shot":{"id":"shot-note-reveal","start":4.0,'
            '"transition":"the camera cuts to","composition":"A close-up frames the note.",'
            '"subjects":"The woman holds the unfolded note toward the camera.",'
            '"environment":"The same room remains softly visible behind her.",'
            '"lighting":"Soft window light keeps the handwriting legible.",'
            '"steps":[{"type":"action","text":"She reveals the handwritten message."}],'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow","target":"the note"},'
            '"sounds":["Paper opens with a crisp rustle."]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch(
            "video.director.generate_chat",
            side_effect=[first_response, corrected_response],
        ) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "require_proposal": True,
                "messages": [{
                    "role": "user",
                    "content": "Split the action and note reveal into two distinct shots.",
                }],
            })
        self.assertEqual(generate.call_count, 2)
        self.assertFalse(result["proposal_error"])
        preview = preview_changeset(document, result["proposal"])["document"]
        self.assertEqual(len(preview["shots"]), 2)
        self.assertEqual(preview["shots"][1]["id"], "shot-note-reveal")
        retry_messages = generate.call_args.args[1]
        self.assertIn("requested 2 resulting shots", retry_messages[-1]["content"])

    def test_missing_proposal_after_correction_is_reported(self):
        with patch("video.director.generate_chat", return_value="Still only prose.") as generate:
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "messages": [{"role": "user", "content": "Compose the entire video."}],
            })
        self.assertEqual(generate.call_count, 4)
        self.assertIsNone(result["proposal"])
        self.assertEqual(result["status"], "ready")
        self.assertIn("required structured proposal", result["proposal_error"])
        self.assertIn("Nothing was changed", result["message"])
        self.assertIsNone(result["pending_plan"])

    def test_failed_proposal_keeps_validation_error_without_requesting_clarification(self):
        invalid = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage a greeting","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":["She says hello."]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", side_effect=[invalid, "Only prose.", "Still prose.", "No JSON."]):
            result = director_chat({
                "scope": "shot",
                "document": director_document(),
                "selected_shot_id": "shot-1",
                "messages": [{"role": "user", "content": "Make her say hello, then wave."}],
            })

        self.assertIsNone(result["proposal"])
        self.assertEqual(result["status"], "ready")
        self.assertIn("step 1 must be an object", result["proposal_error"])
        self.assertIsNone(result["clarification"])
        self.assertIsNone(result["pending_plan"])

    def test_legacy_validation_pending_plan_does_not_force_proposal_mode(self):
        with patch("video.director.generate_chat", return_value="The prior proposal had invalid step structure.") as generate:
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "pending_plan": {
                    "clarification_id": "proposal-validation",
                    "original_request": "Make her say hello, then wave.",
                    "validation_issue": "step 1 must be an object",
                },
                "messages": [
                    {"role": "user", "content": "Make her say hello, then wave."},
                    {"role": "assistant", "content": "Please clarify what should be changed."},
                    {"role": "user", "content": "What specifically needs clarification?"},
                ],
            })

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["message"], "The prior proposal had invalid step structure.")
        self.assertIsNone(result["proposal"])
        self.assertFalse(result["proposal_error"])
        self.assertIsNone(result["pending_plan"])

    def test_ambiguous_subject_binding_asks_then_continues_pending_plan(self):
        value = director_document()
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "women.png",
            "name": "Women", "roles": ["subject"],
            "observed_visual_facts": "Two women stand side by side.",
            "subject_candidates": [
                {"name": "young woman", "location": "left"},
                {"name": "young woman", "location": "right"},
            ],
        }]
        document = normalize_document(value)
        first_request = {
            "scope": "project",
            "document": document,
            "messages": [{"role": "user", "content": "She sits down on a chair."}],
        }
        with patch("video.director.generate_chat") as generate:
            clarification = director_chat(first_request)

        self.assertEqual(generate.call_count, 0)
        self.assertEqual(clarification["status"], "needs_clarification")
        self.assertEqual(
            clarification["clarification"]["choices"],
            ["young woman on the left", "young woman on the right"],
        )
        response = (
            "I continued the pending plan.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Seat the selected subject","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"steps":[{"type":"action","text":"<Subject 1> sits down on a chair."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        continued_request = {
            "scope": "project",
            "document": document,
            "pending_plan": clarification["pending_plan"],
            "messages": [
                {"role": "user", "content": "She sits down on a chair."},
                {"role": "assistant", "content": clarification["message"]},
                {"role": "user", "content": "The young woman on the left."},
            ],
        }
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat(continued_request)

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["status"], "ready")
        preview = preview_changeset(document, result["proposal"])
        self.assertEqual(
            preview["document"]["subject_definitions"][0]["text"],
            "is only the young woman on the left in <Picture 1>.",
        )
        self.assertIn("<Subject 1> sits down on a chair", preview["compiled_prompt"])

    def test_visual_identifiers_resolve_two_private_subjects_without_prompt_leakage(self):
        value = director_document()
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "people.png",
            "name": "People", "roles": ["subject"],
            "observed_visual_facts": "Two people stand side by side, one dressed in black and one in white.",
            "subject_candidates": [
                {
                    "name": "person", "location": "left",
                    "visual_selectors": ["person in black", "black outfit"],
                },
                {
                    "name": "person", "location": "right",
                    "visual_selectors": ["person in white", "white outfit"],
                },
            ],
        }]
        document = normalize_document(value)
        response = (
            "I assigned both referenced people independently.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage both subjects","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"steps":[{"type":"action","text":"<Subject 1> sits down while <Subject 2> opens the door."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        request = {
            "scope": "project",
            "document": document,
            "messages": [{
                "role": "user",
                "content": "Person in black sits down; person in white opens the door.",
            }],
        }
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat(request)

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["status"], "ready")
        context = provider_context(generate.call_args.args[1])
        self.assertEqual(
            [item["token"] for item in context["subject_registry"]],
            ["<Subject 1>", "<Subject 2>"],
        )
        self.assertEqual(
            generate.call_args.args[1][-1]["content"],
            "<Subject 1> sits down; <Subject 2> opens the door.",
        )
        self.assertNotIn("visual_selectors", json.dumps(context))
        preview = preview_changeset(document, result["proposal"])
        definitions = preview["document"]["subject_definitions"]
        self.assertEqual(
            [item["text"] for item in definitions],
            [
                "is only the person on the left in <Picture 1>.",
                "is only the person on the right in <Picture 1>.",
            ],
        )
        prompt = preview["compiled_prompt"].casefold()
        self.assertIn("<subject 1> sits down", prompt)
        self.assertIn("<subject 2> opens the door", prompt)
        self.assertNotIn("person in black", prompt)
        self.assertNotIn("person in white", prompt)
        self.assertNotIn("black outfit", prompt)
        self.assertNotIn("white outfit", prompt)

    def test_first_frame_and_subject_reference_keep_roles_isolated(self):
        value = director_document()
        value["shots"] = [value["shots"][0]]
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [
            {
                "id": "reference-1", "kind": "image", "path": "first.png",
                "name": "First frame", "roles": ["first_frame"],
                "observed_visual_facts": "A blonde woman in a grey top and white skirt stands outdoors.",
                "subject_candidates": [{
                    "name": "young woman", "location": "center",
                    "visual_selectors": ["blonde hair", "grey top"],
                    "grounded_attributes": {
                        "hair": "long blonde hair",
                        "clothing": "grey patterned top and white skirt",
                    },
                }],
            },
            {
                "id": "reference-2", "kind": "image", "path": "second.png",
                "name": "Second woman", "roles": ["subject"],
                "observed_visual_facts": "A brunette woman wears a red dress.",
                "subject_candidates": [{
                    "name": "young woman", "location": "center",
                    "visual_selectors": ["brunette woman", "red dress"],
                    "grounded_attributes": {
                        "hair": "long brown hair",
                        "clothing": "red dress",
                    },
                }],
            },
        ]
        document = normalize_document(value)
        response = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Stage the greeting and entrance","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"dialogue","speaker":"<Subject 1>","speaker_id":"S1",'
            '"language":"English","text":"Hello there"},'
            '{"type":"action","text":"<Subject 2> walks into the frame from the left and smiles."},'
            '{"type":"dialogue","speaker":"<Subject 1>","speaker_id":"S1",'
            '"language":"English","text":"General kenobi"}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "require_proposal": True,
                "messages": [{
                    "role": "user",
                    "content": (
                        'The girl in Picture 1 will say "Hello there", then the girl from Picture 2 '
                        'will walk into the frame from the left, smile and say "General kenobi".'
                    ),
                }],
            })

        self.assertEqual(result["status"], "ready", result)
        self.assertIsNotNone(result["proposal"], result)
        self.assertEqual(
            result["proposal"]["base_document_hash"],
            document_fingerprint(document),
            result["proposal"],
        )
        resolved_instruction = generate.call_args.args[1][-1]["content"]
        self.assertIn('<Subject 1> will say "Hello there"', resolved_instruction)
        self.assertIn("<Subject 2> will walk", resolved_instruction)
        self.assertIn("FIRST-FRAME LOCK", generate.call_args.args[1][0]["content"])
        context = provider_context(generate.call_args.args[1])
        self.assertEqual(
            [item["token"] for item in context["subject_registry"]],
            ["<Subject 1>", "<Subject 2>"],
        )
        preview = preview_changeset(document, result["proposal"])
        definitions = {
            f"<{item['label']}>" if not item["label"].startswith("<") else item["label"]: item["text"]
            for item in preview["document"]["subject_definitions"]
        }
        self.assertIn("supplied first frame of [Shot 1]", definitions["<Picture 1>"])
        self.assertIn("only the young woman at center in <Picture 1>", definitions["<Subject 1>"])
        self.assertIn("only the young woman at center in <Picture 2>", definitions["<Subject 2>"])
        self.assertIn(
            "only <Picture 1> supplies [Shot 1]'s background, environment, lighting, composition, "
            "camera framing, and spatial relationships",
            preview["document"]["summary"],
        )
        subject_2_retention = next(
            item for item in preview["document"]["retention_analysis"]
            if item["label"] == "<Subject 2>"
        )
        self.assertIn("background, environment, lighting, composition", subject_2_retention["detail"])
        compiled = preview["compiled_prompt"]
        detailed = compiled.split("detailed_description:\n", 1)[1].split("\n\noverall_soundscape:", 1)[0]
        self.assertIn(
            "<Picture 2> supplies only <Subject 2>'s identity and appearance",
            detailed,
        )
        self.assertIn("<Subject 1> (S1) says", compiled)
        self.assertIn("<Subject 2> walks into the frame from the left", compiled)
        self.assertIn("<Subject 2> (S2) says: <d>[English] General kenobi</d>", compiled)
        self.assertNotIn("Grounded reference appearance", compiled)
        self.assertNotIn("red dress", detailed)
        self.assertNotIn("long brown hair", detailed)
        self.assertNotIn("grey patterned top", detailed)

    def test_private_visual_identifier_leak_is_repaired_without_retry(self):
        value = director_document()
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "people.png",
            "name": "People", "roles": ["subject"],
            "subject_candidates": [
                {
                    "name": "person", "location": "left",
                    "visual_selectors": ["person in black", "black outfit"],
                },
                {
                    "name": "person", "location": "right",
                    "visual_selectors": ["person in white", "white outfit"],
                },
            ],
        }]
        document = normalize_document(value)
        leaked = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Seat the subject","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"steps":[{"type":"action","text":"The person in black sits down."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=leaked) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "messages": [{"role": "user", "content": "Person in black sits down."}],
            })

        self.assertEqual(generate.call_count, 1)
        preview = preview_changeset(document, result["proposal"])
        self.assertIn("<Subject 1> sits down", preview["compiled_prompt"])
        self.assertNotIn("person in black", preview["compiled_prompt"].casefold())

    def test_explicit_subject_attribute_request_preserves_attribute_prose(self):
        value = director_document()
        value["shots"][0]["steps"] = [
            step for step in value["shots"][0]["steps"] if step["type"] == "action"
        ]
        value["references"] = [{
            "id": "reference-1", "kind": "image", "path": "person.png",
            "name": "Person", "roles": ["subject"],
            "subject_candidates": [{
                "name": "person", "location": "",
                "visual_selectors": ["blonde hair"],
                "grounded_attributes": {
                    "hair": "long blonde hair",
                    "clothing": "red jacket",
                },
            }],
        }]
        document = normalize_document(value)
        response = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Preserve the requested hair","operations":[{"op":"update_shot",'
            '"shot_id":"shot-1","fields":{"steps":['
            '{"type":"action","text":"<Subject 1> keeps her blonde hair as she sits."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", return_value=response) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "messages": [{"role": "user", "content": "Keep her blonde hair as she sits."}],
            })

        context = provider_context(generate.call_args.args[1])
        self.assertEqual(context["explicit_subject_attributes"], [{
            "token": "<Subject 1>",
            "attributes": {"hair": "long blonde hair"},
        }])
        preview = preview_changeset(document, result["proposal"])
        self.assertIn("<Subject 1> keeps her blonde hair", preview["compiled_prompt"])

    def test_complete_project_gets_second_correction_for_an_incomplete_first_correction(self):
        document = normalize_document(director_document())
        document["shots"][0]["steps"] = [
            step for step in document["shots"][0]["steps"] if step["type"] == "action"
        ]
        document["shots"][1]["steps"] = []
        incomplete = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Only the opening","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"steps":[{"type":"action","text":"She unfolds the letter."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        complete = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Complete both shots","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"composition":"Medium shot","subjects":"A woman","environment":"Train carriage",'
            '"lighting":"Cool window light","steps":['
            '{"type":"action","text":"She unfolds the letter."}]}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"start":4.5,"composition":"Close-up",'
            '"subjects":"The letter","environment":"Train carriage","lighting":"Cool window light",'
            '"steps":[{"type":"action","text":"The letter text fills the frame."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch(
            "video.director.generate_chat",
            side_effect=["Advice without a proposal.", incomplete, complete],
        ) as generate:
            result = director_chat({
                "scope": "project",
                "document": document,
                "messages": [{
                    "role": "user",
                    "content": "Create the complete two-shot video with Shot 2 cutting at exactly 4.5 seconds.",
                }],
            })

        self.assertEqual(generate.call_count, 3)
        self.assertFalse(result["proposal_error"])
        self.assertEqual(result["proposal"]["base_document_hash"], document_fingerprint(document))
        preview = preview_changeset(document, result["proposal"])["document"]
        self.assertEqual(preview["shots"][1]["start"], 4.5)
        self.assertTrue(action_text(preview["shots"][1]))

    def test_complete_project_rejects_explicit_continuity_breaks(self):
        document = normalize_document({
            "version": 1,
            "duration_seconds": 8,
            "shots": [
                {
                    "id": "shot-1", "start": 0, "composition": "Medium shot", "subjects": "A cook",
                    "environment": "Kitchen", "lighting": "Window light",
                    "action": "She holds a mug in her right hand, filled to 80%.", "sounds": ["Room tone"],
                },
                {
                    "id": "shot-2", "start": 4, "composition": "Close-up", "subjects": "The mug",
                    "environment": "Kitchen", "lighting": "Window light",
                    "action": "The mug is now 50% full in her left hand.", "sounds": ["Ceramic clink"],
                },
            ],
        })
        data = {
            "messages": [{
                "role": "user",
                "content": (
                    "Refine the complete video: keep the mug in her right hand across cuts, keep its fill level "
                    "consistent, and add synchronized sounds."
                ),
            }],
        }
        with self.assertRaisesRegex(ValueError, "fill level|right-hand"):
            _validate_requested_project_result(document, data)

    def test_complete_project_requires_requested_screen_direction_across_cuts(self):
        document = normalize_document(director_document())
        for index, shot in enumerate(document["shots"], 1):
            shot.update({
                "composition": f"Medium shot {index}", "subjects": "The same woman",
                "environment": "Train carriage", "lighting": "Cool window light",
            })
        document["shots"][0]["steps"][0]["text"] = (
            "She walks screen-left to screen-right through the carriage."
        )
        document["shots"][1]["steps"][0]["text"] = "She continues walking toward the door."
        data = {"messages": [{"role": "user", "content": (
            "Completely refine both shots. She moves screen-left to screen-right; preserve screen "
            "direction throughout and do not reverse it across the cut."
        )}]}
        with self.assertRaisesRegex(ValueError, "screen-left to screen-right"):
            _validate_requested_project_result(document, data)
        document["shots"][1]["steps"][0]["text"] = (
            "She continues screen-left to screen-right toward the door."
        )
        _validate_requested_project_result(document, data)

    def test_complete_project_requires_explicit_shot_start_list(self):
        document = normalize_document({
            "version": 1,
            "duration_seconds": 9,
            "shots": [
                {"id": "shot-1", "start": 0, "composition": "Wide shot", "subjects": "A baker",
                 "environment": "Bakery", "lighting": "Night light",
                 "steps": [{"type": "action", "text": "She opens the shutters."}]},
                {"id": "shot-2", "start": 4, "composition": "Medium shot", "subjects": "The baker",
                 "environment": "Bakery", "lighting": "Night light",
                 "steps": [{"type": "action", "text": "She finds a parcel."}]},
                {"id": "shot-3", "start": 6, "composition": "Close-up", "subjects": "The baker",
                 "environment": "Bakery", "lighting": "Night light",
                 "steps": [{"type": "action", "text": "She reads its note."}]},
            ],
        })
        request = {"messages": [{
            "role": "user",
            "content": "Completely rewrite this as exactly three resulting shots at 0, 3, and 6 seconds.",
        }]}
        with self.assertRaisesRegex(ValueError, "requested shot start times"):
            _validate_requested_project_result(document, request)
        document["shots"][1]["start"] = 3
        _validate_requested_project_result(document, request)

    def test_invalid_project_timeline_is_corrected_once(self):
        invalid_response = (
            "Here is the complete structured video document populated for MiniMax H3.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the closing shot","operations":['
            '{"op":"add_shot","shot":{"id":"shot-closing","start":4.0,'
            '"steps":[{"type":"action","text":"The letter rests on the empty seat."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        corrected_response = (
            "I corrected the closing cut so it has a unique time.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the closing shot","operations":['
            '{"op":"add_shot","shot":{"id":"shot-closing","start":6.0,'
            '"steps":[{"type":"action","text":"The letter rests on the empty seat."}]}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch(
            "video.director.generate_chat",
            side_effect=[invalid_response, corrected_response],
        ) as generate:
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "messages": [{"role": "user", "content": "Add the closing shot."}],
            })

        self.assertEqual(generate.call_count, 2)
        self.assertFalse(result["proposal_error"])
        self.assertEqual(result["proposal"]["operations"][0]["shot"]["start"], 6.0)
        retry_messages = generate.call_args.args[1]
        self.assertIn("strictly increasing", retry_messages[-1]["content"])
        self.assertNotIn(invalid_response, retry_messages[-2]["content"])
        self.assertIn("Invalid structured draft to repair", retry_messages[-2]["content"])

    def test_malformed_json_retry_accepts_vague_camera_qualifiers(self):
        malformed_response = (
            "Here is the complete structured video document.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose two shots","operations":[{"op":"update_shot",}]}'
            f"\n{CHANGESET_END}"
        )
        corrected_response = (
            "Here is the corrected two-shot sequence.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose two shots","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"start":4.5,"camera":{"type":"Push In",'
            '"amplitude":"gentle emphasis","speed":"smoothly","target":"the text reveal"}}}]}'
            f"\n{CHANGESET_END}"
        )
        with patch("video.director.generate_chat", side_effect=[malformed_response, corrected_response]) as generate:
            result = director_chat({
                "scope": "project",
                "document": director_document(),
                "messages": [{"role": "user", "content": "Create a two-shot sequence with a cut at 4.5 seconds."}],
            })

        self.assertEqual(generate.call_count, 2)
        self.assertFalse(result["proposal_error"])
        camera = result["proposal"]["operations"][0]["fields"]["camera"]
        self.assertEqual(camera["amplitude"], "default")
        self.assertEqual(camera["speed"], "default")


if __name__ == "__main__":
    unittest.main()
