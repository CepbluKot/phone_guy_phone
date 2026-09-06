const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const RVC_READY = {
  type: 'ready', version: 1, sampleRate: 48000, channels: 1,
  sampleFormat: 's16le', frameBytes: 1920, outputSamples: 96000
};

function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

function client(options = {}) {
  options.now = options.now ?? 0;
  const defaults = {profile: 'rvc', delay: '5', pitch: '-1.5', effect: '.85', noise: '.04', gain: '0'};
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      value: defaults[id] ?? 0, textContent: '', disabled: false, hidden: false
    });
    return elements.get(id);
  };
  let socket, node, ctx, stopped = false;
  const track = {stop() {stopped = true;}};
  const stream = {getTracks: () => [track]};
  class Context {
    constructor() {
      ctx = this; this.sampleRate = 48000; this.destination = {};
      this.audioWorklet = {addModule: async () => {
        if (options.moduleGate) await options.moduleGate.promise;
      }};
    }
    async resume() {if (options.resumeGate) await options.resumeGate.promise; this.resumed = true;}
    close() {this.closed = true;}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
    createOscillator() {return {frequency: {}, connect() {}, disconnect() {}, start() {this.started = true;}, stop() {this.stopped = true;}};}
    createGain() {return {gain: {}, connect() {}};}
  }
  class Socket {
    static OPEN = 1;
    constructor(url) {socket = this; this.url = url; this.readyState = 1; this.bufferedAmount = 0; this.messages = [];}
    send(message) {this.messages.push(message);}
    close() {this.closed = true;}
  }
  class WorkletNode {
    constructor() {
      node = this;
      this.port = {messages: [], postMessage: (message) => {
        this.port.messages.push(message); this.played = message;
      }};
    }
    connect() {this.connected = true;}
    disconnect() {this.disconnected = true;}
  }
  vm.runInNewContext(fs.readFileSync('web/app.js', 'utf8'), {
    document: {getElementById: get}, window: {isSecureContext: true, addEventListener() {}},
    location: {host: 'voice.lan.awesomeio.ru'}, AudioContext: Context,
    AudioWorkletNode: WorkletNode,
    navigator: {mediaDevices: {async getUserMedia() {
      if (options.permissionFailure) {const error = new Error(); error.name = 'NotAllowedError'; throw error;}
      if (options.micGate) await options.micGate.promise;
      return stream;
    }}}, WebSocket: Socket, performance: {now: () => options.now},
    setInterval: callback => {options.interval = callback; return 1;}, clearInterval() {},
    setTimeout: callback => {options.timeout = callback; return 2;}, clearTimeout() {}
  });
  return {
    get, socket: () => socket, node: () => node, context: () => ctx, stopped: () => stopped,
    advance(milliseconds) {options.now += milliseconds; options.interval?.();}
  };
}

test('default Phone Guy profile uses version 1 RVC and accepts many captures before one burst reply', async () => {
  const ui = client();
  assert.equal(ui.get('dsp-settings').hidden, true);
  await ui.get('start').onclick();
  const socket = ui.socket();
  assert.equal(socket.url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/rvc');
  assert.equal(ui.get('profile').disabled, true);
  assert.equal(ui.node().port.messages[0].type, 'configure');
  assert.equal(ui.node().port.messages[0].mode, 'rvc');
  socket.onopen();
  const start = JSON.parse(socket.messages[0]);
  assert.equal(start.version, 1);
  assert.equal(start.sampleFormat, 's16le');
  socket.onmessage({data: JSON.stringify({type: 'warming', timeoutSeconds: 90})});
  assert.match(ui.get('status').textContent, /прогрева/i);
  socket.onmessage({data: JSON.stringify(RVC_READY)});
  for (let i = 0; i < 105; i++) {
    ui.node().port.onmessage({data: {
      type: 'capture', pcm: new Int16Array(960).fill(4096).buffer,
      queueMs: 0, underruns: 0, dropped: 0
    }});
  }
  assert.equal(socket.messages.length, 106);
  socket.onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 0, outputSamples: 96000,
    consumedSamples: 96000, processingMs: 686.6
  })});
  socket.onmessage({data: new Int16Array(96000).fill(2000).buffer});
  assert.equal(ui.node().played.type, 'play');
  assert.equal(ui.node().played.pcm.byteLength, 192000);
  assert.ok(ui.get('input').value > 0);
  assert.ok(ui.get('output').value > 0);
});

test('malformed RVC metrics fail closed without forwarding their binary packet', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify(RVC_READY)});
  ui.socket().onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 1, outputSamples: 96000,
    consumedSamples: 96000, processingMs: 10
  })});
  assert.match(ui.get('status').textContent, /Неверн|некоррект/i);
  assert.ok(ui.socket().closed);
  assert.notEqual(ui.node().played?.type, 'play');
});

