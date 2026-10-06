"""Offline policy equivalence and adversarial validation; no model-quality claim."""

import copy
import json
import unittest
from unittest.mock import patch

from video.contracts import normalize_document
from video import director
from video.director import (
    _normalize_director_proposal, _proposal_retry_messages, _validate_parsed_proposal,
    build_provider_messages, director_chat, document_fingerprint, preview_changeset,
)
from video.director_policy import (
    POLICY_VERSION, PolicyModule, ProposalIssueError, compose_policy, measure_messages,
    measurement_report, measurement_requests, validate_stage, validation_issue,
    evaluate_requests,
)
from test_director import director_document, edit_request


# Updated for the continuous-action shot planning policy; composition below
# still verifies all policy sections against the canonical instructions.
# Stable T2VA cases: small/large, shot/project.
BASELINE_HASHES = (
    "077f9d0e4f2f6f78b494d2eef9257d0861e0483d4cde67910f27f42354cafef6",
    "1a8a680e2bda3b8a53c17fdc40e13fda83a294dd2d6c66d68261227ec764dd68",
    "fa45e6da2ab119527b0ae5e50b9d98324162a9d6bf1a35bd4c030fd843477e24",
    "e25075df4c9db5f212b90f4684dd77eb51b23730330dab4d92b7dbe141d29e55",
)


def proposal_for(document, fields, scope="project"):
    return {
        "base_document_hash": document_fingerprint(document),
        "scope": {"type": scope, **({"shot_id": "shot-1"} if scope == "shot" else {})},
        "summary": "Change only requested fields",
        "operations": [{"op": "update_shot", "shot_id": "shot-1", "fields": fields}],
    }


