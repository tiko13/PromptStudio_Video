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
    _validate_reference_only_preservation,
    _validate_requested_project_result,
    _enrich_reference_definition_placeholders,
    _parse_vision_grounding,
    _preserve_reference_only_request,
    _proposal_retry_messages,
    _proposal_requested,
)


def director_document():
    return {
        "version": 1,
        "mode": "auto",
        "duration_seconds": 8,
        "style": "Live-action, cinematic",
        "shots": [
            {
                "id": "shot-1",
                "start": 0,
                "action": "The woman unfolds a letter.",
                "dialogue": [{
                    "speaker": "The woman",
                    "speaker_id": "S1",
                    "language": "English",
                    "text": "Do not rewrite this.",
                }],
                "visible_text": ["Central Station"],
            },
            {"id": "shot-2", "start": 4, "action": "She looks toward the window."},
        ],
        "references": [],
    }


def provider_context(messages):
    marker = "Current production context (reference data):\n"
    return json.loads(messages[0]["content"].split(marker, 1)[1])


class DirectorTests(unittest.TestCase):
    def test_reference_contract_has_no_literal_appearance_example(self):
        for system_message in (SHOT_SYSTEM_MESSAGE, PROJECT_SYSTEM_MESSAGE):
            self.assertNotIn("with the observed identity traits.", system_message)
            self.assertNotIn("concrete visible identity traits observed in that image", system_message)
            self.assertNotIn("shoulder-length wavy black hair", system_message)
            self.assertNotIn("teal linen blouse", system_message)
            self.assertIn("observed_visual_facts", system_message)

    def test_vision_grounding_contract_is_reference_role_focused(self):
        self.assertIn("Follow assigned_usage strictly", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("For scene usage", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("omitting a visible person's identity and wardrobe", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("For style usage", VISION_GROUNDING_SYSTEM_MESSAGE)

    def test_vision_grounding_contract_separates_pixels_from_requested_action(self):
        self.assertIn("report only concrete facts visible in their pixels", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("do not add the user's requested action", VISION_GROUNDING_SYSTEM_MESSAGE)
        self.assertIn("Return JSON only", VISION_GROUNDING_SYSTEM_MESSAGE)

    def test_vision_grounding_parser_preserves_attachment_order(self):
        attachments = [
            {"id": "a", "name": "First.webp", "usage": "subject"},
            {"id": "b", "name": "Second.png", "usage": "scene"},
        ]
        parsed = _parse_vision_grounding(
            '{"images":[{"index":2,"observations":"Blue tiled room."},'
            '{"index":1,"observations":"Platinum-blonde hair and pink clothing."}]}',
            attachments,
        )
        self.assertEqual([item["attachment_id"] for item in parsed], ["a", "b"])
        self.assertIn("Platinum-blonde", parsed[0]["observations"])
        self.assertIn("Blue tiled room", parsed[1]["observations"])

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
        self.assertIn("observed_visual_facts", correction)
        self.assertIn("Copy only those grounded facts", correction)
        self.assertIn("do not invent or substitute appearance details", correction)

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
            '{"op":"update_shot","shot_id":"shot-1","fields":{"action":"She opens a red umbrella."}}]}'
            f"\n{CHANGESET_END}"
        )

        parsed = parse_director_response(raw, "", document_fingerprint(document), "project")
        preview = preview_changeset(document, parsed["proposal"])

        self.assertFalse(parsed["proposal_error"])
        self.assertEqual(preview["document"]["task_types"], [])
        self.assertEqual(preview["document"]["shots"][0]["action"], "She opens a red umbrella.")

    def test_parser_repairs_a_missing_operation_closing_brace(self):
        document = normalize_document(director_document())
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Repair structure","operations":['
            '{"op":"update_project","fields":{"style":"Cinematic"},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"action":"She turns."}}]}'
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
        changed["shots"][0]["action"] = "She poses without moving."
        request = {
            "attachments": [{"path": "woman.png", "usage": "subject"}],
            "messages": [{"role": "user", "content": "Make this the identity reference."}],
        }
        with self.assertRaisesRegex(ValueError, "preserve shot-1 action verbatim"):
            _validate_reference_only_preservation(original, changed, request)
        changed["shots"][0]["action"] = original["shots"][0]["action"] + " <Subject 1> keeps the same identity."
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

    def test_valid_proposal_updates_only_allowed_fields(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            "A slow push-in will make the reaction clearer.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Clarify the reaction","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"action":"She watches the lights pass.",'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow",'
            '"target":"her reflection"}}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "shot-2", fingerprint)
        self.assertFalse(parsed["proposal_error"])
        result = preview_changeset(document, parsed["proposal"])
        updated = result["document"]
        self.assertEqual(updated["shots"][1]["action"], "She watches the lights pass.")
        self.assertEqual(updated["shots"][1]["camera"]["type"], "Push In")
        self.assertEqual(updated["shots"][0]["dialogue"][0]["text"], "Do not rewrite this.")
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
            '"environment":"<Environment1> kitchen","action":"<Picture1> stays upright",'
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
        self.assertEqual(result["document"]["shots"][0]["action"], "red mug stays upright")
        self.assertEqual(result["document"]["non_diegetic_music"], "N/A")
        self.assertEqual(result["document"]["overall_soundscape"], "Wheel hum.")

    def test_project_proposal_can_update_main_description(self):
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
        self.assertIn(result["document"]["main_description"], result["compiled_prompt"])

    def test_structured_camera_motion_is_not_duplicated_in_action(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine reveal","operations":[{"op":"update_shot","shot_id":"shot-2",'
            '"fields":{"action":"She opens the letter. The camera slowly pushes in toward the text. The slow push-in emphasizes the writing.",'
            '"camera":{"type":"Push In","amplitude":"small","speed":"slow",'
            '"target":"her movement towards the text"}}}]} '
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual(result["document"]["shots"][1]["action"], "She opens the letter.")
        self.assertEqual(result["document"]["shots"][1]["camera"]["target"], "the text")
        self.assertEqual(result["compiled_prompt"].count("camera pushes in"), 1)

    def test_add_operations_for_display_ids_update_existing_shots(self):
        document = normalize_document(director_document())
        fingerprint = document_fingerprint(document)
        raw = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Populate both shots","operations":['
            '{"op":"add_shot","shot":{"id":"Shot1","start":0,"action":"Opening action."}},'
            '{"op":"add_shot","shot":{"id":"Shot2","start":4.5,"action":"Reveal action."}}]}'
            f"\n{CHANGESET_END}"
        )
        parsed = parse_director_response(raw, "", fingerprint, "project")
        result = preview_changeset(document, parsed["proposal"])
        self.assertEqual([item["op"] for item in result["proposal"]["operations"]], ["update_shot", "update_shot"])
        self.assertEqual([shot["id"] for shot in result["document"]["shots"]], ["shot-1", "shot-2"])
        self.assertEqual(result["document"]["shots"][1]["start"], 4.5)

    def test_proposal_rejects_protected_fields_and_other_shots(self):
        fingerprint = document_fingerprint(director_document())
        protected = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Rewrite dialogue","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"dialogue":[]}}]}\n'
            f"{CHANGESET_END}"
        )
        parsed = parse_director_response(protected, "shot-2", fingerprint)
        self.assertIsNone(parsed["proposal"])
        self.assertIn("Protected or unsupported", parsed["proposal_error"])

        other = protected.replace('"shot_id":"shot-2"', '"shot_id":"shot-1"').replace('"dialogue":[]', '"action":"Changed"')
        parsed = parse_director_response(other, "shot-2", fingerprint)
        self.assertIsNone(parsed["proposal"])
        self.assertIn("another shot", parsed["proposal_error"])

    def test_stale_proposal_is_rejected(self):
        document = normalize_document(director_document())
        proposal = {
            "base_document_hash": document_fingerprint(document),
            "scope": {"type": "shot", "shot_id": "shot-2"},
            "summary": "Update action",
            "operations": [{"op": "update_shot", "shot_id": "shot-2", "fields": {"action": "New action"}}],
        }
        document["shots"][1]["action"] = "A manual edit happened."
        with self.assertRaisesRegex(ValueError, "changed after this proposal"):
            preview_changeset(document, proposal)

    def test_chat_returns_validated_proposal_and_context_usage(self):
        response = (
            "This keeps the action feasible.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Refine action","operations":[{"op":"update_shot",'
            '"shot_id":"shot-2","fields":{"action":"She turns toward the passing lights."}}]}\n'
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

    def test_grounded_visual_facts_are_authoritative_director_context(self):
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
            }],
        })
        context = provider_context(messages)
        attached = context["attached_images"][0]
        self.assertIn("platinum-blonde", attached["observed_visual_facts"])
        self.assertIn("pink crop top", attached["observed_visual_facts"])
        self.assertIn("Authoritative pixel observations", attached["observation_policy"])

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
            'pink bottoms, black boots, full-body pose against a plain light wall."}]}'
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
        self.assertIn("Platinum-blonde", context["attached_images"][0]["observed_visual_facts"])
        self.assertIn("pink crop top", context["attached_images"][0]["observed_visual_facts"])
        self.assertEqual(result["vision_observations"][0]["usage"], "subject")
        self.assertEqual(progress, [{
            "phase": "vision_grounding",
            "attempt": 1,
            "maximum_attempts": 2,
        }])

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
            '"action":"<Subject 1> looks toward the window while holding the letter."}}]}'
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
            proposal["operations"].append({
                "op": "update_shot", "shot_id": shot_id,
                "fields": {
                    "composition": long_composition, "subjects": long_subjects,
                    "environment": long_environment, "lighting": long_lighting,
                    "action": long_action, "sounds": ["Fabric shifts softly and the paper opens with a crisp rustle."],
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
        self.assertIn("<Subject 1> is the blonde woman in <Picture 1>", prompt)
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
            '{"op":"update_shot","shot_id":"shot-2","fields":{"start":3.0,"action":"She studies her reflection."}},'
            '{"op":"add_shot","shot":{"id":"shot-farewell","start":6.0,"transition":"the camera cuts to",'
            '"composition":"Close-up of the folded letter.","action":"Her hand leaves the letter on the seat.",'
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
        self.assertEqual(result["document"]["shots"][0]["dialogue"][0]["text"], "Do not rewrite this.")

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

    def test_complete_rewrite_can_replace_every_unprotected_shot(self):
        value = director_document()
        for item in value["shots"]:
            item["dialogue"] = []
            item["visible_text"] = []
        document = normalize_document(value)
        replacement = {
            "id": "replacement-shot", "start": 0,
            "composition": "A bakery counter fills the frame.",
            "subjects": "A baker stands behind the counter.",
            "environment": "A small bakery before sunrise.",
            "lighting": "Warm practical light meets cool window light.",
            "action": "The baker opens the shutters.",
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
            item["dialogue"] = []
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
            '"action":"She watches the city lights recede."}}]}'
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
        self.assertTrue(all(
            call.args[0]["thinking_mode"] == "High" for call in generate.call_args_list
        ))
        retry_messages = generate.call_args.args[1]
        self.assertIn("machine-applicable structured proposal", retry_messages[-2]["content"])
        self.assertIn(CHANGESET_BEGIN, retry_messages[-1]["content"])
        self.assertEqual(progress, [{
            "phase": "proposal_correction",
            "attempt": 1,
            "maximum_attempts": 3,
        }])

    def test_explicit_shot_count_retries_structurally_incomplete_proposal(self):
        document = normalize_document(director_document())
        document["shots"] = document["shots"][:1]
        first_response = (
            "I composed the requested progression.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose the progression","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"action":"The woman stands and reveals the note."}}]}'
            f"\n{CHANGESET_END}"
        )
        corrected_response = (
            "I separated the action and reveal into two shots.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Compose two distinct shots","operations":['
            '{"op":"update_shot","shot_id":"shot-1","fields":'
            '{"action":"The woman rises from the red armchair."}},'
            '{"op":"add_shot","shot":{"id":"shot-note-reveal","start":4.0,'
            '"transition":"the camera cuts to","composition":"A close-up frames the note.",'
            '"subjects":"The woman holds the unfolded note toward the camera.",'
            '"environment":"The same room remains softly visible behind her.",'
            '"lighting":"Soft window light keeps the handwriting legible.",'
            '"action":"She reveals the handwritten message.",'
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
        self.assertIn("required structured proposal", result["proposal_error"])

    def test_complete_project_gets_second_correction_for_an_incomplete_first_correction(self):
        document = normalize_document(director_document())
        document["shots"][1]["action"] = ""
        incomplete = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Only the opening","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"action":"She unfolds the letter."}}]}'
            f"\n{CHANGESET_END}"
        )
        complete = (
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Complete both shots","operations":[{"op":"update_shot","shot_id":"shot-1",'
            '"fields":{"composition":"Medium shot","subjects":"A woman","environment":"Train carriage",'
            '"lighting":"Cool window light","action":"She unfolds the letter."}},'
            '{"op":"update_shot","shot_id":"shot-2","fields":{"start":4.5,"composition":"Close-up",'
            '"subjects":"The letter","environment":"Train carriage","lighting":"Cool window light",'
            '"action":"The letter text fills the frame."}}]}'
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
        self.assertTrue(preview["shots"][1]["action"])

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

    def test_invalid_project_timeline_is_corrected_once(self):
        invalid_response = (
            "Here is the complete structured video document populated for MiniMax H3.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the closing shot","operations":['
            '{"op":"add_shot","shot":{"id":"shot-closing","start":4.0,'
            '"action":"The letter rests on the empty seat."}}]}'
            f"\n{CHANGESET_END}"
        )
        corrected_response = (
            "I corrected the closing cut so it has a unique time.\n"
            f"{CHANGESET_BEGIN}\n"
            '{"summary":"Add the closing shot","operations":['
            '{"op":"add_shot","shot":{"id":"shot-closing","start":6.0,'
            '"action":"The letter rests on the empty seat."}}]}'
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
        self.assertIn("machine-applicable structured proposal", retry_messages[-2]["content"])

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
