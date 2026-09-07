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
let socket = null;
const filledKeys = new Set();

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

// Only one <audio> plays at a time: starting one pauses every other result
// (and the just-started recording/upload wipes stale ones via renderPlaceholders,
// so this only has to handle "user pressed play on two cards").
function pauseOtherAudios(current) {
  for (const audio of resultsEl.querySelectorAll('audio')) {
    if (audio !== current) audio.pause();
  }
}

// Called once per key, ever (see filledKeys below) -- both the websocket
// push and the polling fallback resend the *whole* job snapshot on every
// update, and re-setting an <audio> element's innerHTML while it's mid
// playback restarts it. Rendering a key only the first time it appears
// keeps an already-playing result untouched when a later result lands.
function fillResult(info) {
  const div = el('result-' + info.key);
  if (!div || filledKeys.has(info.key)) return;
  filledKeys.add(info.key);
  if (info.error) {
    div.innerHTML = '<h3>' + info.key + '</h3><p>' + (info.label || '') + '</p>' +
      '<p class="meta">не обработано: ' + info.error + '</p>';
    return;
  }
  const src = info.wavUrl || ('data:audio/wav;base64,' + info.wavBase64);
  const wallText = info.wallMs ? ('обработка: ' + info.wallMs + ' мс · ') : '';
  div.innerHTML = '<h3>' + info.key + '</h3><p>' + info.label + '</p>' +
    '<audio controls src="' + src + '"></audio>' +
    '<p class="meta">' + wallText + 'выход: ' + info.outputSeconds + 'с</p>';
  div.querySelector('audio').addEventListener('play', (e) => pauseOtherAudios(e.target));
}

function renderPlaceholders(order) {
  resultsEl.innerHTML = '';
  filledKeys.clear();
  for (const key of order) appendPlaceholder(key, '…');
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

function closeSocket() {
  if (socket) { try { socket.close(); } catch {} socket = null; }
}

function applyJobSnapshot(data) {
  if (data.order && data.order.length && !resultsEl.children.length) renderPlaceholders(data.order);
  for (const key of (data.order || [])) {
    const info = data.results && data.results[key];
    if (info) fillResult({...info, key});
  }
  if (data.status === 'running') {
    statusEl.textContent = 'Обрабатываю запись ' + data.inputSeconds + 'с — результаты появятся ниже по одному, каждый источник считает независимо. Ссылка на этот результат сохранена в адресной строке.';
  } else if (data.status === 'done') {
    statusEl.textContent = 'Готово. Эту ссылку можно сохранить, чтобы вернуться к результату позже.';
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

// Polling fallback for GET /api/compare/<jobId> -- only used if the
// websocket below can't even connect (odd proxy, browser without WS).
// A dropped tick just retries on the next one instead of losing the whole
// comparison, and the job id in the URL means this page reopened later (or
// on another device) picks the result back up.
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

// Primary transport: the server pushes a full snapshot over /ws/compare/<id>
// on connect and again every time a new result lands (see rt_jobs.py's
// JobStore.notify), so the page just waits instead of asking on a timer.
function watchJob(jobId) {
  closeSocket();
  stopPolling();
  setControlsDisabled(true);
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/ws/compare/' + jobId);
  socket = ws;
  let gotAnyMessage = false;
  ws.onmessage = (event) => {
    gotAnyMessage = true;
    const data = JSON.parse(event.data);
    if (data.code === 'not_found') {
      statusEl.textContent = 'Результат не найден (сервер перезапускался или прошло много времени) — начните новое сравнение.';
      setControlsDisabled(false);
      closeSocket();
      return;
    }
    applyJobSnapshot(data);
    if (data.status !== 'running') closeSocket();
  };
  ws.onerror = () => {
    if (!gotAnyMessage) {
      // Couldn't even open the socket (proxy/browser issue) -- fall back
      // to polling instead of leaving the page stuck.
      pollJob(jobId);
    }
  };
  ws.onclose = () => {
    if (socket === ws) socket = null;
  };
}

async function startCompareJob(body, headers) {
  closeSocket();
  resultsEl.innerHTML = '';
  filledKeys.clear();
  statusEl.textContent = 'Отправляю…';
  setControlsDisabled(true);
  try {
    // No manual knobs on this page -- transpose is always auto-computed
    // server-side (see rt_server.py's TARGET_MEDIAN_F0_HZ); formant/
    // index_rate are left at their defaults (untouched, not auto-applied).
    const response = await fetch('/api/compare?transpose=auto', {method: 'POST', headers, body});
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
    watchJob(data.jobId);
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
    watchJob(jobId);
  }
})();