test('RVC ready metadata and binary length are both validated strictly', async () => {
  const invalidReady = client();
  await invalidReady.get('start').onclick();
  invalidReady.socket().onopen();
  invalidReady.socket().onmessage({data: JSON.stringify({type: 'ready', version: 1})});
  assert.match(invalidReady.get('status').textContent, /некоррект/i);

  const invalidBinary = client();
  await invalidBinary.get('start').onclick();
  invalidBinary.socket().onopen();
  invalidBinary.socket().onmessage({data: JSON.stringify(RVC_READY)});
  for (let i = 0; i < 100; i++) {
    invalidBinary.node().port.onmessage({data: {
      type: 'capture', pcm: new Int16Array(960).buffer,
      queueMs: 0, underruns: 0, dropped: 0
    }});
  }
  invalidBinary.socket().onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 0, outputSamples: 96000,
    consumedSamples: 96000, processingMs: 10
  })});
  invalidBinary.socket().onmessage({data: new ArrayBuffer(1920)});
  assert.match(invalidBinary.get('status').textContent, /некоррект/i);
  assert.notEqual(invalidBinary.node().played?.type, 'play');
});

test('warming has a 90 second deadline and ready audio has a 10 second progress deadline', async () => {
  const warming = client();
  await warming.get('start').onclick();
  warming.socket().onopen();
  warming.socket().onmessage({data: JSON.stringify({type: 'warming', timeoutSeconds: 90})});
  warming.advance(90001);
  assert.match(warming.get('status').textContent, /недоступна/i);

  const ready = client();
  await ready.get('start').onclick();
  ready.socket().onopen();
  ready.socket().onmessage({data: JSON.stringify(RVC_READY)});
  ready.advance(10001);
  assert.match(ready.get('status').textContent, /перестала отвечать/i);
});

test('stop during an await and stale replies cannot resurrect capture or playback', async () => {
  const gate = deferred();
  const ui = client({resumeGate: gate});
  const starting = ui.get('start').onclick();
  ui.get('stop').onclick();
  assert.ok(ui.context().closed);
  gate.resolve();
  await starting;
  assert.equal(ui.socket(), undefined);
  assert.equal(ui.get('profile').disabled, false);

  const live = client();
  await live.get('start').onclick();
  const staleSocket = live.socket();
  staleSocket.onopen();
  staleSocket.onmessage({data: JSON.stringify(RVC_READY)});
  live.get('stop').onclick();
  staleSocket.onmessage({data: JSON.stringify({
    type: 'metrics', outputStart: 0, outputSamples: 96000,
    consumedSamples: 96000, processingMs: 10
  })});
  staleSocket.onmessage({data: new ArrayBuffer(192000)});
  assert.notEqual(live.node().played?.type, 'play');
  assert.ok(live.stopped());
});

test('fallback profile retains DSP settings and the line test always uses DSP', async () => {
  const ui = client();
  ui.get('profile').value = 'dsp';
  ui.get('profile').onchange();
  assert.equal(ui.get('dsp-settings').hidden, false);
  await ui.get('start').onclick();
  assert.equal(ui.socket().url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/audio');
  assert.equal(ui.node().port.messages[0].type, 'configure');
  assert.equal(ui.node().port.messages[0].mode, 'dsp');
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify({type: 'ready'})});
  ui.get('pitch').value = -4;
  ui.get('pitch').oninput();
  assert.equal(JSON.parse(ui.socket().messages.at(-1)).settings.pitchSemitones, -4);

  ui.get('stop').onclick();
  ui.get('profile').value = 'rvc';
  await ui.get('test').onclick();
  assert.equal(ui.socket().url, 'wss://vm-voice-1.lan.awesomeio.ru/ws/audio');
  assert.equal(ui.node().port.messages[0].type, 'configure');
  assert.equal(ui.node().port.messages[0].mode, 'dsp');
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify({type: 'ready'})});
  assert.match(ui.get('status').textContent, /DSP/i);
});

test('fallback DSP still accepts server metrics before each PCM reply', async () => {
  const ui = client();
  ui.get('profile').value = 'dsp';
  await ui.get('start').onclick();
  ui.socket().onopen();
  ui.socket().onmessage({data: JSON.stringify({type: 'ready'})});
  ui.node().port.onmessage({data: {
    type: 'capture', pcm: new Int16Array(960).fill(4096).buffer,
    queueMs: 40, underruns: 0, dropped: 0
  }});
  ui.socket().onmessage({data: JSON.stringify({type: 'metrics', processingMs: 12.5})});
  ui.socket().onmessage({data: new Int16Array(960).fill(2000).buffer});
  assert.equal(ui.node().played.type, 'play');
  assert.equal(ui.socket().closed, undefined);
});

test('microphone rejection reports actionable error and releases audio context', async () => {
  const ui = client({permissionFailure: true});
  await ui.get('start').onclick();
  assert.match(ui.get('status').textContent, /Разрешите/);
  assert.ok(ui.context().closed);
  assert.equal(ui.get('start').disabled, false);
});

test('server overload is displayed as actionable failure', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.socket().onmessage({data: JSON.stringify({type: 'error', code: 'overloaded'})});
  assert.match(ui.get('status').textContent, /не успевает/i);
  assert.ok(ui.stopped());
});

test('server busy remains an actionable failure', async () => {
  const ui = client();
  await ui.get('start').onclick();
  ui.socket().onmessage({data: JSON.stringify({type: 'error', code: 'busy'})});
  assert.match(ui.get('status').textContent, /занята/i);
  assert.ok(ui.stopped());
});
