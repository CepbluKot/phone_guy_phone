const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function client(permissionFailure = false) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value: 0, textContent: '', disabled: false});
    return elements.get(id);
  };
  let socket, node, ctx, stopped = false;
  const track = {stop() {stopped=true;}};
  class Context {
    constructor() {ctx=this; this.sampleRate=48000; this.audioWorklet={addModule:async()=>{}};}
    async resume() {this.resumed=true;}
    close() {this.closed=true;}
    createMediaStreamSource() {return {connect() {},disconnect() {}};}
  }
  class Socket {
    static OPEN = 1;
    constructor() {socket=this; this.readyState=1; this.bufferedAmount=0; this.messages=[];}
    send(message) {this.messages.push(message);}
    close() {this.closed=true;}
  }
  vm.runInNewContext(fs.readFileSync('web/app.js','utf8'), {
    document:{getElementById:get}, window:{isSecureContext:true,addEventListener() {}},
    location:{host:'voice.lan.awesomeio.ru'}, AudioContext:Context,
    AudioWorkletNode:class {constructor() {node=this; this.port={postMessage:message=>{this.played=message;}};} connect() {} disconnect() {}},
    navigator:{mediaDevices:{async getUserMedia() {
      if(permissionFailure) {const error=new Error(); error.name='NotAllowedError'; throw error;}
      return {getTracks:()=>[track]};
    }}}, WebSocket:Socket, performance,
    setInterval:()=>1,clearInterval() {},setTimeout:()=>2,clearTimeout() {}
  });
  return {get, socket:()=>socket, node:()=>node, context:()=>ctx, stopped:()=>stopped};
}

test('start transfers capture to websocket and server PCM to playback without implicit DOM globals', async () => {
  const ui=client();
  await ui.get('start').onclick();
  const socket=ui.socket();
  socket.onopen();
  assert.equal(JSON.parse(socket.messages[0]).sampleFormat,'s16le');
  socket.onmessage({data:JSON.stringify({type:'ready'})});
  ui.node().port.onmessage({data:{type:'capture',pcm:new Int16Array(960).fill(4096).buffer,queueMs:40,underruns:0,dropped:0}});
  assert.equal(socket.messages[1].byteLength,1920);
  socket.onmessage({data:new Int16Array(960).fill(2000).buffer});
  assert.equal(ui.node().played.type,'play');
  assert.ok(ui.get('input').value>0);
  assert.ok(ui.get('output').value>0);
  ui.get('stop').onclick();
  assert.ok(ui.stopped()); assert.ok(ui.context().closed); assert.ok(socket.closed);
  assert.equal(ui.get('start').disabled,false);
});

test('microphone rejection reports actionable error and releases audio context', async () => {
  const ui=client(true);
  await ui.get('start').onclick();
  assert.match(ui.get('status').textContent,/Разрешите/);
  assert.ok(ui.context().closed);
  assert.equal(ui.get('start').disabled,false);
});

test('server busy is displayed as failure and settings update a live session', async () => {
  const ui=client();
  await ui.get('start').onclick();
  ui.socket().onmessage({data:JSON.stringify({type:'ready'})});
  ui.get('pitch').value=-4;
  ui.get('pitch').oninput();
  assert.equal(JSON.parse(ui.socket().messages[0]).settings.pitchSemitones,-4);
  ui.socket().onmessage({data:JSON.stringify({type:'error',code:'busy'})});
  assert.match(ui.get('status').textContent,/занята/);
  assert.ok(ui.stopped());
});
