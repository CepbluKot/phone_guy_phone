const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const READY_V2 = {
  type: 'ready', version: 2, sampleRate: 48000, channels: 1,
  sampleFormat: 's16le', frameBytes: 1920, outputSamples: 48000
};

function client(options = {}) {
  options.now = options.now ?? 0;
  const elements = new Map();
  const allowedIds = new Set([
    'start', 'stop', 'status', 'latency', 'input', 'output', 'delay',
    'delay-value', 'diagnostics'
  ]);
  const get = id => {
    if (!allowedIds.has(id)) throw new Error(`Unexpected DOM lookup: ${id}`);
    if (!elements.has(id)) elements.set(id, {value: 0, textContent: '', disabled: false});
    return elements.get(id);
  };
  let socket, mirrorSocket, node, ctx;
  const track = {stop() {}};
  const stream = {getTracks: () => [track]};
  class Context {
    constructor() {
      ctx = this; this.sampleRate = 48000; this.destination = {};
      this.audioWorklet = {addModule: async () => {}};
    }
    async resume() {this.resumed = true;}
    close() {this.closed = true;}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
  }
  class Socket {
    static OPEN = 1;
    constructor(url) {
      this.url = url; this.readyState = 1; this.bufferedAmount = 0; this.messages = [];
      if (url.endsWith('/ws/rvc-v2')) socket = this;
      else mirrorSocket = this;
    }
    send(message) {this.messages.push(message);}
    close() {this.closed = true;}
  }
  class WorkletNode {
    constructor() {
      node = this;
      this.port = {messages: [], postMessage: (message, transfer = []) => {
        const delivered = structuredClone(message, {transfer});
        this.port.messages.push(delivered); this.played = delivered;
      }};
    }
    connect() {this.connected = true;}
    disconnect() {this.disconnected = true;}
  }
  vm.runInNewContext(fs.readFileSync('web/live/app.js', 'utf8'), {
    document: {getElementById: get},
    window: {isSecureContext: true, addEventListener() {}},
    AudioContext: Context,
    AudioWorkletNode: WorkletNode,
    navigator: {mediaDevices: {async getUserMedia() {return stream;}}},
    WebSocket: Socket, performance: {now: () => options.now},
    setInterval: callback => {options.interval = callback; return 1;}, clearInterval() {}
  }, {filename: 'web/live/app.js'});
  return {
    get, socket: () => socket, mirrorSocket: () => mirrorSocket,
    node: () => node, context: () => ctx,
    advance(milliseconds) {options.now += milliseconds; options.interval?.();}
  };
}

test('live page exposes only the monitor controls', () => {
  const html = fs.readFileSync('web/live/index.html', 'utf8');
  const interactiveIds = [...html.matchAll(/<(?:button|input)\b[^>]*\bid="([^"]+)"/g)]
    .map(match => match[1]);
  assert.deepEqual(interactiveIds, ['start', 'stop', 'delay']);
  assert.match(html, /Phone Guy/);
});

test('rendered frames go only to mirror socket and relay failure leaves RVC playing', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify(READY_V2)});
  const mirror = ui.mirrorSocket();
  assert.equal(mirror.url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/live-mirror');
  const rendered = new Int16Array(960).fill(7777).buffer;
  ui.node().port.onmessage({data: {type: 'mirror', pcm: rendered}});
  assert.equal(mirror.messages.at(-1), rendered);
  assert.equal(ui.socket().messages.length, 1);
  mirror.bufferedAmount = 7680;
  ui.node().port.onmessage({data: {type: 'mirror', pcm: new ArrayBuffer(1920)}});
  assert.equal(mirror.messages.length, 1);
  mirror.onerror();
  assert.equal(ui.context().closed, undefined);
  assert.equal(ui.get('stop').disabled, false);
});

test('v2 session wires packets, meters and dynamic hop through the worklet', async () => {
  const ui = client();
  await ui.get('start').onclick();
  const socket = ui.socket();
  assert.equal(socket.url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/rvc-v2');
  socket.onopen();
  const start = JSON.parse(socket.messages[0]);
  assert.equal(start.version, 2);
  socket.onmessage({data: JSON.stringify(READY_V2)});
  const configure = ui.node().port.messages.at(-1);
  assert.deepEqual(configure, {type: 'configure', packetSamples: 48000, holdSamples: 6000});
  for (let i = 0; i < 55; i++) {
    ui.node().port.onmessage({data: {
      type: 'capture', pcm: new Int16Array(960).fill(4096).buffer,
      queueMs: 0, underruns: 0
    }});
  }
  assert.equal(socket.messages.length, 56);
  socket.onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 0, outputSamples: 48000,
    consumedSamples: 48000, processingMs: 545.5
  })});
  const reply = new Int16Array(48000).fill(2000).buffer;
  socket.onmessage({data: reply});
  assert.equal(reply.byteLength, 0);
  assert.equal(ui.node().played.type, 'play');
  assert.equal(ui.node().played.pcm.byteLength, 96000);
  assert.ok(ui.get('input').value > 0);
  assert.ok(ui.get('output').value > 0);
});

test('hop declared outside the supported range fails closed', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify({...READY_V2, outputSamples: 96001})});
  assert.match(ui.get('status').textContent, /некоррект/i);
});

test('metrics for a stale hop position are rejected without playback', async () => {
  const ui = client();
  await ui.get('start').onclick();
  const socket = ui.socket();
  socket.onopen();
  socket.onmessage({data: JSON.stringify(READY_V2)});
  for (let i = 0; i < 55; i++) {
    ui.node().port.onmessage({data: {
      type: 'capture', pcm: new Int16Array(960).buffer, queueMs: 0, underruns: 0
    }});
  }
  socket.onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 0, outputSamples: 48000,
    consumedSamples: 96000, processingMs: 10
  })});
  assert.match(ui.get('status').textContent, /некоррект/i);
  assert.notEqual(ui.node().played?.type, 'play');
});

test('delay slider forwards seconds to the worklet and clears on the fly', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.get('delay').value = '2.5';
  ui.get('delay').oninput();
  assert.equal(ui.get('delay-value').textContent, '2.5');
  assert.deepEqual(ui.node().port.messages.at(-1), {type: 'delay', seconds: 2.5});
  assert.match(ui.get('status').textContent, /очищен/i);
});

test('stop sends the stop control and releases the audio context', async () => {
  const ui = client();
  await ui.get('start').onclick();
  const socket = ui.socket();
  socket.onopen();
  socket.onmessage({data: JSON.stringify(READY_V2)});
  ui.get('stop').onclick();
  assert.deepEqual(JSON.parse(socket.messages.at(-1)), {type: 'stop'});
  assert.ok(ui.context().closed);
  assert.ok(socket.closed);
  assert.equal(ui.get('start').disabled, false);
});
