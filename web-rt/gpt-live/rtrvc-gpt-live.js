'use strict';

// GPT's own wire protocol (research/latency-optimization's rvc_service/
// server.py), relayed through our own /ws/rvc-gpt (see rt_server.py's
// gpt_live_proxy for why this isn't proxied at the Caddy layer directly).
// Differs from our own /ws/rvc: version 2, a "warming" message while the
// isolated engine loads, and 1-second output hops (outputSamples) instead
// of our ~0.3s blocks -- otherwise the same capture/playback shape via the
// shared /audio-worklet.js.
const SAMPLE_RATE = 48000;
const el = id => document.getElementById(id);
const startButton = el('start'), stopButton = el('stop');
const statusEl = el('status'), latencyEl = el('latency');
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
    s.stream?.getTracks().forEach(track => track.stop());
    try { s.source?.disconnect(); } catch {}
    try { s.node?.disconnect(); } catch {}
    if (s.socket) {
      try {
        if (s.socket.readyState === WebSocket.OPEN) s.socket.send(JSON.stringify({type: 'stop'}));
      } catch {}
      try { s.socket.close(); } catch {}
    }
    try { s.ctx?.close(); } catch {}
  }
  setControls(false);
  el('input').value = 0;
  el('output').value = 0;
  statusEl.textContent = message;
}

function fail(s, message) {
  if (session !== s) return;
  stop('Ошибка: ' + message);
}

async function begin() {
  if (session) return;
  const s = {ready: false, sent: 0, received: 0, processingMs: 0};
  session = s;
  setControls(true);
  statusEl.textContent = 'Запрашиваю микрофон…';

  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS или localhost.');
    s.ctx = new AudioContext({sampleRate: SAMPLE_RATE, latencyHint: 'interactive'});
    await s.ctx.resume();
    await s.ctx.audioWorklet.addModule('/audio-worklet.js');
    s.node = new AudioWorkletNode(s.ctx, 'rt-audio', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit'
    });
    s.node.port.onmessage = ({data}) => {
      if (session !== s) return;
      if (data.type === 'error') return fail(s, data.code);
      if (data.type !== 'capture') return;
      if (!s.ready) return;
      el('input').value = Math.min(1, level(new Int16Array(data.pcm)) * 4);
      s.sent++;
      s.socket.send(data.pcm);
    };

    s.stream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false
    }});
    s.source = s.ctx.createMediaStreamSource(s.stream);
    s.source.connect(s.node);
    s.stream.getTracks().forEach(track => { track.onended = () => fail(s, 'Микрофон отключён.'); });

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    s.socket = new WebSocket(proto + '//' + location.host + '/ws/rvc-gpt');
    s.socket.binaryType = 'arraybuffer';
    s.socket.onopen = () => s.socket.send(JSON.stringify({
      type: 'start', version: 2, sampleRate: SAMPLE_RATE, channels: 1, sampleFormat: 's16le'
    }));
    s.socket.onmessage = ({data}) => {
      if (session !== s) return;
      if (typeof data === 'string') {
        const message = JSON.parse(data);
        if (message.type === 'error') return fail(s, message.code || message.message);
        if (message.type === 'warming') {
          statusEl.textContent = 'Загружаю изолированный движок GPT (первый раз может занять до минуты)…';
          return;
        }
        if (message.type === 'ready') {
          s.ready = true;
          s.node.connect(s.ctx.destination);
          statusEl.textContent = 'Готово — говорите непрерывно (блок ' + message.outputSamples / SAMPLE_RATE + 'с, дольше нашего).';
          return;
        }
        if (message.type === 'metrics') {
          s.processingMs = message.processingMs;
          return;
        }
        if (message.type === 'autoTranspose') {
          s.autoTransposeText = message.measuredHz
            ? 'авто-транспонирование: ваша высота ~' + Math.round(message.measuredHz) + 'Гц -> сдвиг ' +
              message.semitones.toFixed(2) + ' полутонов'
            : 'авто-транспонирование не применено (не удалось измерить высоту)';
          return;
        }
        if (message.type === 'stopped') return;
        return;
      }
      const pcm = data;
      el('output').value = Math.min(1, level(new Int16Array(pcm)) * 4);
      s.node.port.postMessage({type: 'play', pcm}, [pcm]);
      s.received++;
      latencyEl.textContent = 'Обработка блока: ' + s.processingMs.toFixed(0) + ' мс';
      el('diagnostics').textContent = 'Отправлено кадров / получено блоков: ' + s.sent + ' / ' + s.received +
        (s.autoTransposeText ? ' · ' + s.autoTransposeText : ' · авто-транспонирование: измеряю первые 2с…');
    };
    s.socket.onerror = () => fail(s, 'Не удалось подключиться.');
    s.socket.onclose = () => { if (session === s) stop('Соединение закрыто.'); };
  } catch (error) {
    fail(s, error.message);
  }
}

startButton.onclick = () => begin();
stopButton.onclick = () => stop();
setControls(false);
window.addEventListener('pagehide', () => stop());
