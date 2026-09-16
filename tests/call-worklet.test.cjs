const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create() {
  let Processor;
  const messages = [];
  vm.runInNewContext(fs.readFileSync('web/call/audio-worklet.js', 'utf8'), {
    sampleRate: 48000,
    Int16Array,
    AudioWorkletProcessor: class {
      constructor() { this.port = {postMessage: value => messages.push(value)}; }
    },
    registerProcessor: (_, cls) => { Processor = cls; },
  });
  return {node: new Processor(), messages};
}

test('microphone meter is throttled while 20 ms audio frames remain complete', () => {
  const {node, messages} = create();
  for (let block = 0; block < 40; block++) {
    node.process([[new Float32Array(128).fill(.25)]]);
  }

  const frames = messages.filter(message => message.type === 'frame');
  const levels = messages.filter(message => message.type === 'level');
  assert.equal(frames.length, 5);
  assert.ok(frames.every(message => message.pcm.byteLength === 1920));
  assert.equal(levels.length, 1);
});
