const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create(deviceRate = 48000) {
  let Processor;
  const messages = [];
  vm.runInNewContext(fs.readFileSync('web/conference/audio-worklet.js', 'utf8'), {
    sampleRate: deviceRate,
    AudioWorkletProcessor: class {constructor() {this.port = {postMessage: value => messages.push(value)};}},
    registerProcessor: (_, cls) => {Processor = cls;}
  });
  return {node: new Processor(), messages};
}

function play(node, samples) {
  node.port.onmessage({data: {type: 'play', pcm: new Int16Array(samples).buffer}});
}

test('converts PCM16 and waits for a 250ms reserve before rendering', () => {
  const {node} = create();
  play(node, new Array(960).fill(8192));
  const held = new Float32Array(128);
  node.process([[]], [[held]]);
  assert.ok(held.every(value => value === 0));
  play(node, new Array(11040).fill(8192));
  const released = new Float32Array(128);
  node.process([[]], [[released]]);
  assert.ok(released.every(value => value === .25));
});

test('resamples 48k PCM to the AudioContext device rate', () => {
  const {node} = create(24000);
  play(node, new Array(12000).fill(16384));
  const output = new Float32Array(6000);
  node.process([[]], [[output]]);
  assert.ok(output.every(value => value === .5));
});

test('queue is bounded to four seconds and preserves earlier audio on overload', () => {
  const {node, messages} = create();
  for (let i = 0; i < 201; i++) play(node, new Array(960).fill((i + 1) * 100));
  assert.equal(messages.at(-1).type, 'error');
  assert.equal(messages.at(-1).code, 'overloaded');
  const output = new Float32Array(960);
  node.process([[]], [[output]]);
  assert.ok(output.every(value => value === 100 / 32768));
});

test('underflow outputs silence and Stop clears queued audio', () => {
  const {node} = create();
  const underflow = new Float32Array(128);
  node.process([[]], [[underflow]]);
  assert.ok(underflow.every(value => value === 0));
  play(node, new Array(12000).fill(8192));
  node.port.onmessage({data: {type: 'stop'}});
  const output = new Float32Array(12000);
  node.process([[]], [[output]]);
  assert.ok(output.every(value => value === 0));
});
