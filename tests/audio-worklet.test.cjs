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

test('selected delay shifts actual PCM by exactly five seconds, without losing samples', () => {
  const plain = create().node, delayed = create().node;
  delayed.port.onmessage({data:{type:'delay',seconds:5}});
  const baseline=[], result=[];
  for (let block=0;block<350;block++) {
    const frame=new Int16Array(960).fill(block<20 ? 8192 : 0).buffer;
    for (const [node,dest] of [[plain,baseline],[delayed,result]]) {
      node.port.onmessage({data:{type:'play',pcm:frame}});
      const output=new Float32Array(960);
      node.process([[]],[[output]]);
      dest.push(...output);
    }
  }
  assert.ok(result.slice(0,240000).every(x=>x===0));
  assert.deepEqual(result.slice(240000),baseline.slice(0,result.length-240000));
  assert.ok(result.some(x=>x!==0));
});

test('changing delay clears old speech and zero delay immediately plays new PCM', () => {
  const node=create().node;
  node.port.onmessage({data:{type:'delay',seconds:5}});
  for(let i=0;i<10;i++) node.port.onmessage({data:{type:'play',pcm:new Int16Array(960).fill(8192).buffer}});
  node.process([[]],[[new Float32Array(960)]]);
  node.port.onmessage({data:{type:'delay',seconds:0}});
  const output=new Float32Array(960);
  node.process([[]],[[output]]);
  assert.ok(output.every(x=>x===.25));
});
