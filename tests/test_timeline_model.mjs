import test from 'node:test';
import assert from 'node:assert/strict';
import {generatedFrames, timingPlan, frame, formatTime, parseTime, editBoundary, changeDuration, guideRange, createHistory} from '../web/js/timeline-model.js';

const project = () => ({document:{duration_seconds:10,shots:[{id:'a',start:0,steps:[]},{id:'b',start:1,steps:[]}],references:[]}});
test('Python half-even ties and native frame counts', () => {
  assert.equal(generatedFrames(5),124); assert.equal(generatedFrames(5.1875),124);
  assert.equal(generatedFrames(10),243); assert.equal(generatedFrames(15),362);
  assert.equal(timingPlan({...project(),extension_source:{parent_project_id:"p",parent_generation_id:"g"}}).delivered,238);
});
test('120 frame nudges have no accumulated drift; edit preserves original', () => {
  const p=project(), original=structuredClone(p);
  for(let i=0;i<120;i++) p.document=editBoundary(p,1,p.document.shots[1].start+1/24);
  assert.equal(p.document.shots[1].start,6); assert.equal(original.document.shots[1].start,1);
});
test('timecode roundtrips every frame through hours and rejects invalid fields', () => {
  for(let f=0;f<100000;f+=7) assert.equal(frame(parseTime(formatTime(f/24))),f);
  for(const value of ['00:00:00:24','00:60:00:00','-1','','1.2']) assert.throws(()=>parseTime(value));
  assert.throws(()=>parseTime('2.5','frames'));
});
test('shortening is atomic and requires explicit permission to truncate events', () => {
  const p=project(); p.document.shots[0].steps=[{start:0,end:1,text:'Protected words'}];
  const original=structuredClone(p);
  assert.throws(()=>editBoundary(p,1,.5),/shorten/); assert.deepEqual(p,original);
  const trimmed=editBoundary(p,1,.5,{events:'trim'});
  assert.equal(trimmed.shots[0].steps[0].end,.5); assert.equal(trimmed.shots[0].steps[0].text,'Protected words');
});
test('ripple shifts later shots; scaling semantic cues never stretches exact audio', () => {
  const p=project();p.document.shots.push({id:'c',start:5,steps:[]});
  const doc=editBoundary(p,1,2,{mode:'ripple'}); assert.equal(doc.shots[2].start,6);
  p.document.shots[0].steps=[{start:0,end:1}];
  assert.equal(editBoundary(p,1,.5,{events:'scale'}).shots[0].steps[0].end,.5);
  p.document.shots[0].audio_clips=[{start:0,end:1}];
  assert.throws(()=>editBoundary(p,1,.5,{events:'scale'}),/shorten/);
});
test('duration reduction rejects cuts outside output and guides use cropped lengths', () => {
  const p=project();p.document.shots[1].start=8;
  assert.throws(()=>changeDuration(p,5),/boundaries/);
  const range=guideRange({kind:'video',guide_frame:100,trim_start:0,trim_end:1},124);
  assert.equal(range.frames,22);assert.equal(range.end,122);assert.equal(range.cropped,true);assert.equal(range.overflow,false);
  assert.equal(guideRange({kind:'audio',guide_frame:120,trim_end:1},124).overflow,true);
});
test('history is bounded, preserves redo, and refuses stale external history', () => {
  const h=createHistory({v:0},2);h.record({v:1});h.record({v:2});h.record({v:3});
  assert.deepEqual(h.undo(),{v:2});assert.deepEqual(h.redo(),{v:3});h.undo();h.record({v:5});assert.equal(h.canRedo,false);
  h.sync({v:99});assert.equal(h.canUndo,false);assert.equal(h.undo(),null);
});
test('scaling a cut leaves untouched shots and exact source metadata unchanged', () => {
  const p=project();p.document.shots.push({id:'c',start:5,steps:[{start:.123,end:1.123,text:'Do not quantize me'}]});
  p.document.shots[0].audio_clips=[{start:0,end:.25,source_start:.321,source_end:.571,gain_db:-4}];
  const doc=editBoundary(p,1,.5,{events:'scale'});
  assert.deepEqual(doc.shots[2],p.document.shots[2]);
  assert.deepEqual(doc.shots[0].audio_clips,p.document.shots[0].audio_clips);
});
test('duration validation and scaling very short cues retain a usable range', () => {
  for (const duration of [0,-1,NaN,Infinity,150]) assert.throws(()=>generatedFrames(duration));
  const p=project();p.document.shots[0].steps=[{start:.96,end:1,text:'Last frame'}];
  const doc=editBoundary(p,1,1/24,{events:'scale'});
  assert.equal(doc.shots[0].steps[0].start,0);assert.equal(doc.shots[0].steps[0].end,1/24);
});