class DirectorPolicyTests(unittest.TestCase):
    def setUp(self):
        # Isolate existing author policy/correction tests from the extra model stages.
        planner = patch("video.director._create_director_plan", return_value=None)
        planner.start()
        self.addCleanup(planner.stop)

    def test_initial_prompt_bytes_match_twenty_baseline_cases(self):
        rows = measurement_report()
        # Original reference fixtures generated random IDs, so only the four
        # reference-free baseline message hashes are stable. For all modes,
        # compare the exact original composition algorithm below.
        self.assertEqual(tuple(row["messages_sha256"] for row in rows[:4]), BASELINE_HASHES[:4])
        for name, request in measurement_requests():
            document = normalize_document(request["document"])
            legacy = director.PROJECT_SYSTEM_MESSAGE if request["scope"] == "project" else director.SHOT_SYSTEM_MESSAGE
            if document["resolved_mode"] in {"i2va", "fl2va", "l2va"}:
                legacy += "\n\n" + director.BASE_KEYFRAME_DIRECTOR_POLICY
            if director._has_first_frame_anchor(document):
                legacy += "\n\n" + director.I2VA_DIRECTOR_POLICY
            for guide_name, guide in director._prompt_writing_guides(document["resolved_mode"]):
                legacy += (f"\n\nBEGIN AUTHORITATIVE MINIMAX H3 {guide_name.upper()} VIDEO PROMPT WRITING GUIDE\n\n"
                           + guide + f"\nEND AUTHORITATIVE MINIMAX H3 {guide_name.upper()} VIDEO PROMPT WRITING GUIDE")
            messages, _ = build_provider_messages(request)
            self.assertEqual(messages[0]["content"], legacy, name)
        self.assertTrue(all(row["policy_version"] == POLICY_VERSION for row in rows))
        self.assertTrue(all(row["prompt_tokens"] is None and row["first_pass_validity"] is None for row in rows))

    def test_only_applicable_named_policies_and_guides_are_composed(self):
        for name, request in measurement_requests():
            _, usage = build_provider_messages(request)
            names = [item["name"] for item in usage["policy_modules"]]
            mode = name.split("/")[0]
            self.assertEqual(len(names), len(set(names)))
            self.assertIn("temporal_synchronization", names)
            self.assertEqual("first_frame_lock" in names, mode in {"i2va", "fl2va"})
            self.assertEqual("guide.reference" in names, mode == "ref2va")
            self.assertEqual("guide.base-shared" in names, mode == "ref2va")
        with self.assertRaises(ValueError):
            compose_policy([PolicyModule("same", "a", "structure"), PolicyModule("same", "b", "structure")])

    def test_normalization_is_idempotent_across_modes_scopes_and_sizes(self):
        for name, request in measurement_requests():
            with self.subTest(case=name):
                document = normalize_document(request["document"])
                if document["resolved_mode"] == "ref2va":
                    document["references"][0]["subject_candidates"] = [{"name": "courier", "location": "", "attributes": ""}]
                proposal = proposal_for(document, {"camera": {
                    "type": "Push In", "speed": "slow", "amplitude": "small", "target": "the courier",
                }}, request["scope"])
                original = copy.deepcopy((document, proposal, request))
                once = _normalize_director_proposal(document, proposal, request)
                twice = _normalize_director_proposal(document, once, request)
                self.assertEqual(once, twice)
                self.assertEqual((document, proposal, request), original)

    def test_literals_and_reference_bindings_survive_repeated_preview(self):
        document = director_document()
        document["shots"][0]["steps"][1]["text"] = "Nemeň text… Zostaň tu!"
        document["shots"][0]["visible_text"] = ["Zostaň tu. 9:00"]
        document = normalize_document(document)
        request = edit_request("Only change the lighting.")
        proposal = proposal_for(document, {"lighting": "Soft window light."})
        once = _normalize_director_proposal(document, proposal, request)
        self.assertEqual(once, _normalize_director_proposal(document, once, request))
        preview = preview_changeset(document, once, request_data=request)
        repeated = preview_changeset(document, preview["proposal"], request_data=request)
        self.assertEqual(preview, repeated)
        self.assertEqual(preview["document"]["shots"][0]["steps"], document["shots"][0]["steps"])
        self.assertEqual(preview["document"]["shots"][0]["visible_text"], document["shots"][0]["visible_text"])

    def test_reference_normalization_and_compilation_are_repeatable(self):
        _, request = next((name, request) for name, request in measurement_requests() if name == "ref2va/small/project")
        document = normalize_document(request["document"])
        document["references"][0]["subject_candidates"] = [{"name": "courier", "location": "", "attributes": ""}]
        proposal = proposal_for(document, {"subjects": "<Subject 1> holds the letter."})
        proposal["operations"].insert(0, {"op": "update_project", "fields": {
            "subject_definitions": [{"label": "<Subject 1>", "text": "<Subject 1> is the courier in <Picture 1>."}],
            "summary": "[reference generation] <Subject 1> holds a letter.",
            "retention_analysis": [{"label": "<Subject 1>", "where": "[Shot 1]", "relationship": "fully_preserved", "detail": "The subject identity is retained."}],
        }})
        once = _normalize_director_proposal(document, proposal, request)
        self.assertEqual(once, _normalize_director_proposal(document, once, request))
        preview = preview_changeset(document, once, request_data=request)
        repeated = preview_changeset(document, preview["proposal"], request_data=request)
        self.assertEqual(preview, repeated)
        self.assertIn("<Picture 1>", preview["compiled_prompt"])
        self.assertIn("<Subject 1>", preview["compiled_prompt"])

    def test_semantic_validation_is_read_only_and_detects_mutating_checks(self):
        document = director_document()
        original = copy.deepcopy(document)
        with self.assertRaises(ProposalIssueError) as caught:
            validate_stage("semantics", "synthetic", lambda value: value.update(style="wrong"), document)
        self.assertEqual(caught.exception.issue["code"], "validator_mutation")
        self.assertEqual(document, original)

    def test_failed_authorization_has_issue_code_and_preserves_all_inputs(self):
        document = director_document()
        parsed = {"message": "Change light", "proposal_error": "", "proposal": proposal_for(document, {
            "steps": [{"type": "action", "text": "The woman unfolds a letter."}],
        })}
        request = edit_request("Only change the lighting.")
        before = copy.deepcopy((document, parsed, request))
        result = _validate_parsed_proposal(document, parsed, request)
        self.assertIsNone(result["proposal"])
        self.assertIn("protected_content", result["proposal_issues"][0]["detail_codes"])
        self.assertEqual((document, parsed, request), before)

    def test_correction_uses_codes_independent_of_human_error_wording(self):
        messages = _proposal_retry_messages([], "project", proposal_error="Localized explanation", issues=[
            validation_issue("Localized explanation", stage="structure", code="timeline"),
        ])
        correction = messages[-1]["content"]
        self.assertIn('"code":"timeline"', correction)
        self.assertIn("strictly increasing", correction)
        self.assertNotIn("Replace visual-trait placeholders", correction)

    def test_bounded_failure_and_first_pass_metrics_are_observed_not_inferred(self):
        request = next(iter(measurement_requests()))[1]
        original = copy.deepcopy(request)
        with patch("video.director._classify_director_turn", return_value={"route": "mutate", "reference_only": False}), patch(
            "video.director.generate_chat", return_value=json.dumps({"message": "No draft", "proposal": None}),
        ) as generate:
            result = director_chat(request)
        self.assertEqual(generate.call_count, 4)
        self.assertEqual(result["validation_metrics"]["correction_attempts"], 3)
        self.assertFalse(result["validation_metrics"]["first_pass_valid"])
        self.assertEqual(result["validation_metrics"]["first_pass_issues"][0]["code"], "response_contract")
        self.assertIsNone(result["proposal"])
        self.assertEqual(request, original)

    def test_successful_correction_reports_failed_first_attempt(self):
        request = next(iter(measurement_requests()))[1]
        proposal = {"operations": [{"op": "update_shot", "shot_id": "shot-1", "fields": {
            "camera": {"type": "Push In", "speed": "slow", "target": "the courier"},
        }}]}
        responses = [json.dumps({"message": "No draft", "proposal": None}), json.dumps({"message": "Review camera", "proposal": proposal})]
        with patch("video.director._classify_director_turn", return_value={"route": "mutate", "reference_only": False}), patch(
            "video.director.generate_chat", side_effect=responses,
        ):
            result = director_chat(request)
        self.assertIsNotNone(result["proposal"], result["proposal_error"])
        self.assertFalse(result["validation_metrics"]["first_pass_valid"])
        self.assertEqual(result["validation_metrics"]["correction_attempts"], 1)
        self.assertEqual(result["proposal_issues"], [])

    def test_tokens_are_explicitly_unmeasured_without_exact_tokenizer(self):
        messages = [{"role": "user", "content": "Červená loď"}]
        self.assertIsNone(measure_messages(messages)["prompt_tokens"])
        self.assertEqual(measure_messages(messages, lambda _: 5)["prompt_tokens"], 5)
        with self.assertRaises(ValueError):
            measure_messages(messages, lambda _: 2.5)

    def test_evaluation_hook_repeats_identical_inputs_and_keeps_unknown_metrics_null(self):
        samples = []
        def synthetic(data):
            samples.append(copy.deepcopy(data))
            data["document"]["style"] = "Caller mutation must not reach next sample"
            return {"proposal": None, "intent_route": "clarify"}
        report = evaluate_requests(synthetic, {"provider": "synthetic", "api_key": "private"}, seeds=(17,), repeats=2)
        self.assertEqual(len(samples), 40)
        self.assertEqual(samples[0], samples[1])
        self.assertNotIn("api_key", report["settings"])
        self.assertIsNone(report["records"][0]["first_pass_valid"])
        self.assertIsNone(report["records"][0]["correction_attempts"])

    def test_authorized_literal_translation_normalizes_once_without_collateral_edit(self):
        document = director_document()
        steps = copy.deepcopy(document["shots"][0]["steps"])
        steps[1].update(text="Neprepisuj toto.", language="Slovak")
        proposal = proposal_for(document, {"steps": steps})
        request = edit_request("Prelož dialóg do slovenčiny.", changes=("dialogue",))
        once = _normalize_director_proposal(document, proposal, request)
        self.assertEqual(once, _normalize_director_proposal(document, once, request))
        preview = preview_changeset(document, once, request_data=request)
        self.assertEqual(preview["document"]["shots"][0]["steps"][1]["text"], "Neprepisuj toto.")
        self.assertEqual(preview["document"]["shots"][0]["steps"][1]["speaker_id"], "S1")
        self.assertEqual(preview["document"]["shots"][1], document["shots"][1])


if __name__ == "__main__":
    unittest.main()
