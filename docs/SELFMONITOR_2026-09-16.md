# Self-monitor: эхо-линия Phone Guy (1999)

Дата: 2026-09-16. Ветка: `main` (`selfmonitor/`, `deploy/deploy-selfmonitor.sh`).

## Что это

Набери **1999** с любого зарегистрированного SIP-телефона — в трубке звучит
твой собственный живой голос, преобразованный Phone Guy'ем (GPT v2, `/ws/rvc-v2`),
без дополнительной задержки (~1.7 с — накопление окна 1.1 с + обработка ~0.6 с).
Браузерный аналог без телефона: `https://vm-voice-1.lan.awesomeio.ru/live/`
(там же есть ползунок доп. задержки 0–10 с).

## Как устроено

Отдельный процесс `voice-selfmonitor.service` на VM209 (host network), своё
ARI-приложение `selfmonitor` — контроллер конференции (`phoneguy-sip`) этих
каналов не видит. На звонок:

```
caller ── mixing bridge "echo" ── injection (chan_websocket)
   │                                   ▲
   │                                   │ 20 мс PCM
   snoop(spy=in)                       │
   └── mixing bridge "source" ── listener (chan_websocket)
```

- Snoop-канал копирует речь звонящего, не входя ни в один мост с обработанным
  звуком — обратной связи нет структурно.
- Mixing-мост не возвращает каналу его собственный звук, поэтому звонящий
  слышит только обработанный Voice Guy, без своего raw-голоса (fail-closed).
- `listener → RvcStream (v2) → injection`; выходные блоки 1 с режутся в
  20 мс кады с pacing 20 мс (переиспользованы `conference/rvc.py` + `media.py`).
- Одновременно — один звонок: второму сразу `busy` (GUP-воркер всё равно один).

## Эксплуатация

- Деплой/обновление: `bash deploy/deploy-selfmonitor.sh` (idempotent; бэкапит
  extensions.conf, никогда не перезапускает asterisk/voice-rvc/контейнеры).
- Откат: команда в конце вывода скрипта.
- Пароль ARI берётся из активного `ari.conf` конференции в
  `/etc/voice-selfmonitor.env` (0600). Если конференция передеплоится со
  сменой пароля — перезапусти скрипт.
- Если в трубке слышно «двойное» преобразование вместо своего голоса —
  направление snoop перевёрнуто на этой версии Asterisk: добавь
  `SELFMONITOR_SPY=out` в `/etc/voice-selfmonitor.env` и перезапусти сервис.

## Проверено

- 4 unit-теста (`tests/test_selfmonitor.py`): порядок монтажа мостов и snoop,
  перекачка речи и блоков, busy на второй звонок, teardown без passthrough.
- Живое: сервис healthy, приложение `selfmonitor` в `ari show apps`,
  dialplan `1999@phoneguy-sip → Stasis(selfmonitor)`.
- Живой звонок с телефона — за пользователем (телефон 1983 на момент деплоя
  был офлайн).
