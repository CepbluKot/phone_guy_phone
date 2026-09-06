const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const READY = {type: 'ready', version: 1, sampleRate: 48000, channels: 1,
  sampleFormat: 's16le', frameBytes: 1920};

function page() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {disabled: false, textContent: '', value: 0});
    return elements.get(id);
  };
  let microphoneCalls = 0, sourceCalls = 0, context, socket, node;
  class Context {
    constructor() {
      context = this; this.destination = {}; this.sampleRate = 48000;
      this.audioWorklet = {addModule: async url => {this.workletUrl = url;}};
    }
    async resume() {this.resumed = true;}
    close() {this.closed = true;}
    createMediaStreamSource() {sourceCalls++; throw new Error('listener must not create a source');}
  }
  class Socket {
    static OPEN = 1;
    constructor(url) {socket = this; this.url = url; this.readyState = 1; this.sent = [];}
    send(value) {this.sent.push(value);}
    close() {this.closed = true; this.readyState = 3;}
  }
  class WorkletNode {
    constructor() {
      node = this;
      this.port = {sent: [], postMessage: (message, transfer = []) => {
        this.port.sent.push(structuredClone(message, {transfer}));
      }};
    }
    connect(destination) {this.destination = destination;}
    disconnect() {this.disconnected = true;}
  }
  vm.runInNewContext(fs.readFileSync('web/conference/app.js', 'utf8'), {
    document: {getElementById: get}, window: {isSecureContext: true, addEventListener() {}},
    AudioContext: Context, AudioWorkletNode: WorkletNode, WebSocket: Socket,
    navigator: {mediaDevices: {getUserMedia() {microphoneCalls++; throw new Error('microphone denied');}}},
    setTimeout() { return 1; }, clearTimeout() {}
  });
  return {get, context: () => context, socket: () => socket, node: () => node,
    microphoneCalls: () => microphoneCalls, sourceCalls: () => sourceCalls};
}

test('listener connects and disconnects without ever requesting a microphone', async () => {
  const ui = page();
  await ui.get('listen').onclick();
  assert.equal(ui.microphoneCalls(), 0);
  assert.equal(ui.sourceCalls(), 0);
  assert.equal(ui.context().resumed, true);
  assert.equal(ui.context().workletUrl, '/static/conference/audio-worklet.js');
  assert.equal(ui.socket().url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/conference');
  ui.socket().onopen();
  assert.deepEqual(JSON.parse(ui.socket().sent[0]), {type: 'listen', version: 1});
  ui.socket().onmessage({data: JSON.stringify({type: 'preparing'})});
  assert.match(ui.get('status').textContent, /подготов/i);
  ui.socket().onmessage({data: JSON.stringify(READY)});
  assert.equal(ui.node().destination, ui.context().destination);
  assert.match(ui.get('status').textContent, /слушаете/i);
  const pcm = new Int16Array(960).fill(4096).buffer;
  ui.socket().onmessage({data: pcm});
  assert.equal(ui.node().port.sent.at(-1).type, 'play');
  ui.get('disconnect').onclick();
  assert.deepEqual(JSON.parse(ui.socket().sent.at(-1)), {type: 'stop'});
  assert.equal(ui.socket().closed, true);
  assert.equal(ui.context().closed, true);
  assert.equal(ui.microphoneCalls(), 0);
  assert.equal(ui.sourceCalls(), 0);
});

test('stale ready and audio replies after Disconnect cannot restore listening', async () => {
  const ui = page();
  await ui.get('listen').onclick();
  const staleSocket = ui.socket();
  staleSocket.onopen();
  ui.get('disconnect').onclick();
  staleSocket.onmessage({data: JSON.stringify(READY)});
  staleSocket.onmessage({data: new ArrayBuffer(1920)});
  assert.equal(ui.node().destination, undefined);
  assert.equal(ui.node().port.sent.some(message => message.type === 'play'), false);
  assert.equal(ui.microphoneCalls(), 0);
});
