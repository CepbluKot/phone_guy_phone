'use strict';

const SAMPLE_RATE = 48000;
const el = id => document.getElementById(id);
const startButton = el('start'), stopButton = el('stop');
const statusEl = el('status');
let session = null;

function level(pcm) {
  if (!pcm.length) return 0;
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.sqrt(sum / pcm.length);
}

function setControls(running) {
  startButton.disabled = running;
  stopButton.disabled = !running;
}

function stop(message = 'Остановлено') {
  const s = session;
  session = null;
  if (s) {
    try { s.node?.disconnect(); } catch {}
    try { s.socket?.close(); } catch {}
    try { s.ctx?.close(); } catch {}
  }
  setControls(false);
  el('output').value = 0;
  statusEl.textContent = message;
}

async function begin() {
  if (session) return;
  const s = {received: 0};
  session = s;
  setControls(true);
  statusEl.textContent = 'Подключаюсь…';
  try {
    s.ctx = new AudioContext({sampleRate: SAMPLE_RATE});
    await s.ctx.resume();
    await s.ctx.audioWorklet.addModule('/audio-worklet.js');
    s.node = new AudioWorkletNode(s.ctx, 'rt-audio', {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1]
    });
    s.node.connect(s.ctx.destination);

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    s.socket = new WebSocket(proto + '//' + location.host + '/ws/listen');
    s.socket.binaryType = 'arraybuffer';
    s.socket.onopen = () => { statusEl.textContent = 'В эфире — слушайте.'; };
    s.socket.onmessage = ({data}) => {
      if (session !== s) return;
      el('output').value = Math.min(1, level(new Int16Array(data.slice(0))) * 4);
      s.node.port.postMessage({type: 'play', pcm: data}, [data]);
      s.received++;
      el('diagnostics').textContent = 'Получено блоков: ' + s.received;
    };
    s.socket.onerror = () => stop('Не удалось подключиться.');
    s.socket.onclose = () => { if (session === s) stop('Соединение закрыто.'); };
  } catch (error) {
    stop('Ошибка: ' + error.message);
  }
}

startButton.onclick = () => begin();
stopButton.onclick = () => stop();
setControls(false);
window.addEventListener('pagehide', () => stop());
