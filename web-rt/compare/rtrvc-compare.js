'use strict';

const SAMPLE_RATE = 48000;
const MAX_SECONDS = 12;
const POLL_MS = 1000;
const el = id => document.getElementById(id);
const recordButton = el('record'), stopButton = el('stop');
const statusEl = el('status'), timerEl = el('timer'), resultsEl = el('results');
const fileInput = el('fileInput'), fileGoButton = el('fileGo');
let rec = null;
let busy = false;
let pollTimer = null;

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

function renderPlaceholders(order) {
  resultsEl.innerHTML = '';
  for (const key of order) appendPlaceholder(key, '…');
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

function applyJobSnapshot(data) {
  if (data.order && data.order.length && !resultsEl.children.length) renderPlaceholders(data.order);
  for (const key of (data.order || [])) {
    const info = data.results && data.results[key];
    if (info) fillResult({...info, key});
  }
  if (data.status === 'running') {
    statusEl.textContent = 'Обрабатываю запись ' + data.inputSeconds + 'с через все 4 варианта — по одному, результаты появятся ниже. Ссылка на этот результат сохранена в адресной строке.';
  } else if (data.status === 'done') {
    statusEl.textContent = 'Готово — все 4 варианта обработаны. Эту ссылку можно сохранить, чтобы вернуться к результату позже.';
    stopPolling();
    setControlsDisabled(false);
  } else if (data.status === 'error') {
    statusEl.textContent = 'Ошибка обработки: ' + (data.error || 'unknown');
    for (const key of (data.order || [])) {
      if (data.results && data.results[key]) continue;
      const div = el('result-' + key);
      const meta = div?.querySelector('.meta');
      if (meta) meta.textContent = 'не обработано (ошибка)';
    }
    stopPolling();
    setControlsDisabled(false);
  }
}

// Polls GET /api/compare/<jobId> instead of holding one HTTP response open
// for the whole multi-minute conversion (see rvc_service/rt_jobs.py) -- a
// dropped connection just fails one poll tick, not the whole comparison,
// and the job id in the URL means this same page reopened later (or on
// another device) picks the result back up.
async function pollJob(jobId) {
  stopPolling();
  setControlsDisabled(true);
  const tick = async () => {
    try {
      const response = await fetch('/api/compare/' + jobId, {cache: 'no-store'});
      if (response.status === 404) {
        statusEl.textContent = 'Результат не найден (сервер перезапускался или прошло много времени) — начните новое сравнение.';
        setControlsDisabled(false);
        return;
      }
      const data = await response.json();
      applyJobSnapshot(data);
      if (data.status === 'running') pollTimer = setTimeout(tick, POLL_MS);
    } catch {
      pollTimer = setTimeout(tick, POLL_MS);
    }
  };
  await tick();
}

async function startCompareJob(body, headers) {
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
    const data = await response.json();
    const url = new URL(location.href);
    url.searchParams.set('job', data.jobId);
    history.pushState({job: data.jobId}, '', url);
    renderPlaceholders(data.order);
    pollJob(data.jobId);
  } catch (error) {
    statusEl.textContent = error.message || 'Ошибка отправки.';
    setControlsDisabled(false);
  }
}

async function process(r) {
  try { r.ctx?.close(); } catch {}
  const totalSamples = r.chunks.reduce((n, c) => n + c.length, 0);
  const merged = new Int16Array(totalSamples);
  let offset = 0;
  for (const chunk of r.chunks) { merged.set(chunk, offset); offset += chunk.length; }
  await startCompareJob(merged.buffer, {'Content-Type': 'application/octet-stream'});
}

async function processFile() {
  const file = fileInput.files[0];
  if (!file || busy) return;
  const form = new FormData();
  form.append('audio', file, file.name);
  await startCompareJob(form, undefined);
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

(function resumeFromUrl() {
  const jobId = new URLSearchParams(location.search).get('job');
  if (jobId) {
    statusEl.textContent = 'Загружаю сохранённый результат…';
    pollJob(jobId);
  }
})();
