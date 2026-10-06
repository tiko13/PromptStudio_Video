import test from 'node:test';
import assert from 'node:assert/strict';
import {buildProposalReview, formatReviewValue} from '../web/js/director-proposal-review.js';

const shot = (id, start = 0) => ({id, start, composition: 'Medium-wide shot', subjects: 'Woman in a blue suit',
  environment: 'Office', lighting: 'Daylight', transition: 'the camera cuts to',
  camera: {type: 'Static Shot', amplitude: 'default', speed: 'default', target: 'Woman'},
  steps: [{id: 'step-original', type: 'action', text: 'She sits.'}], sounds: ['Office hum'], visible_text: [], notes: ''});
const document = shots => ({duration_seconds: 5, shots, style: 'Live-action', main_description: 'She stands and walks.', non_diegetic_music: 'N/A'});
const review = (before, after, operations = []) => buildProposalReview(before, after, {operations});

test('new shots expose every field and all ordered action/dialogue steps without internal shot IDs', () => {
  const a = shot('shot-long-uuid');
  const b = {...shot('new-long-uuid', 2.5), composition: 'Tracking medium shot', lighting: '',
    steps: [{type: 'action', text: 'She stands.'}, {type: 'dialogue', text: 'Do not change “this”.', speaker: 'Woman', speaker_id: 'S1', language: 'English'}, {type: 'action', text: 'She walks.'}],
    camera: {type: 'Tracking Shot', speed: 'slow', amplitude: 'small', target: 'Woman'}};
  const result = review(document([a]), document([a, b]), [{op: 'add_shot', shot: b}]);
  assert.deepEqual(result.notices, ['Shot count: 1 → 2.', 'Cut added at 2.5 s.']);
  assert.equal(result.cards[0].status, 'Updated'); // Its end changed when the cut was added.
  assert.equal(result.cards[1].title, 'Shot 2');
  assert.equal(result.cards[1].status, 'New');
  assert.match(result.cards[1].timing, /2.5 s–5.167 s/);
  const fields = Object.fromEntries(result.cards[1].fields.map(field => [field.key, field]));
  assert.equal(fields.environment.value, 'Office');
  assert.equal(fields.lighting.status, 'Not specified');
  assert.match(fields.steps.value, /1\. Action\nShe stands\.[\s\S]*2\. Dialogue[\s\S]*Do not change “this”\.[\s\S]*3\. Action\nShe walks\./);
  assert.match(fields.camera.value, /Tracking Shot\nAmplitude: small\nSpeed: slow\nTarget: Woman/);
  assert.doesNotMatch(JSON.stringify(result), /long-uuid|step-original/);
});

test('partial camera updates show the backend merged result and preserved context', () => {
  const before = document([shot('a')]);
  const after = structuredClone(before); after.shots[0].camera.speed = 'slow';
  const result = review(before, after, [{op: 'update_shot', shot_id: 'a', fields: {camera: {speed: 'slow'}}}]);
  const fields = result.cards[0].fields;
  assert.equal(fields.find(field => field.key === 'environment').status, 'Unchanged');
  assert.match(fields.find(field => field.key === 'camera').value, /Static Shot[\s\S]*Target: Woman/);
  assert.match(fields.find(field => field.key === 'camera').before, /Speed: default/);
  assert.deepEqual(before.shots[0].camera.speed, 'default');
});

test('full replacement and explicit clears show cleared fields and original values', () => {
  const before = document([shot('a')]);
  const after = document([{...shot('a'), environment: '', sounds: [], steps: []}]);
  const result = review(before, after, [{op: 'update_shot', shot_id: 'a', replace: true}]);
  assert.equal(result.cards[0].status, 'Replaced');
  const environment = result.cards[0].fields.find(field => field.key === 'environment');
  assert.equal(environment.status, 'Cleared'); assert.equal(environment.before, 'Office');
  assert.equal(result.cards[0].fields.find(field => field.key === 'steps').status, 'Cleared');
});

test('reorders, removals and timing changes use before and resulting timeline numbering', () => {
  const before = document([shot('a'), shot('b', 2), shot('c', 4)]);
  const after = document([shot('c'), shot('a', 3)]);
  const result = review(before, after);
  assert.deepEqual(result.cards.map(card => [card.title, card.status]), [['Shot 3 → Shot 1', 'Updated'], ['Shot 1 → Shot 2', 'Updated'], ['Shot 2', 'Removed']]);
  assert.equal(result.cards[0].transition, 'Opening shot · no cut');
  assert.equal(result.cards[1].transition, 'Cut at 3 s');
  assert.match(result.cards[2].timing, /2 s–4 s/);
  assert.match(result.cards[1].oldTiming, /0 s–2 s/);
});

test('project-only edit preserves shot details and shows manual override clearing', () => {
  const before = {...document([shot('a')]), prompt_override: 'Manual prompt'};
  const after = {...structuredClone(before), style: 'Watercolor', prompt_override: ''};
  const result = review(before, after);
  assert.equal(result.cards[0].status, 'Unchanged');
  assert.equal(result.project.find(field => field.key === 'prompt_override').status, 'Cleared');
  assert.equal(result.project.find(field => field.key === 'style').before, 'Live-action');
});

test('generated step IDs and object key order do not manufacture edits', () => {
  const before = document([shot('a')]);
  const after = structuredClone(before); after.shots[0].steps[0] = {text: 'She sits.', id: 'new-step-id', type: 'action'};
  assert.equal(review(before, after).cards[0].status, 'Unchanged');
});

test('dialogue, lyrics, reference tokens and visible text remain exact, including HTML-like input', () => {
  const literal = '<Subject 1> says “A & B” <script>alert(1)</script>\nNext line.';
  assert.equal(formatReviewValue(literal), literal);
  const value = formatReviewValue([{type: 'dialogue', performance: 'singing', text: literal, speaker_id: 'S1', utterance_id: 'chorus', crosses_cut: true, voiceover: true, start: 0, end: 2}], 'steps');
  assert.ok(value.includes(literal)); assert.match(value, /Singing/); assert.match(value, /Dialogue link: chorus/);
  assert.match(value, /Crosses cut: Yes/); assert.match(value, /0 s–2 s within shot/);
});

test('review snapshots are detached from mutable document and proposal data', () => {
  const before = document([shot('a')]), after = document([shot('a'), shot('b', 2.5)]);
  const result = review(before, after); const saved = JSON.stringify(result);
  after.shots.reverse(); after.shots[0].environment = 'Beach'; before.shots[0].subjects = 'Someone else';
  assert.equal(JSON.stringify(result), saved);
});
