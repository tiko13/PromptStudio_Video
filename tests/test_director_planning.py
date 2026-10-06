import copy
import json
import unittest
from unittest.mock import patch

from video.contracts import default_document, normalize_document
from video.director import director_chat, compact_project_context, preview_changeset, _validate_requested_project_result, _validate_requested_camera_mechanics, _create_director_plan
from video.director_planning import (
    PLAN_SCHEMA, REVIEW_SCHEMA, PLANNING_POLICY, REVIEW_POLICY,
    planning_payload, normalize_plan, validate_plan_result, create_plan, review_result,
)


def request(text='A woman sits in an office, then stands and walks forward as the camera follows her.'):
    return {'document': default_document(), 'scope': 'project', 'context_budget_chars': 32000,
            'messages': [{'role': 'user', 'content': text}]}


def raw_plan(structure='continuous', shot_id='shot-1', start=0):
    return {'structure': structure, 'rationale': 'One continuous event.', 'structure_evidence': '',
            'continuity': ['Same woman and office throughout.'],
            'shots': [{'existing_shot_id': shot_id, 'start': start, 'purpose': 'Stand and walk',
                       'entry_state': 'Woman seated at the office desk.',
                       'action_beats': ['She pushes back her chair, stands, then walks forward.'],
                       'exit_state': 'Same woman walking forward in the same office.',
                       'camera': 'The camera follows as she walks.', 'cut_reason': ''}]}


def response(environment='A sunlit office with the desk behind her.'):
    return {'message': 'One continuous shot.', 'proposal': {'summary': 'Stand and walk in one shot', 'operations': [
        {'op': 'update_shot', 'shot_id': 'shot-1', 'fields': {
            'subjects': 'A woman in a blue suit.', 'environment': environment, 'lighting': 'Daylight from an office window.',
            'composition': 'A medium shot frames her seated at the desk.',
            'steps': [{'type': 'action', 'text': 'She pushes back her chair and stands, then walks forward.'}],
            'camera': {'type': 'Tracking Shot', 'speed': 'slow', 'amplitude': 'small', 'target': 'the woman'},
        }}]}}


