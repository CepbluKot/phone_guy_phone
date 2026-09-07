'use strict';

const SAMPLE_RATE = 48000;
const MAX_SECONDS = 12;
const VARIANT_ORDER = ['v1-rmvpe', 'v2-fcpe', 'v3-rmvpe-fast', 'v4-fcpe-fast'];
const el = id => document.getElementById(id);
const recordButton = el('record'), stopButton = el('stop');
const statusEl = el('status'), timerEl = el('timer'), resultsEl = el('results');
let rec = null;

function level(pcm) {
  if (!pcm.length) return 0;
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.sqrt(sum / pcm.length);
}

function formatTime(seconds) {
  const s = Math.floor(seconds);
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

function stopRecording(reason) {
  if (!rec) return;
  const r = rec;
  rec = null;
  clearInterval(r.timerHandle);
  try { r.source?.disconnect(); } catch {}
  try { r.node?.disconnect(); } catch {}
  r.stream?.getTracks().forEach(t => t.stop());
  recordButton.disabled = false;
  stopButton.disabled = true;
  if (reason === 'process') process(r);
  else { try { r.ctx?.close(); } catch {} }
}

async function process(r) {
  statusEl.textContent = 'Обрабатываю через все 4 варианта — это может занять до минуты…';
  resultsEl.innerHTML = '';
  try { r.ctx?.close(); } catch {}
  const totalSamples = r.chunks.reduce((n, c) => n + c.length, 0);
  const merged = new Int16Array(totalSamples);
  let offset = 0;
  for (const chunk of r.chunks) { merged.set(chunk, offset); offset += chunk.length; }

  try {
    const response = await fetch('/api/compare', {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream'},
      body: merged.buffer,
    });
    if (!response.ok) {
      let code = 'error';
      try { code = (await response.json()).code || code; } catch {}
      throw new Error('Ошибка: ' + code);
    }
    const data = await response.json();
    statusEl.textContent = 'Готово — запись ' + data.inputSeconds + 'с, ниже результаты всех вариантов.';
    for (const key of VARIANT_ORDER) {
      const info = data.results[key];
      if (!info) continue;
      const div = document.createElement('div');
      div.className = 'result';
      div.innerHTML = '<h3>' + key + '</h3><p>' + info.label + '</p>' +
        '<audio controls src="data:audio/wav;base64,' + info.wavBase64 + '"></audio>' +
        '<p class="meta">обработка всей записи: ' + info.wallMs + ' мс · выход: ' + info.outputSeconds + 'с</p>';
      resultsEl.appendChild(div);
    }
  } catch (error) {
    statusEl.textContent = error.message || 'Ошибка обработки.';
  }
}

recordButton.onclick = async () => {
  if (rec) return;
  resultsEl.innerHTML = '';
  statusEl.textContent = 'Запрашиваю микрофон…';
  const r = {chunks: [], startedAt: performance.now()};
  rec = r;
  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS или localhost.');
    r.ctx = new AudioContext({sampleRate: SAMPLE_RATE});
    await r.ctx.resume();
    await r.ctx.audioWorklet.addModule('/audio-worklet.js');
    r.node = new AudioWorkletNode(r.ctx, 'rt-audio', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit'
    });
    r.node.port.onmessage = ({data}) => {
      if (rec !== r || data.type !== 'capture') return;
      const pcm = new Int16Array(data.pcm);
      r.chunks.push(pcm);
      el('input').value = Math.min(1, level(pcm) * 4);
    };
    r.stream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false
    }});
    r.source = r.ctx.createMediaStreamSource(r.stream);
    r.source.connect(r.node);
    // rt-audio also wants an output connection to keep running in some browsers
    r.node.connect(r.ctx.destination);

    recordButton.disabled = true;
    stopButton.disabled = false;
    statusEl.textContent = 'Идёт запись — говорите…';
    r.timerHandle = setInterval(() => {
      const elapsed = (performance.now() - r.startedAt) / 1000;
      timerEl.textContent = formatTime(elapsed);
      if (elapsed >= MAX_SECONDS) stopRecording('process');
    }, 200);
  } catch (error) {
    rec = null;
    statusEl.textContent = 'Ошибка: ' + error.message;
    recordButton.disabled = false;
    stopButton.disabled = true;
  }
};

stopButton.onclick = () => stopRecording('process');
window.addEventListener('pagehide', () => stopRecording('discard'));
