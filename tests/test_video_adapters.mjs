import assert from 'node:assert/strict';
import {test} from 'node:test';
import {applyVideoAdapters, hasReferenceAdapters, videoAdapterLoaders, adapterMatchesType, REFERENCE_ADAPTER_TYPE, CONTENT_LORA_TYPE} from '../web/js/video-adapters.js';

function snapshot() {
  return {output:{
    '10':{class_type:'PSV_MiniMaxH3Director',inputs:{ref2va_model:['9',0]}},
    '11':{class_type:'PSV_MiniMaxH3SamplingProfile',inputs:{model:['10',0],mode:['10',6]}},
    '12':{class_type:'BasicGuider',inputs:{model:['11',0],conditioning:['10',1]}},
    '13':{class_type:'SamplerCustomAdvanced',inputs:{latent_image:['10',2]}},
  }, workflow:{nodes:[{id:10,outputs:[{links:[1]},{links:[2]}, {}, {}, {}, {}, {links:[3]}]},
    {id:11,outputs:[{links:[4]}]}, {id:12}, {id:13}],
    links:[[1,10,0,11,0,'MODEL'],[2,10,1,12,1,'CONDITIONING'],[3,10,6,11,1,'STRING'],[4,11,0,12,0,'MODEL']]}};
}
const workflow = {director_node_id:'10'};
const doc = {content_loras:[{name:'MiniMax3/style.safetensors',strength:.4}],reference_adapters:[{name:'MiniMax3/hero.safetensors',kind:'reflora',category:'loras',lora_strength:.7,visual_strength:.8,audio_strength:0,components:'visual'}]};

test('new snapshots route model and conditioning once; sampling policy and latent stay intact',()=>{
  const value=snapshot(); const original=structuredClone(value);
  applyVideoAdapters(value,workflow,doc);
  const content=Object.entries(value.output).find(([,n])=>n.class_type===CONTENT_LORA_TYPE);
  const refs=Object.entries(value.output).find(([,n])=>n.class_type===REFERENCE_ADAPTER_TYPE);
  assert.deepEqual(content[1].inputs.model,['10',0]);
  assert.equal(content[1].inputs.lora_type, 'MiniMax3');
  assert.equal(refs[1].inputs.adapter_type, 'MiniMax3');
  assert.deepEqual(refs[1].inputs.model,[content[0],0]);
  assert.deepEqual(refs[1].inputs.positive,['10',1]);
  assert.deepEqual(value.output['11'].inputs.model,[refs[0],0]);
  assert.deepEqual(value.output['12'].inputs.conditioning,[refs[0],1]);
  assert.deepEqual(value.output['13'],original.output['13']);
  assert.deepEqual(value.output['12'].inputs.model,['11',0]);
  assert.equal(value.workflow.links.find(l=>l[0]===2)[1],Number(refs[0]));
  doc.content_loras[0].strength=.9;
  assert.equal(JSON.parse(content[1].inputs.lora_stack_json)[0].strength,.4);
  assert.throws(()=>applyVideoAdapters(value,workflow,doc),/already frozen/);
});

test('workflow Types filter both slash styles, root wildcard and hidden files', () => {
  assert.equal(adapterMatchesType('minimax3\\nested\\hero.safetensors', 'MiniMax3'), true);
  for (const name of ['Image/style.safetensors','root.safetensors','MiniMax3/_hidden.safetensors']) {
    assert.equal(adapterMatchesType(name, 'MiniMax3'), false);
  }
  assert.equal(adapterMatchesType('root.safetensors', '*'), true);
  for (const type of ['', '..', 'MiniMax3/nested']) assert.equal(adapterMatchesType('MiniMax3/style.safetensors',type),false);
  assert.throws(() => applyVideoAdapters(snapshot(), workflow, {content_loras:[{name:'Image/style.safetensors'}]}), /outside workflow Type/);
});

test('fills declared empty workflow loaders once and preserves their Types and wiring', () => {
  const value = snapshot();
  value.output['20'] = {class_type:CONTENT_LORA_TYPE,inputs:{model:['10',0],lora_type:'Characters',lora_stack_json:'[]'}};
  value.output['21'] = {class_type:REFERENCE_ADAPTER_TYPE,inputs:{model:['20',0],positive:['10',1],mode:['10',6],adapter_type:'*',reference_stack_json:'[]'}};
  value.output['11'].inputs.model = ['21',0];
  value.output['12'].inputs.conditioning = ['21',1];
  const loaders = videoAdapterLoaders(workflow,value);
  assert.equal(loaders.content_loras.type,'Characters');
  const selected = {content_loras:[{name:'Characters\\actor.safetensors',strength:.5}], reference_adapters:[{name:'root.safetensors',category:'refmods',kind:'refmod'}]};
  const ids = Object.keys(value.output);
  applyVideoAdapters(value,workflow,selected);
  assert.deepEqual(Object.keys(value.output),ids);
  assert.deepEqual(JSON.parse(value.output['20'].inputs.lora_stack_json),selected.content_loras);
  assert.deepEqual(JSON.parse(value.output['21'].inputs.reference_stack_json),selected.reference_adapters);
  assert.deepEqual(value.output['11'].inputs.model,['21',0]);
  assert.equal(value.output['21'].inputs.adapter_type,'*');
});

test('ambiguous empty loaders fail before changing the snapshot', () => {
  const value = snapshot();
  for (const id of ['20','21']) value.output[id] = {class_type:CONTENT_LORA_TYPE,inputs:{model:['10',0],lora_type:'MiniMax3'}};
  const original = structuredClone(value);
  assert.throws(() => applyVideoAdapters(value,workflow,doc), /More than one empty/);
  assert.deepEqual(value,original);
});
test('empty selection keeps original graph; unsupported reference workflow fails',()=>{
  const value=snapshot(); const original=structuredClone(value);
  applyVideoAdapters(value,workflow,{}); assert.deepEqual(value,original);
  delete value.output['10'].inputs.ref2va_model;
  assert.throws(()=>applyVideoAdapters(value,workflow,doc),/Ref2VA model/);
});
test('zero references do not force Ref2VA; RefLoRA is not also applied as a content LoRA',()=>{
  assert.equal(hasReferenceAdapters({reference_adapters:[{visual_strength:0,audio_strength:0}]}),false);
  assert.equal(hasReferenceAdapters(doc),true);
  assert.throws(()=>applyVideoAdapters(snapshot(),workflow,{...doc,content_loras:[{name:'MiniMax3/hero.safetensors',strength:1}]}),/twice/);
});
