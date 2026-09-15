const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {disabled: false, textContent: '', value: 0});
    return elements.get(id);
  };
  let context, socket, node;

  class Context {
    constructor() {
      context = this;
      this.state = 'running';
      this.resumeCalls = 0;
      this.audioWorklet = {addModule: async () => {}};
    }
    async resume() { this.resumed = true; this.resumeCalls++; this.state = 'running'; }
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    close() {}
  }
  class Socket {
    static OPEN = 1;
    constructor() { socket = this; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; }
    send(value) { this.sent.push(value); }
    close() {}
  }
  class WorkletNode {
    constructor() { node = this; this.port = {}; }
    disconnect() {}
  }

  vm.runInNewContext(fs.readFileSync('web/call/app.js', 'utf8'), {
    document: {getElementById: get},
    window: {isSecureContext: true, addEventListener() {}},
    navigator: {mediaDevices: {getUserMedia: async () => ({getTracks: () => []})}},
    AudioContext: Context,
    AudioWorkletNode: WorkletNode,
    WebSocket: Socket,
  });
  return {get, context: () => context, socket: () => socket, node: () => node};
}

test('browser resumes audio capture before opening the phone call', async () => {
  const ui = page();
  await ui.get('call').onclick();
  assert.equal(ui.context().resumed, true);
});

test('browser drops stale microphone frames instead of growing the WebSocket buffer', async () => {
  const ui = page();
  await ui.get('call').onclick();
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify({type: 'ringing'})});

  const first = new ArrayBuffer(1920);
  ui.node().port.onmessage({data: {type: 'frame', pcm: first}});
  assert.equal(ui.socket().sent.filter(value => value instanceof ArrayBuffer).length, 1);

  ui.socket().bufferedAmount = 7680;
  ui.node().port.onmessage({data: {type: 'frame', pcm: new ArrayBuffer(1920)}});
  assert.equal(ui.socket().sent.filter(value => value instanceof ArrayBuffer).length, 1);
});

test('browser resumes capture if the audio context is suspended mid-call', async () => {
  const ui = page();
  await ui.get('call').onclick();
  assert.equal(ui.context().resumeCalls, 1);

  ui.context().state = 'suspended';
  await ui.context().onstatechange();
  assert.equal(ui.context().resumeCalls, 2);
});
