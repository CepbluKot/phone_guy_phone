'use strict';

const el = id => document.getElementById(id);
const goButton = el('go'), statusEl = el('status'), player = el('player');
const ERRORS = {
  empty_text: 'Введите текст.',
  text_too_long: 'Слишком длинный текст (максимум 400 символов).',
  tts_unavailable: 'Синтез речи недоступен на сервере.',
  tts_timeout: 'Синтез занял слишком много времени, попробуйте текст короче.',
  model_unavailable: 'Модель голоса ещё не готова, подождите и повторите.',
  invalid_request: 'Некорректный запрос.',
};

goButton.onclick = async () => {
  const text = el('text').value;
  const lang = el('lang').value;
  goButton.disabled = true;
  player.hidden = true;
  statusEl.textContent = 'Озвучиваю… (Piper, затем RVC — может занять несколько секунд)';
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
    const blob = await response.blob();
    player.src = URL.createObjectURL(blob);
    player.hidden = false;
    statusEl.textContent = 'Готово.';
    player.play().catch(() => {});
  } catch (error) {
    statusEl.textContent = error.message || 'Ошибка синтеза.';
  } finally {
    goButton.disabled = false;
  }
};
