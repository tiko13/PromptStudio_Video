"""Opt-in planning matrix against the running shared Director/llama.cpp service.

Previews synthetic documents only: never saves a project or queues video rendering.
Run with ComfyUI's Python. Results include full requests/proposals for manual review.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video.contracts import default_document, normalize_document
from video.continuation import build_continuation_document


def http(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode()) from exc


def office(two=False):
    doc = default_document(); doc['duration_seconds'] = 8
    doc['shots'][0].update(subjects='A woman in a blue suit with short brown hair.', composition='A medium-wide shot of the woman seated at a wooden desk.',
                          environment='A daylight office with a wooden desk and a window behind it.', lighting='Soft window daylight.',
                          steps=[{'type': 'action', 'text': 'The woman sits at her desk.'}])
    if two:
        second = copy.deepcopy(doc['shots'][0]); second.update(id='shot-2', start=4, composition='A closer view of the same woman at the same desk.')
        doc['shots'].append(second)
    return normalize_document(doc)


def cases():
    items = [
        ('office', 'A young woman sits in an office, then stands up and walks forward as the camera follows her.', default_document(), 1, {}),
        ('no-cut', 'One uninterrupted take with no cuts: the woman stands up, picks up a red folder from the desk and walks through the office doorway. The camera follows her.', office(), 1, {}),
        ('explicit-cut', 'Use exactly two shots. In the same office she stands holding a red folder in her right hand. Cut at 4 seconds to a closer view as she walks forward, keeping the folder in her right hand and the same clothes and office.', office(), 2, {'cuts': [4]}),
        ('scene-change', 'Write exactly two shots: the woman closes her folder in the office; cut at 4 seconds to the same woman arriving on a sunny beach, still carrying that closed folder and wearing the same blue suit.', office(), 2, {'cuts': [4]}),
        ('local-edit', 'Only change the lighting to warm evening light in both shots. Keep everything else unchanged.', office(True), 2, {'preserve': True}),
        ('selected-shot', 'Only make the camera a slow push-in toward the woman. Keep the scene and action unchanged.', office(True), 2, {'scope': 'shot', 'selected_shot_id': 'shot-2', 'preserve': True}),
        ('slovak', 'Žena sedí v kancelárii, potom vstane a kráča dopredu, kamera ju sleduje. Bez strihu, stále tá istá kancelária a oblečenie.', office(), 1, {}),
        ('concurrent-dialogue', 'In one continuous shot she smiles while waving and says exactly "Wait for me." Keep the smile and wave going during the line, then lower her hand. The camera stays still.', office(), 1, {'literal': 'Wait for me.'}),
        ('delegated-montage', 'Create a three-shot montage of the same woman carrying the red folder: at the office desk, in the corridor, then outside the building. Keep her blue suit and short brown hair throughout. Choose appropriate cut times.', office(), 3, {}),
        ('negated-cut', 'Do not add a second shot or cut away. She rises from the chair and walks forward while the camera follows, all in the same office.', office(), 1, {}),
        ('negative-count', 'Do not use two shots and do not orbit around her. Keep this as one uninterrupted take: she gets up and walks forward with the camera following her in the same office.', office(), 1, {}),
    ]
    anchored = office(); anchored['references'] = [{'id': 'first', 'kind': 'image', 'path': 'synthetic-first-frame.png', 'roles': ['first_frame'], 'observed_visual_facts': 'The woman sits at the desk in the office.'}]
    items.append(('first-frame', 'From the supplied first frame, she stands and walks forward while the camera follows. Preserve the appearance and office shown in the image.', normalize_document(anchored), 1, {}))
    bookends = copy.deepcopy(anchored); bookends['references'].append({'id': 'last', 'kind': 'image', 'path': 'synthetic-last-frame.png', 'roles': ['last_frame']})
    items.append(('bookends', 'Connect the supplied first and last frames in one continuous shot: she rises from her seat, walks forward and settles into the final standing pose. Preserve her identity and the office.', normalize_document(bookends), 1, {}))
    moving = office()
    moving['shots'][0].update(steps=[{'type': 'action', 'text': 'She stands, picks up a red folder in her right hand and walks toward the office door.'}],
                              camera={'type': 'Tracking Shot', 'amplitude': 'small', 'speed': 'slow', 'target': 'the woman'})
    items.append(('extension', 'Continue her ongoing walk through the office door into the corridor without a cut. She keeps the red folder in her right hand and the camera keeps following. Do not repeat standing or picking up the folder.', normalize_document(moving), 1, {'extension': True}))
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', default='http://127.0.0.1:8188')
    parser.add_argument('--llama', default='http://127.0.0.1:8080')
    parser.add_argument('--cases', default='office,explicit-cut,local-edit')
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('test-results/planning-live.json'))
    args = parser.parse_args()
    models = http(args.llama + '/v1/models')['data']
    loaded = [item for item in models if item.get('status', {}).get('value') == 'loaded']
    if len(loaded) != 1: raise RuntimeError('Expected exactly one loaded llama.cpp model; select one before testing.')
    model = loaded[0]['id']
    requested = set(args.cases.split(',')); records = []
    previous = {item['case']: item for item in json.loads(args.output.read_text(encoding='utf-8'))} if args.resume and args.output.exists() else {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for name, text, document, count, expected in cases():
        if 'all' not in requested and name not in requested: continue
        record = {'case': name, 'model': model, 'seed': args.seed, 'request': text, 'before': document}
        started = time.monotonic(); print(f'{name}: starting', flush=True)
        payload = {'async': True, 'job_id': str(uuid.uuid4()), 'document': document, 'scope': expected.get('scope', 'project'),
                   'selected_shot_id': expected.get('selected_shot_id', ''), 'messages': [{'role': 'user', 'content': text}],
                   'llm_provider': 'llamacpp', 'llamacpp_url': args.llama, 'llamacpp_model': model, 'keep_models_loaded': True,
                   'thinking_mode': 'Disabled', 'context_budget_chars': 32000, 'max_response_tokens': 5000,
                   'temperature': 0.2, 'sampler_seed': args.seed}
        endpoint = '/promptstudio-video/continuations/plan' if expected.get('extension') else '/promptstudio-video/director/chat'
        if expected.get('extension'):
            payload.update(brief=text, duration_seconds=5, source_effective_duration=8)
        try:
            old = previous.get(name, {})
            old_job = old.get('result', {}).get('job', {}).get('job_id') if old.get('request') == text and old.get('seed') == args.seed else None
            if old_job:
                payload['job_id'] = old_job
                result = http(args.origin + endpoint + '/' + old_job)
            else:
                result = http(args.origin + endpoint, payload)
            last_phase = None
            while (result.get('job_id') or result.get('job')) and result.get('status') not in {'complete', 'failed', 'cancelled'}:
                if time.monotonic() - started > 600:
                    http(args.origin + endpoint + '/' + payload['job_id'] + '/cancel', {})
                    raise TimeoutError('Director matrix case exceeded ten minutes; cancellation requested')
                time.sleep(1)
                result = http(args.origin + endpoint + '/' + payload['job_id'])
                phase = result.get('director_progress', {}).get('phase')
                if phase != last_phase:
                    print(f'{name}: {phase or result.get("status")}', flush=True); last_phase = phase
            if result.get('status') in {'failed', 'cancelled'}: raise RuntimeError(result.get('error', 'Job failed'))
            result = result.get('result', result); record['result'] = result
            if expected.get('extension'):
                assert result.get('valid'), result
                build_continuation_document(document, text, 5, extension_document=result['document'])
                preview = result
            else:
                if not result.get('proposal'): raise AssertionError(result.get('proposal_error') or 'No proposal')
                preview = http(args.origin + '/promptstudio-video/director/preview', {'document': document, 'proposal': result['proposal']})
            record['preview'] = preview
            shots = preview['document']['shots']; assert len(shots) == count, (len(shots), count)
            if expected.get('cuts'): assert [s['start'] for s in shots[1:]] == expected['cuts']
            if expected.get('preserve'): assert [(s['id'], s['start']) for s in shots] == [(s['id'], s['start']) for s in document['shots']]
            if name == 'local-edit':
                for old, new in zip(document['shots'], shots):
                    for field in ['subjects', 'steps', 'camera', 'sounds', 'visible_text']:
                        assert old[field] == new[field], ('unrequested change', field)
            if name == 'selected-shot':
                assert shots[0] == document['shots'][0], 'The unselected shot changed'
                for field in ['subjects', 'environment', 'lighting', 'steps', 'sounds', 'visible_text']:
                    assert shots[1][field] == document['shots'][1][field], ('unrequested change', field)
                assert shots[1]['camera']['type'] == 'Push In'
            if expected.get('literal'): assert expected['literal'] in preview['compiled_prompt']
            if name in {'office', 'no-cut', 'slovak', 'negated-cut', 'negative-count', 'first-frame', 'extension'}: assert shots[0]['camera']['type'] == 'Tracking Shot', shots[0]['camera']
            record['passed'] = True
        except Exception as exc:
            record.update(passed=False, error=str(exc))
        record['seconds'] = round(time.monotonic() - started, 2); records.append(record)
        args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'{name}: {"PASS" if record["passed"] else "FAIL"} in {record["seconds"]}s {record.get("error", "")}', flush=True)
    if not records or not all(record['passed'] for record in records): raise SystemExit(1)


if __name__ == '__main__': main()
