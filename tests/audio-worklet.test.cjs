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
  for (let i = 0; i < 15; i++) node.process([[new Float32Array(128).fill(.25)]], [[new Float32Array(128)]]);
  const frames = messages.filter(m => m.type === 'capture');
  assert.equal(frames.length, 2);
  for (const frame of frames) {
    const pcm = new Int16Array(frame.pcm);
    assert.equal(pcm.length, 960);
    assert.ok(pcm.every(x => x === 8192));
  }
});

test('RVC holds one full burst for jitter margin then plays two bursts without truncation or sample loss', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', mode: 'rvc'}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill(8192).buffer}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill(16384).buffer}});
  const held = new Float32Array(12000);
  node.process([[]], [[held]]);
  assert.ok(held.every(sample => sample === 0));
  const output = new Float32Array(192000);
  node.process([[]], [[output]]);
  assert.ok(output.subarray(0, 96000).every(sample => sample === .25));
  assert.ok(output.subarray(96000).every(sample => sample === .5));
});

test('RVC queue time includes the remaining initial hold', () => {
  const {node, messages} = create();
  node.port.onmessage({data: {type: 'configure', mode: 'rvc'}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill(8192).buffer}});
  node.process([[new Float32Array(960)]], [[new Float32Array(960)]]);
  const capture = messages.find(message => message.type === 'capture');
  assert.ok(capture.queueMs > 2200, capture.queueMs);
  assert.ok(capture.queueMs <= 2250, capture.queueMs);
});

test('RVC queue rejects audio beyond twelve seconds and reports overload without dropping old speech', () => {
  const {node, messages} = create();
  node.port.onmessage({data: {type: 'configure', mode: 'rvc'}});
  for (let i = 0; i < 7; i++) {
    node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill((i + 1) * 1000).buffer}});
  }
  const failure = messages.find(message => message.type === 'error');
  assert.equal(failure.code, 'overloaded');
  const held = new Float32Array(12000);
  node.process([[]], [[held]]);
  const first = new Float32Array(96000);
  node.process([[]], [[first]]);
  assert.ok(first.every(sample => sample === 1000 / 32768));
});

test('DSP server PCM reaches audio output and backlog stays bounded', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', mode: 'dsp'}});
  for (let i = 0; i < 30; i++) node.port.onmessage({data: {type: 'play', pcm: new Int16Array(960).fill(8192).buffer}});
  let heard = false;
  let blocks = 0;
  for (let i = 0; i < 200; i++) {
    const output = new Float32Array(128);
    node.process([[]], [[output]]);
    if (output.some(x => x !== 0)) {heard = true; blocks++;}
  }
  assert.ok(heard);
  assert.ok(blocks <= 150, blocks);
});

test('selected DSP delay shifts actual PCM by exactly five seconds, without losing samples', () => {
  const plain = create().node, delayed = create().node;
  delayed.port.onmessage({data: {type: 'delay', seconds: 5}});
  const baseline = [], result = [];
  for (let block = 0; block < 350; block++) {
    const frame = new Int16Array(960).fill(block < 20 ? 8192 : 0).buffer;
    for (const [node, dest] of [[plain, baseline], [delayed, result]]) {
      node.port.onmessage({data: {type: 'play', pcm: frame}});
      const output = new Float32Array(960);
      node.process([[]], [[output]]);
      dest.push(...output);
    }
  }
  assert.ok(result.slice(0, 240000).every(x => x === 0));
  assert.deepEqual(result.slice(240000), baseline.slice(0, result.length - 240000));
  assert.ok(result.some(x => x !== 0));
});

test('changing DSP delay preserves its existing queued-audio contract', () => {
  const node = create().node;
  node.port.onmessage({data: {type: 'delay', seconds: 5}});
  for (let i = 0; i < 10; i++) node.port.onmessage({data: {type: 'play', pcm: new Int16Array(960).fill(8192).buffer}});
  node.process([[]], [[new Float32Array(960)]]);
  node.port.onmessage({data: {type: 'delay', seconds: 0}});
  const output = new Float32Array(960);
  node.process([[]], [[output]]);
  assert.ok(output.every(x => x === .25));
});

test('changing RVC delay clears queued and delayed audio before new playback', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', mode: 'rvc'}});
  node.port.onmessage({data: {type: 'delay', seconds: 5}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill(8192).buffer}});
  node.process([[]], [[new Float32Array(12000)]]);
  node.port.onmessage({data: {type: 'delay', seconds: 0}});
  const silent = new Float32Array(96000);
  node.process([[]], [[silent]]);
  assert.ok(silent.every(x => x === 0));
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).fill(16384).buffer}});
  node.process([[]], [[new Float32Array(12000)]]);
  const fresh = new Float32Array(96000);
  node.process([[]], [[fresh]]);
  assert.ok(fresh.every(x => x === .5));
});
