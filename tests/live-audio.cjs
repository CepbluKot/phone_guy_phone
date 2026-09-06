// Run from repository root: node tests/live-audio.cjs [seconds]
// Runs the actual AudioWorklet capture/playback code with synthetic audio over WSS.
// This measures transport + PCM output, not physical microphone/speaker hardware.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let Processor;
const seconds = Number(process.argv[2] || 10);
const pending = [], rtts = [], processing = [];
let sent = 0, received = 0, energy = 0, underruns = 0, queueMs = 0;
let dropped = 0, sample = 0, ready = false;
const socket = new WebSocket(process.env.VOICE_WS_URL || 'wss://vm-voice-1.lan.awesomeio.ru/ws/audio');
socket.binaryType = 'arraybuffer';
vm.runInNewContext(fs.readFileSync('web/audio-worklet.js','utf8'), {
  AudioWorkletProcessor: class {constructor() {this.port = {postMessage(data) {
    if (data.type==='capture' && ready) {
      pending.push(performance.now()); socket.send(data.pcm); sent++;
      underruns=data.underruns; dropped=data.dropped; queueMs=data.queueMs;
    }
  }};}}, registerProcessor: (_, cls) => {Processor=cls;}
});
const node = new Processor();
const begun = performance.now();
let rendered = 0;
socket.onopen = () => socket.send(JSON.stringify({type:'start',sampleRate:48000,channels:1,sampleFormat:'s16le',settings:{pitchSemitones:-3,noiseMix:0}}));
socket.onmessage = ({data}) => {
  if (typeof data==='string') {
    const message=JSON.parse(data);
    if (message.type==='error') throw Error(message.code);
    if (message.type==='ready') {ready=true; rendered=0; started=performance.now();}
    if (message.type==='metrics') processing.push(message.processingMs);
    return;
  }
  assert.equal(data.byteLength,1920);
  assert.ok(pending.length);
  rtts.push(performance.now()-pending.shift()); received++;
  node.port.onmessage({data:{type:'play',pcm:data}});
};
socket.onerror = error => {console.error(error); process.exit(1);};
let started=0;
const interval=setInterval(()=>{
  if (!ready) {
    if (performance.now()-begun>10000) throw Error('ready timeout');
    return;
  }
  const elapsed=performance.now()-started;
  const expected=Math.min(seconds*48000, Math.floor(elapsed*48/128)*128);
  while(rendered<expected) {
    const input=new Float32Array(128);
    for(let i=0;i<128;i++) input[i]=.2*Math.sin(2*Math.PI*440*sample++/48000);
    const output=new Float32Array(128);
    node.process([[input]],[[output]]);
    for (const v of output) energy+=v*v;
    rendered+=128;
  }
  if (elapsed>(seconds*1000+500)) {
    clearInterval(interval); ready=false; socket.close();
    rtts.sort((a,b)=>a-b); processing.sort((a,b)=>a-b);
    const result={seconds,sent,received,pcmOutputRms:Math.sqrt(energy/rendered),
      rttP50:rtts[Math.floor(rtts.length*.5)],rttP95:rtts[Math.floor(rtts.length*.95)],
      processingP95:processing[Math.floor(processing.length*.95)],underruns,dropped,queueMs};
    console.log(JSON.stringify(result,null,2));
    assert.equal(received,sent); assert.ok(result.pcmOutputRms>.005); assert.equal(dropped,0);
  }
},4);
