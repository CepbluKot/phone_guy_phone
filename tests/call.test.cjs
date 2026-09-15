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
  let context;

  class Context {
    constructor() {
      context = this;
      this.audioWorklet = {addModule: async () => {}};
    }
    async resume() { this.resumed = true; }
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    close() {}
  }
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 0; }
    close() {}
  }
  class WorkletNode {
    constructor() { this.port = {}; }
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
  return {get, context: () => context};
}

test('browser resumes audio capture before opening the phone call', async () => {
  const ui = page();
  await ui.get('call').onclick();
  assert.equal(ui.context().resumed, true);
});
