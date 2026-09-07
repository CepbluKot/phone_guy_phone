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

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
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

// Polls GET /api/tts/<jobId> instead of blocking one fetch on the whole
// Piper+RVC pipeline (see rvc_service/rt_jobs.py) -- a dropped connection
// just fails one poll tick, not the synthesis, and the job id in the URL
// means this page reopened later shows the same result again.
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
      if (data.status === 'running') {
        statusEl.textContent = 'Озвучиваю… (Piper, затем RVC — может занять несколько секунд)';
        pollTimer = setTimeout(tick, POLL_MS);
      } else if (data.status === 'done') {
        showResult(data);
      } else {
        statusEl.textContent = ERRORS[data.error] || ('Ошибка: ' + data.error);
        goButton.disabled = false;
      }
    } catch {
      pollTimer = setTimeout(tick, POLL_MS);
    }
  };
  await tick();
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
    pollJob(data.jobId);
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
    pollJob(jobId);
  }
})();
