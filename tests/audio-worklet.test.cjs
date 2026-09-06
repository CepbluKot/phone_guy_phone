const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function create() {
  let Processor;
  const messages = [];
  const scope = {AudioWorkletProcessor: class {constructor() {this.port = {postMessage: m => messages.push(m)};}},
    registerProcessor: (_, cls) => {Processor = cls;}};
  vm.runInNewContext(fs.readFileSync('web/audio-worklet.js', 'utf8'), scope);
  return {node: new Processor(), messages};
}

test('128-sample render blocks produce complete 960-sample PCM frames without loss', () => {
  const {node, messages} = create();
  for (let i=0; i<15; i++) node.process([[new Float32Array(128).fill(.25)]], [[new Float32Array(128)]]);
  const frames = messages.filter(m=>m.type==='capture');
  assert.equal(frames.length, 2);
  for (const frame of frames) {
    const pcm = new Int16Array(frame.pcm);
    assert.equal(pcm.length, 960);
    assert.ok(pcm.every(x=>x===8192));
  }
});

test('server PCM reaches audio output and backlog stays bounded', () => {
  const {node} = create();
  for (let i=0; i<30; i++) node.port.onmessage({data:{type:'play',pcm:new Int16Array(960).fill(8192).buffer}});
  let heard = false;
  let blocks = 0;
  for (let i=0; i<200; i++) {
    const output = new Float32Array(128);
    node.process([[]], [[output]]);
    if (output.some(x=>x!==0)) {heard=true; blocks++;}
  }
  assert.ok(heard);
  assert.ok(blocks<=150, blocks);
});