class ShotPlanTests(unittest.TestCase):
    def payload(self, data=None):
        data = data or request()
        document, context = compact_project_context(data)
        return planning_payload(data, document, context, data['messages'])

    def test_continuous_plan_does_not_turn_beats_into_cuts(self):
        value = normalize_plan(raw_plan(), self.payload())
        self.assertEqual(value['timeline'], [{'id': 'shot-1', 'start': 0}])
        self.assertEqual(value['shots'][0]['exit_state'], 'Same woman walking forward in the same office.')

    def test_new_shot_ids_are_assigned_by_code_and_existing_ids_survive(self):
        data = request('Use two shots; cut at 2.5 seconds.')
        value = raw_plan('sequence'); value['structure_evidence'] = data['messages'][0]['content']
        second = copy.deepcopy(value['shots'][0]); second.update(existing_shot_id='', start=2.5, cut_reason='Requested cut at 2.5 seconds.')
        value['shots'].append(second)
        plan = normalize_plan(value, self.payload(data))
        self.assertEqual(plan['timeline'], [{'id': 'shot-1', 'start': 0}, {'id': 'planned-shot-2', 'start': 2.5}])

    def test_sequence_cannot_cite_assistant_suggestion_as_user_authority(self):
        data = request(); data['messages'].insert(0, {'role': 'assistant', 'content': 'Use two shots.'})
        value = raw_plan('sequence'); value['structure_evidence'] = 'Use two shots.'
        with self.assertRaisesRegex(ValueError, 'verbatim user clause'):
            normalize_plan(value, self.payload(data))

    def test_bad_plans_fail_closed(self):
        mutations = [
            lambda p: p.update(structure='invented'),
            lambda p: p['shots'][0].update(start=float('nan')),
            lambda p: p['shots'][0].update(start=float('inf')),
            lambda p: p['shots'][0].update(start=True),
            lambda p: p['shots'][0].update(start=2),
            lambda p: p['shots'][0].update(existing_shot_id='Shot 1'),
            lambda p: p['shots'].append(copy.deepcopy(p['shots'][0])),
            lambda p: p['shots'][0].update(action_beats=[]),
        ]
        for mutation in mutations:
            value = raw_plan(); mutation(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_plan(value, self.payload())

    def test_preserve_plan_keeps_all_existing_shots_even_when_only_one_is_edited(self):
        data = request('Only make the first shot brighter.')
        data['document']['shots'].append({**copy.deepcopy(data['document']['shots'][0]), 'id': 'other', 'start': 2.5})
        plan = normalize_plan(raw_plan('preserve'), self.payload(data))
        self.assertEqual(len(plan['timeline']), 2)
        for mutation in [lambda s: s.update(start=1), lambda s: s.update(existing_shot_id='')]:
            value = raw_plan('preserve'); mutation(value['shots'][0])
            with self.assertRaises(ValueError): normalize_plan(value, self.payload(data))

    def test_selected_scope_cannot_restructure_or_plan_another_shot(self):
        payload = self.payload(); payload.update(scope='shot', selected_shot_id='shot-1')
        with self.assertRaises(ValueError): normalize_plan(raw_plan(), payload)
        self.assertEqual(normalize_plan(raw_plan('preserve'), payload)['structure'], 'preserve')
        payload['selected_shot_id'] = 'other'
        with self.assertRaises(ValueError): normalize_plan(raw_plan('preserve'), payload)

    def test_topology_guard_rejects_extra_cut_wrong_id_and_moved_start(self):
        plan = normalize_plan(raw_plan(), self.payload())
        document = default_document()
        validate_plan_result(plan, document)
        for shots in [[*document['shots'], {'id': 'extra', 'start': 2.5}], [{'id': 'wrong', 'start': 0}], [{'id': 'shot-1', 'start': 1}]]:
            with self.assertRaisesRegex(ValueError, 'planned shot structure'):
                validate_plan_result(plan, {'shots': shots})

    def test_planner_retries_invalid_schema_once_then_fails(self):
        calls = []
        def generate(data, messages, images):
            calls.append(messages)
            self.assertEqual(data['_response_schema'], PLAN_SCHEMA)
            return '{}'
        with self.assertRaisesRegex(ValueError, 'Shot planning failed'):
            create_plan(request(), self.payload(), generate, json.loads)
        self.assertEqual(len(calls), 2)
        self.assertIn('validation feedback', calls[1][-1]['content'])

    def test_reviewer_cannot_silently_pass_missing_or_invalid_verdict(self):
        for value in [None, [], {}, {'issues': None}, {'issues': [{'code': 'continuity', 'detail': ''}]}, {'issues': [{'code': 'unknown', 'detail': 'x'}]}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review_result({}, {}, {}, {}, lambda *_: json.dumps(value), json.loads)

    def test_negated_counts_and_camera_mentions_are_not_reinterpreted_by_legacy_keywords(self):
        data = request('Do not use two shots and do not orbit around her. Keep one continuous tracking shot.')
        data['_shot_plan'] = normalize_plan(raw_plan(), self.payload(data))
        _validate_requested_project_result(data['document'], data)
        _validate_requested_camera_mechanics(data['document'], data)

    def test_review_omits_inert_first_transition_but_preserves_real_cut(self):
        document = default_document()
        document['shots'].append({**copy.deepcopy(document['shots'][0]), 'id': 'second', 'start': 2.5})
        original = copy.deepcopy(document)
        def generate(data, messages, images):
            reviewed = json.loads(messages[1]['content'])['result']
            self.assertNotIn('transition', reviewed['shots'][0])
            self.assertEqual(reviewed['shots'][1]['transition'], document['shots'][1]['transition'])
            return '{"issues": []}'
        review_result({}, {}, {}, document, generate, json.loads)
        self.assertEqual(document, original)

    def test_reference_only_planning_is_deterministic_and_preserves_all_shots(self):
        data = request('Only attach the reference.'); data['_turn_intent'] = {'reference_only': True}
        document, context = compact_project_context(data)
        with patch('video.director.generate_chat') as generate:
            plan = _create_director_plan(data, document, context)
        generate.assert_not_called()
        self.assertEqual(plan['structure'], 'preserve')
        self.assertEqual(plan['timeline'], [{'id': 'shot-1', 'start': 0}])

    def test_extension_plan_uses_tail_duration_and_keeps_source_end_context(self):
        data = request('Continue walking without a cut.')
        data['continuation_context'] = {'authored_tail_duration': 2.5, 'source_end': 'She is already walking with a red folder.'}
        payload = self.payload(data)
        self.assertEqual(payload['effective_duration'], 2.5)
        self.assertEqual(payload['continuation_context'], data['continuation_context'])
        value = raw_plan('sequence'); value['structure_evidence'] = data['messages'][0]['content']
        value['shots'].append({**value['shots'][0], 'existing_shot_id': '', 'start': 3, 'cut_reason': 'Invalid tail boundary.'})
        with self.assertRaisesRegex(ValueError, 'effective duration'):
            normalize_plan(value, payload)


class PlanningPipelineTests(unittest.TestCase):
    def run_pipeline(self, responses, data=None):
        data = data or request(); original = copy.deepcopy(data); phases = []
        intent = {'route': 'mutate', 'reference_only': False, 'resolved_instruction': data['messages'][-1]['content']}
        with patch('video.director._classify_director_turn', return_value=intent), patch('video.director.generate_chat', side_effect=[json.dumps(value) for value in responses]) as generate:
            result = director_chat(data, lambda progress: phases.append(progress['phase']))
        self.assertEqual(data, original, 'Planning must not mutate the request/document')
        return result, generate, phases

    def test_plan_author_review_are_separate_stages_and_metadata_does_not_compile(self):
        result, generate, phases = self.run_pipeline([raw_plan(), response(), {'issues': []}])
        self.assertIsNotNone(result['proposal'], result['proposal_error'])
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(generate.call_args_list[0].args[0]['_response_schema'], PLAN_SCHEMA)
        self.assertEqual(generate.call_args_list[2].args[0]['_response_schema'], REVIEW_SCHEMA)
        self.assertIn('SHOT PLAN FOR THIS TURN', generate.call_args_list[1].args[1][0]['content'])
        self.assertIn('shot_planning', phases); self.assertIn('continuity_review', phases)
        final = preview_changeset(request()['document'], result['proposal'])['compiled_prompt']
        self.assertNotIn('entry_state', final); self.assertNotIn('SHOT PLAN', final)
        self.assertNotIn('[Shot 2]', final)

    def test_extra_cut_is_repaired_before_semantic_review(self):
        wrong = response(); wrong['proposal']['operations'].append({'op': 'add_shot', 'shot': {'id': 'extra', 'start': 2.5, 'steps': [{'type': 'action', 'text': 'She walks.'}]}})
        result, generate, _ = self.run_pipeline([raw_plan(), wrong, response(), {'issues': []}])
        self.assertIsNotNone(result['proposal'], result['proposal_error'])
        self.assertEqual(result['validation_metrics']['first_pass_issues'][0]['code'], 'planned_timeline')
        self.assertEqual(result['validation_metrics']['correction_attempts'], 1)
        self.assertEqual(generate.call_count, 4)

    def test_semantic_scene_drift_repaired_without_changing_the_plan(self):
        issue = {'issues': [{'code': 'continuity', 'detail': 'Shot 1 changed the office into a beach without a request; preserve the office.'}]}
        result, generate, _ = self.run_pipeline([raw_plan(), response('A beach.'), issue, response(), {'issues': []}])
        self.assertIsNotNone(result['proposal'], result['proposal_error'])
        self.assertEqual(result['validation_metrics']['first_pass_issues'][0]['code'], 'continuity_review')
        self.assertIn('preserve the office', generate.call_args_list[3].args[1][-1]['content'])

    def test_plan_conflict_replans_within_the_same_bounded_correction_budget(self):
        issue = {'issues': [{'code': 'plan_conflict', 'detail': 'The plan overlooked the requested continuous tracking; correct the plan.'}]}
        result, generate, _ = self.run_pipeline([raw_plan(), response(), issue, raw_plan(), response(), {'issues': []}])
        self.assertIsNotNone(result['proposal'], result['proposal_error'])
        self.assertEqual(generate.call_count, 6)
        self.assertEqual(result['validation_metrics']['correction_attempts'], 1)

    def test_repeated_continuity_failures_never_publish_applyable_proposal(self):
        issue = {'issues': [{'code': 'continuity', 'detail': 'Unrequested office-to-beach scene change.'}]}
        result, generate, _ = self.run_pipeline([raw_plan(), *[item for _ in range(4) for item in (response('Beach'), issue)]])
        self.assertIsNone(result['proposal']); self.assertEqual(generate.call_count, 9)
        self.assertEqual(result['validation_metrics']['correction_attempts'], 3)
        self.assertIn('continuity review', result['message'])

    def test_advice_does_not_plan_review_or_offer_apply(self):
        with patch('video.director._classify_director_turn', return_value={'route': 'discuss'}), patch('video.director.generate_chat', return_value=json.dumps(response())) as generate:
            result = director_chat(request('Would a tracking shot work?'))
        self.assertEqual(generate.call_count, 1); self.assertIsNone(result['proposal'])

    def test_new_extension_authority_is_internal_and_source_is_unchanged(self):
        from video.extension_planner import plan_extension
        parent = default_document(); original = copy.deepcopy(parent)
        plan = raw_plan(shot_id='extension-shot-1')
        draft = response(); draft['proposal']['operations'][0].update(shot_id='extension-shot-1', replace=True)
        draft['proposal']['operations'].insert(0, {'op': 'update_project', 'replace': True, 'fields': {
            'main_description': 'She walks forward in the office.', 'style': 'Live action.',
            'overall_soundscape': 'Quiet office room tone.', 'non_diegetic_music': 'N/A'}})
        intent = {'route': 'mutate', 'confidence': 0.4, 'reference_only': False,
                  'edit_intent': {'scope': 'fields', 'replacement': 'patch',
                                  'protected_changes': {'dialogue': 'She walks forward in the office.'}}}
        with patch('video.director._classify_director_turn', return_value=intent), patch('video.director.generate_chat', side_effect=[json.dumps(item) for item in (plan, draft, {'issues': []})]) as extension_generate:
            result = plan_extension({'document': parent, 'brief': 'She walks forward in the office.', 'duration_seconds': 5})
        self.assertTrue(result['valid'])
        self.assertEqual(parent, original)
        payload = json.loads(extension_generate.call_args_list[0].args[1][1]['content'])
        self.assertEqual(payload['production_context']['edit_intent']['protected_changes']['dialogue'], '')
        # An HTTP/body flag cannot grant the internal builder's authority.
        data = request('Only change the lighting.'); data['authoring_new_extension'] = True
        with patch('video.director._classify_director_turn', return_value=intent), patch('video.director.generate_chat', side_effect=[json.dumps(item) for item in (raw_plan('preserve'), response(), {'issues': []})]) as generate:
            director_chat(data)
        payload = json.loads(generate.call_args_list[0].args[1][1]['content'])
        self.assertEqual(payload['production_context']['edit_intent']['replacement'], 'patch')


if __name__ == '__main__':
    unittest.main()
