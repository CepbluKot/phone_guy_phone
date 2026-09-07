'use strict';

const SAMPLE_RATE = 48000;
const MAX_SECONDS = 12;
const VARIANT_ORDER = ['v1-rmvpe', 'v2-fcpe', 'v3-rmvpe-fast', 'v4-fcpe-fast'];
const el = id => document.getElementById(id);
const recordButton = el('record'), stopButton = el('stop');
const statusEl = el('status'), timerEl = el('timer'), resultsEl = el('results');
const fileInput = el('fileInput'), fileGoButton = el('fileGo');
let rec = null;
let busy = false;

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

function setControlsDisabled(disabled) {
  busy = disabled;
  recordButton.disabled = disabled;
  fileGoButton.disabled = disabled || !fileInput.files.length;
  fileInput.disabled = disabled;
}

function appendPlaceholder(key, label) {
  const div = document.createElement('div');
  div.className = 'result';
  div.id = 'result-' + key;
  div.innerHTML = '<h3>' + key + '</h3><p>' + label + '</p><p class="meta">обрабатывается…</p>';
  resultsEl.appendChild(div);
}

function fillResult(info) {
  const div = el('result-' + info.key);
  if (!div) return;
  div.innerHTML = '<h3>' + info.key + '</h3><p>' + info.label + '</p>' +
    '<audio controls src="data:audio/wav;base64,' + info.wavBase64 + '"></audio>' +
    '<p class="meta">обработка всей записи: ' + info.wallMs + ' мс · выход: ' + info.outputSeconds + 'с</p>';
}

// Reads the /api/compare NDJSON stream (one line per finished variant, sent
// as soon as it's ready) rather than one big JSON blob at the end -- so
// results appear one by one and only one variant's audio is ever decoded
// server-side at a time (see rvc_service/rt_server.py's /api/compare).
async function runCompare(body, headers) {
  resultsEl.innerHTML = '';
  statusEl.textContent = 'Отправляю…';
  setControlsDisabled(true);
  try {
    const response = await fetch('/api/compare', {method: 'POST', headers, body});
    if (!response.ok) {
      let code = 'error';
      try { code = (await response.json()).code || code; } catch {}
      throw new Error('Ошибка: ' + code);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let placeholdersReady = false;
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let newlineAt;
      while ((newlineAt = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newlineAt);
        buffer = buffer.slice(newlineAt + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.type === 'start') {
          if (!placeholdersReady) {
            for (const key of VARIANT_ORDER) appendPlaceholder(key, '…');
            placeholdersReady = true;
          }
          statusEl.textContent = 'Обрабатываю запись ' + msg.inputSeconds + 'с через все 4 варианта — по одному, результаты появятся ниже…';
        } else if (msg.type === 'result') {
          fillResult(msg);
        } else if (msg.type === 'done') {
          statusEl.textContent = 'Готово — все 4 варианта обработаны.';
        }
      }
    }
  } catch (error) {
    statusEl.textContent = error.message || 'Ошибка обработки.';
  } finally {
    setControlsDisabled(false);
  }
}

async function process(r) {
  try { r.ctx?.close(); } catch {}
  const totalSamples = r.chunks.reduce((n, c) => n + c.length, 0);
  const merged = new Int16Array(totalSamples);
  let offset = 0;
  for (const chunk of r.chunks) { merged.set(chunk, offset); offset += chunk.length; }
  await runCompare(merged.buffer, {'Content-Type': 'application/octet-stream'});
}

async function processFile() {
  const file = fileInput.files[0];
  if (!file || busy) return;
  const form = new FormData();
  form.append('audio', file, file.name);
  await runCompare(form, undefined);
}

recordButton.onclick = async () => {
  if (rec || busy) return;
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

fileInput.onchange = () => { fileGoButton.disabled = busy || !fileInput.files.length; };
fileGoButton.onclick = () => processFile();
