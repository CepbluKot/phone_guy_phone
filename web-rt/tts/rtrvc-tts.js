'use strict';

const POLL_MS = 1000;
const el = id => document.getElementById(id);
const goButton = el('go'), statusEl = el('status'), player = el('player');
const ERRORS = {
  empty_text: 'Введите текст.',
  text_too_long: 'Слишком длинный текст (максимум 400 символов).',
  tts_unavailable: 'Синтез речи недоступен на сервере.',
  tts_timeout: 'Синтез занял слишком много времени, попробуйте текст короче.',
  tts_failed: 'Синтез не удался.',
  model_unavailable: 'Модель голоса ещё не готова, подождите и повторите.',
  invalid_request: 'Некорректный запрос.',
  internal_error: 'Внутренняя ошибка сервера.',
};
let pollTimer = null;
let socket = null;

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

function closeSocket() {
  if (socket) { try { socket.close(); } catch {} socket = null; }
}

function showResult(data) {
  if (el('text') && typeof data.text === 'string') el('text').value = data.text;
  if (data.lang) el('lang').value = data.lang;
  player.src = 'data:audio/wav;base64,' + data.wavBase64;
  player.hidden = false;
  statusEl.textContent = 'Готово. Ссылку из адресной строки можно сохранить, чтобы прослушать этот результат позже.';
  goButton.disabled = false;
  player.play().catch(() => {});
}

function applyJobSnapshot(data) {
  if (data.status === 'running') {
    statusEl.textContent = 'Озвучиваю… (Piper, затем RVC — может занять несколько секунд)';
  } else if (data.status === 'done') {
    showResult(data);
  } else {
    statusEl.textContent = ERRORS[data.error] || ('Ошибка: ' + data.error);
    goButton.disabled = false;
  }
}

// Polling fallback for GET /api/tts/<jobId> -- only used if the websocket
// below can't even connect. A dropped tick just retries on the next one
// instead of losing the synthesis, and the job id in the URL means this
// page reopened later (or on another device) shows the same result again.
async function pollJob(jobId) {
  stopPolling();
  const tick = async () => {
    try {
      const response = await fetch('/api/tts/' + jobId, {cache: 'no-store'});
      if (response.status === 404) {
        statusEl.textContent = 'Результат не найден (сервер перезапускался или прошло много времени) — озвучьте заново.';
        goButton.disabled = false;
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

// Primary transport: the server pushes a snapshot over /ws/tts/<id> on
// connect and again when the job finishes, instead of asking on a timer.
function watchJob(jobId) {
  closeSocket();
  stopPolling();
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/ws/tts/' + jobId);
  socket = ws;
  let gotAnyMessage = false;
  ws.onmessage = (event) => {
    gotAnyMessage = true;
    const data = JSON.parse(event.data);
    if (data.code === 'not_found') {
      statusEl.textContent = 'Результат не найден (сервер перезапускался или прошло много времени) — озвучьте заново.';
      goButton.disabled = false;
      closeSocket();
      return;
    }
    applyJobSnapshot(data);
    if (data.status !== 'running') closeSocket();
  };
  ws.onerror = () => {
    if (!gotAnyMessage) pollJob(jobId);
  };
  ws.onclose = () => {
    if (socket === ws) socket = null;
  };
}

goButton.onclick = async () => {
  const text = el('text').value;
  const lang = el('lang').value;
  goButton.disabled = true;
  player.hidden = true;
  statusEl.textContent = 'Отправляю…';
  try {
    const response = await fetch('/api/tts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, lang}),
    });
    if (!response.ok) {
      let code = 'invalid_request';
      try { code = (await response.json()).code || code; } catch {}
      throw new Error(ERRORS[code] || ('Ошибка: ' + code));
    }
    const data = await response.json();
    const url = new URL(location.href);
    url.searchParams.set('job', data.jobId);
    history.pushState({job: data.jobId}, '', url);
    watchJob(data.jobId);
  } catch (error) {
    statusEl.textContent = error.message || 'Ошибка синтеза.';
    goButton.disabled = false;
  }
};

(function resumeFromUrl() {
  const jobId = new URLSearchParams(location.search).get('job');
  if (jobId) {
    goButton.disabled = true;
    statusEl.textContent = 'Загружаю сохранённый результат…';
    watchJob(jobId);
  }
})();
