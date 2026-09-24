const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function create() {
  let Processor;
  const messages = [];
  const scope = {AudioWorkletProcessor: class {constructor() {this.port = {postMessage: m => messages.push(m)};}},
    registerProcessor: (_, cls) => {Processor = cls;}};
  vm.runInNewContext(fs.readFileSync('web/live/audio-worklet.js', 'utf8'), scope,
    {filename: 'web/live/audio-worklet.js'});
  return {node: new Processor(), messages};
}

test('128-sample blocks accumulate into complete 960-sample capture frames', () => {
  const {node, messages} = create();
  for (let i = 0; i < 15; i++) node.process([[new Float32Array(128).fill(.25)]], [[new Float32Array(128)]]);
  const frames = messages.filter(m => m.type === 'capture');
  assert.equal(frames.length, 2);
  for (const frame of frames) {
    assert.equal(new Int16Array(frame.pcm).length, 960);
    assert.ok(new Int16Array(frame.pcm).every(x => x === 8192));
  }
});

test('mirror emits the rendered 48kHz PCM after the selected delay', () => {
  const {node, messages} = create();
  node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 2400}});
  node.port.onmessage({data: {type: 'delay', seconds: 0.1}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(8192).buffer}});
  for (let i = 0; i < 55; i++) node.process([[]], [[new Float32Array(960)]]);
  const mirror = messages.filter(message => message.type === 'mirror').map(message => new Int16Array(message.pcm));
  assert.ok(mirror.length >= 50);
  assert.equal(mirror[0].length, 960);
  assert.ok(mirror[0].every(sample => sample === 0));
  assert.ok(mirror.some(frame => frame.some(sample => sample === 8192)));
});

test('only the server-declared packet size is accepted', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 6000}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(96000).buffer}});
  assert.equal(node.queue.length, 0);
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(8192).buffer}});
  assert.equal(node.queue.length, 1);
});

test('initial hold delays playback by the configured jitter margin', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 6000}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(8192).buffer}});
  const held = new Float32Array(6000);
  node.process([[]], [[held]]);
  assert.ok(held.every(sample => sample === 0));
  const output = new Float32Array(48000);
  node.process([[]], [[output]]);
  assert.ok(output.every(sample => sample === .25));
});

test('underrun grows the hold margin for the next restart', () => {
  const {node} = create();
  node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 6000}});
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(8192).buffer}});
  node.process([[]], [[new Float32Array(6000)]]);
  node.process([[]], [[new Float32Array(48000)]]);
  node.process([[]], [[new Float32Array(128)]]);
  assert.equal(node.underruns, 1);
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(16384).buffer}});
  assert.equal(node.holdSamples, 8880);  // 6000 + 2880 (60 ms) adaptive growth
});

test('delay line shifts playback by exactly the requested seconds', () => {
  const plain = create().node, delayed = create().node;
  for (const node of [plain, delayed]) {
    node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 2400}});
  }
  delayed.port.onmessage({data: {type: 'delay', seconds: 1}});
  const baseline = [], result = [];
  for (let block = 0; block < 110; block++) {
    const frame = new Int16Array(960).fill(block < 15 ? 8192 : 0).buffer;
    for (const [node, dest] of [[plain, baseline], [delayed, result]]) {
      node.port.onmessage({data: {type: 'play', pcm: frame}});
      const output = new Float32Array(960);
      node.process([[]], [[output]]);
      dest.push(...output);
    }
  }
  assert.ok(result.slice(0, 48000).every(x => x === 0));
  assert.deepEqual(result.slice(48000, 48000 + 50976), baseline.slice(0, 50976));
});

test('queue overload reports an error and keeps the queued speech', () => {
  const {node, messages} = create();
  node.port.onmessage({data: {type: 'configure', packetSamples: 48000, holdSamples: 6000}});
  for (let i = 0; i < 13; i++) {
    node.port.onmessage({data: {type: 'play', pcm: new Int16Array(48000).fill(8192).buffer}});
  }
  const failure = messages.find(message => message.type === 'error');
  assert.equal(failure.code, 'overloaded');
  node.process([[]], [[new Float32Array(6000)]]);
  const first = new Float32Array(48000);
  node.process([[]], [[first]]);
  assert.ok(first.every(sample => sample === .25));
});
