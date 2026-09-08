"""Deterministic authority regressions; routed examples are not live LLM evidence."""

import copy
import json
import unittest
from unittest.mock import patch

from video.contracts import normalize_document
from video.director import (
    _apply_complete_rewrite_semantics,
    _classify_director_turn,
    _complete_rewrite_requested,
    _current_pending_plan,
    _normalize_edit_intent,
    _protected_content_change_requested,
    _validate_protected_sequence_content,
    _validated_edit_intent,
    director_chat,
    document_fingerprint,
    preview_changeset,
)
from test_director import director_document, edit_request


class DirectorEditAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.document = director_document()
        self.document["shots"][0]["steps"][1]["text"] = "Stay here."
        self.document = normalize_document(self.document)

    def proposal(self, fields=None, operations=None):
        return {
            "base_document_hash": document_fingerprint(self.document),
            "scope": {"type": "project"}, "summary": "Review requested edit",
            "operations": operations if operations is not None else [{
                "op": "update_shot", "shot_id": "shot-1", "fields": fields,
            }],
        }

    def test_audit_synthetic_requests_do_not_grant_authority_from_mentions(self):
        for content in (
            "Do not rewrite the entire scene; only change the lighting.",
            'The sign reads "Rewrite the entire scene". Only fix lighting.',
            "Neprepisuj celú scénu. Zmeň iba osvetlenie.",
        ):
            with self.subTest(content=content):
                self.assertFalse(_complete_rewrite_requested({
                    "messages": [{"role": "user", "content": content}],
                }))
        self.assertFalse(_protected_content_change_requested({
            "messages": [{"role": "user", "content": "Translate the camera description; preserve dialogue."}],
        }, "dialogue"))

    def test_full_rewrite_preserves_audit_spoken_line_and_visible_text(self):
        request = edit_request(
            "Completely rewrite the entire scene but keep every spoken line exactly.",
            replacement=True,
        )
        result = copy.deepcopy(self.document)
        result["shots"][0]["steps"] = []
        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            _validate_protected_sequence_content(self.document, result, request)
        result = copy.deepcopy(self.document)
        result["shots"][0]["visible_text"] = []
        with self.assertRaisesRegex(ValueError, "protected visible text"):
            _validate_protected_sequence_content(self.document, result, request)

    def test_explicit_preservation_vetoes_conflicting_router_proposals(self):
        request = edit_request(
            "Do not rewrite the entire scene. Translate the camera description; preserve dialogue.",
            replacement=True, changes=("dialogue",),
        )
        request["_turn_intent"]["edit_intent"]["preserve"] = ["replacement", "dialogue"]
        self.assertFalse(_complete_rewrite_requested(request))
        self.assertFalse(_protected_content_change_requested(request, "dialogue"))

    def test_quoted_instruction_evidence_is_not_authority_in_multiple_languages(self):
        for quoted in ('"Rewrite the entire scene"', '“Prepíš celú scénu”', '„Prepíš celú scénu“'):
            request = edit_request("The sign says " + quoted + ". Fix the lighting.", replacement=True)
            request["_turn_intent"]["edit_intent"]["replacement_evidence"] = quoted[1:-1]
            self.assertFalse(_complete_rewrite_requested(request))

    def test_multisentence_slovak_intent_preserves_separate_content_kinds(self):
        content = "Prepíš celú scénu. Zachovaj každú repliku, text piesne, označenia hovoriacich a nápisy."
        request = edit_request(content, replacement=True)
        request["_turn_intent"]["edit_intent"]["preserve"] = ["dialogue", "lyrics", "speaker_ids", "visible_text"]
        self.assertTrue(_complete_rewrite_requested(request))
        for kind in ("dialogue", "lyrics", "speaker_ids", "visible_text"):
            self.assertFalse(_protected_content_change_requested(request, kind))

    def test_slovak_specific_translation_can_be_authorized_without_english_regex(self):
        request = edit_request("Prelož dialóg do slovenčiny.", changes=("dialogue",))
        fields = {"steps": copy.deepcopy(self.document["shots"][0]["steps"])}
        fields["steps"][1].update(text="Zostaň tu.", language="Slovak")
        result = preview_changeset(self.document, self.proposal(fields), request_data=request)
        self.assertEqual(result["document"]["shots"][0]["steps"][1]["text"], "Zostaň tu.")
        self.assertEqual(result["protected_content_changes"][0]["kind"], "dialogue")
        applied = preview_changeset(self.document, result["proposal"])
        self.assertEqual(result["document"], applied["document"])

    def test_permissions_are_separate_for_dialogue_lyrics_and_speaker_ids(self):
        request = edit_request("Translate the dialogue.", changes=("dialogue",))
        result = copy.deepcopy(self.document)
        result["shots"][0]["steps"][1]["speaker_id"] = "S2"
        with self.assertRaisesRegex(ValueError, "speaker IDs"):
            _validate_protected_sequence_content(self.document, result, request)
        self.document["shots"][0]["steps"].append({
            "type": "dialogue", "performance": "singing", "speaker_id": "S1",
            "language": "English", "text": "Keep this lyric!",
        })
        result = copy.deepcopy(self.document)
        result["shots"][0]["steps"][-1]["text"] = "A different lyric."
        with self.assertRaisesRegex(ValueError, "protected lyrics"):
            _validate_protected_sequence_content(self.document, result, request)

    def test_speaker_authority_does_not_authorize_dialogue_rewrite(self):
        request = edit_request("Change the speaker ID to S2.", changes=("speaker_ids",))
        result = copy.deepcopy(self.document)
        result["shots"][0]["steps"][1]["speaker_id"] = "S2"
        _validate_protected_sequence_content(self.document, result, request)
        result["shots"][0]["steps"][1]["text"] = "Changed words."
        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            _validate_protected_sequence_content(self.document, result, request)

    def test_explicit_dialogue_removal_also_removes_its_id_occurrence(self):
        request = edit_request("Remove the dialogue.", changes=("dialogue",))
        result = copy.deepcopy(self.document)
        result["shots"][0]["steps"] = result["shots"][0]["steps"][:1]
        _validate_protected_sequence_content(self.document, result, request)

    def test_shot_rewrite_inside_project_does_not_replace_project_fields(self):
        request = edit_request("Rewrite Shot 1 completely.", replacement=True)
        request["_turn_intent"]["edit_intent"]["scope"] = "shot"
        proposal = self.proposal({"steps": copy.deepcopy(self.document["shots"][0]["steps"])})
        replacement = _apply_complete_rewrite_semantics(self.document, proposal, request)
        self.assertEqual(len(replacement["operations"]), 1)
        self.assertTrue(replacement["operations"][0]["replace"])
        proposal["operations"].append({"op": "update_project", "replace": True, "fields": {"style": "New"}})
        with self.assertRaisesRegex(ValueError, "cannot replace project"):
            _apply_complete_rewrite_semantics(self.document, proposal, request)

    def test_removed_shot_and_replace_flag_cannot_bypass_protection(self):
        proposal = self.proposal(operations=[{"op": "remove_shot", "shot_id": "shot-1", "replace": True}])
        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            preview_changeset(self.document, proposal)

    def test_replacement_can_move_protected_content_to_new_timeline(self):
        replacement = {"id": "replacement-shot", "start": 0,
                       "steps": copy.deepcopy(self.document["shots"][0]["steps"]),
                       "visible_text": copy.deepcopy(self.document["shots"][0]["visible_text"])}
        proposal = self.proposal(operations=[
            {"op": "remove_shot", "shot_id": "shot-1", "replace": True},
            {"op": "add_shot", "shot": replacement},
        ])
        result = preview_changeset(self.document, proposal)
        self.assertIn("Stay here.", result["compiled_prompt"])
        self.assertEqual(result["protected_content_changes"], [])

    def test_duplicate_literals_need_the_same_number_of_occurrences(self):
        self.document["shots"][1]["steps"].append(copy.deepcopy(self.document["shots"][0]["steps"][1]))
        self.document["shots"][1]["visible_text"] = ["Central Station"]
        proposal = self.proposal(operations=[{"op": "remove_shot", "shot_id": "shot-1", "replace": True}])
        with self.assertRaisesRegex(ValueError, "protected dialogue"):
            preview_changeset(self.document, proposal)

    def test_evidence_must_be_from_the_current_user_turn(self):
        request = edit_request("Only change the lighting.", replacement=True, changes=("dialogue",))
        intent = request["_turn_intent"]["edit_intent"]
        intent["replacement_evidence"] = "Rewrite the entire scene."
        intent["protected_changes"]["dialogue"] = "Translate the dialogue."
        request["messages"].insert(0, {"role": "assistant", "content": "Translate the dialogue. Rewrite the entire scene."})
        self.assertFalse(_complete_rewrite_requested(request))
        self.assertFalse(_protected_content_change_requested(request, "dialogue"))

    def pending(self):
        return {
            "document_hash": document_fingerprint(self.document), "scope": "project",
            "clarification_id": "choose-reference", "original_request": "Rewrite the entire production, preserving dialogue.",
            "turn_intent": {"edit_intent": {
                "scope": "project", "replacement": "replace", "replacement_evidence": "Rewrite the entire production",
                "protected_changes": {}, "preserve": ["dialogue"],
            }},
        }

    def test_followup_is_reclassified_and_preserves_pending_exclusions(self):
        request = edit_request("Yes, use the second reference.", replacement=True, changes=("dialogue",))
        request.update(document=self.document, pending_plan=self.pending())
        routed = {**request["_turn_intent"], "reference_only": False, "resolved_instruction": "", "reason": "Confirmed plan."}
        with patch("video.director.generate_chat", return_value=json.dumps(routed)) as generate:
            turn = _classify_director_turn(request)
        self.assertEqual(generate.call_count, 1)
        self.assertIsNotNone(json.loads(generate.call_args.args[1][1]["content"])["pending_plan"])
        request["_turn_intent"] = turn
        self.assertTrue(_complete_rewrite_requested(request))
        self.assertFalse(_protected_content_change_requested(request, "dialogue"))

    def test_stale_pending_plan_and_changed_selected_shot_are_discarded(self):
        request = {"scope": "project", "document": self.document, "pending_plan": self.pending()}
        request["document"] = copy.deepcopy(self.document)
        request["document"]["style"] = "Changed after clarification"
        self.assertIsNone(_current_pending_plan(request))
        request.update(scope="shot", selected_shot_id="shot-2", document=self.document)
        request["pending_plan"].update(scope="shot", selected_shot_id="shot-1")
        self.assertIsNone(_current_pending_plan(request))

    def test_stale_followup_cannot_force_mutation_without_semantic_routing(self):
        request = {"scope": "project", "document": self.document, "pending_plan": self.pending(),
                   "messages": [{"role": "user", "content": "Yes."}]}
        request["pending_plan"]["document_hash"] = "stale"
        routed = {"route": "clarify", "confidence": 1, "reason": "Stale confirmation", "edit_intent": {}}
        with patch("video.director.generate_chat", return_value=json.dumps(routed)) as generate:
            result = _classify_director_turn(request)
        self.assertEqual(result["route"], "clarify")
        self.assertIsNone(json.loads(generate.call_args.args[1][1]["content"])["pending_plan"])

    def test_apply_checks_current_document_and_exact_authorized_proposal(self):
        request = edit_request("Change visible text to NEW.", changes=("visible_text",))
        approved = preview_changeset(self.document, self.proposal({"visible_text": ["NEW"]}), request_data=request)["proposal"]
        changed = copy.deepcopy(approved)
        changed["operations"][0]["fields"]["visible_text"] = ["TAMPERED"]
        with self.assertRaisesRegex(ValueError, "authority changed or expired"):
            preview_changeset(self.document, changed)
        changed_document = copy.deepcopy(self.document)
        changed_document["style"] = "New style"
        with self.assertRaisesRegex(ValueError, "changed after this proposal"):
            preview_changeset(changed_document, approved)
        with patch("video.director._EDIT_AUTHORITY_KEY", b"restarted process"):
            with self.assertRaisesRegex(ValueError, "authority changed or expired"):
                preview_changeset(self.document, approved)

    def test_unsigned_caller_permissions_and_replace_cannot_authorize_visible_text_loss(self):
        proposal = self.proposal({"visible_text": []})
        proposal["protected_changes"] = {"visible_text": True}
        with self.assertRaisesRegex(ValueError, "protected visible text"):
            preview_changeset(self.document, proposal)

    def test_low_confidence_and_invalid_permission_types_fail_closed(self):
        request = edit_request("Rewrite the scene.", replacement=True, changes=("dialogue",))
        for confidence in (0.4, float("nan"), "invalid"):
            request["_turn_intent"]["confidence"] = confidence
            self.assertEqual(_validated_edit_intent(request)["replacement"], "patch")
        for value in ({"protected_changes": {"dialogue": True}}, {"preserve": [False]}, {"scope": []}):
            with self.assertRaises(ValueError):
                _normalize_edit_intent(value)

    def test_negated_replacement_rejects_model_replace_flag(self):
        request = edit_request("Do not rewrite the scene; only change lighting.")
        proposal = self.proposal({"lighting": "Warm light"})
        proposal["operations"][0]["replace"] = True
        with self.assertRaisesRegex(ValueError, "replace true is allowed only"):
            _apply_complete_rewrite_semantics(self.document, proposal, request)

    def test_director_proposal_is_reviewable_and_does_not_mutate_input(self):
        request = edit_request("Change visible text to NEW.", changes=("visible_text",))
        request["document"] = copy.deepcopy(self.document)
        request["_turn_intent"].update(reference_only=False, resolved_instruction="")
        response = {"message": "Review the sign change.", "proposal": {
            "summary": "Change the sign", "operations": self.proposal({"visible_text": ["NEW"]})["operations"],
        }}
        with patch("video.director._classify_director_turn", return_value=request["_turn_intent"]), patch(
            "video.director.generate_chat", return_value=json.dumps(response)
        ):
            result = director_chat(request)
        self.assertFalse(result["proposal_error"])
        self.assertIsNotNone(result["proposal"])
        self.assertEqual(request["document"], self.document)
        applied = preview_changeset(self.document, result["proposal"])
        self.assertEqual(applied["document"]["shots"][0]["visible_text"], ["NEW"])


if __name__ == "__main__":
    unittest.main()
