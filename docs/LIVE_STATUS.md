# Текущее развёртывание Phone Guy

## Актуальная проверка браузерного телефона — 2026-09-29

Прямой запрос к Go API на VM209 вернул `healthz: ok`. Каталог вернул
физические телефоны `1983` и `1988` в сети; браузерные номера `345` и `3454`
сейчас не подключены (`active: false`). Поэтому в этой проверке реальный
звонок между браузерами не выполнялся и не считается пройденным.

После изменений SIP, входящего вызова, завершения разговора или WebRTC-аудио
обязателен live acceptance между двумя независимыми браузерными сессиями:
[инструкция и форма результата](BROWSER_CALL_ACCEPTANCE.md). Сборка и unit-тесты
не подтверждают этот сценарий.

Обновлено 2026-09-06, после живых проверок. **AI-only версия развёрнута**:
https://voice.lan.awesomeio.ru/ (VPN). Код на ноутбуке, обработка на VM209.
Старый эффект/настройки/тестовый тон из интерфейса удалены.

## Версия и восстановление

- Финальный код: `41d8a28`, ветка `feature/rvc-streaming`.
- Release: `/opt/voice-rvc/releases/20260906T161258Z`.
- `/opt/voice-rvc/current` указывает на этот release.
- `voice-rvc.service`: active, enabled; listener только `127.0.0.1:8090`.
- UI image: `sha256:40dd5399de0c9445ed4ce5839b8f1016b48bd783c6d56e59a097a8fcd6776c7e`.
- UI assets v6; SHA256 app/index/worklet совпали на ноутбуке и VM.
- Backup: `/opt/voice-rvc/backups/20260906T161258Z`.
- Старый образ: `voice-changer:rollback-20260906T161258Z` (DSP; только аварийный возврат).

Команда **изменяет сервер**, запускать только для намеренного отката:

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
bash deploy/deploy-rvc.sh rollback 20260906T161258Z
```

Реальный rollback exercise выполнен для первого развёртывания
`20260906T160404Z`: exit0, исходный image
`0a70cadc0e291e4b2cecfb8673d9de68f44f59cecbc29a02f3bad322b71ec240`,
старый web/Caddy, release4ba8ab9 и disabled-state восстановились и были проверены.
После этого выполнено финальное развёртывание `20260906T161258Z`, exit0.
Точное состояние каждого snapshot хранится рядом с ним; не смешивать их файлы.

## Go-админка и браузерный телефон

На 2026-09-26 Go admin и `/phone/` обновлены на VM209, release
`20260926T180933Z`; контейнеры `voice-go` и `voice-conference-asterisk-1`
healthy. Asterisk 22.11.0 загружает PJSIP WebSocket transport; WSS доступен
только через частный VM Caddy `/ws/phone-signaling`, ARI остаётся на loopback.
Проверена живая регистрация SIP.js с сайта `voice.lan`, затем сессия отозвана:
каталог профилей восстановлен, активных browser-сессий и каналов нет, контакты
физических аппаратов `1983` и `1988` остались доступны.

Реальный звонок, DTLS-SRTP/RTP аудио, параллельный ответ и RVC во время звонка
пока не проверены, так как это потребовало бы прозвонить физические аппараты.
Точку возврата этого update хранит `/opt/voice-go/updates/20260926T180933Z`;
инструкция в [OPERATIONS.md](OPERATIONS.md#браузерный-внутренний-телефон).

## Проверки

| Проверка | Фактический результат |
| --- | --- |
| Unit Python | 73 passed,2 известных dependency deprecation warnings |
| Unit Node | 20 passed,0 failed |
| Review | Все task-гейты и final review пройдены после исправлений |
| Caddy | Конфигурация проверена на VM до reload |
| Приватные HTTPS health | Оба200, status ok |
| WSS smoke / malformed / reconnect | Пройдены до и после worker restart |
| Повторный worker restart | Прогрев и новая конверсия успешны |
| Rollback | Реальный возврат старой версии прошёл, затем новый deploy |
| Browser | Start, mic capture, выход, delay5→0, Stop, restart, delay5 пройдены |

### Пять минут через настоящий private WSS

Синтетические RU/EN Piper fixtures, включая короткие паузы, без пользовательской
записи. Завершено16:12:13UTC. Выполнено на первом release; финальная поправка
затронула только подготовку микрофона в браузере, backend/worklet не менялись.
На окончательном release повторены WSS10с и2с после restart, а также worklet/browser.

- Отправлено15005 входных кадров, получено150 упорядоченных hops.
- Выход14400000 отсчётов48kHz, ровно300с; все150hops non-silent, finite PCM.
- Wall300.939с; processing p50=685.584мс, p95=706.927мс на2с речи.
- RTF p95=.3535 (время обработки / длительность звука).
- Лаг поступления относительно конца входного hop .7666–.8236с.
- Изменение лага к концу −.0020с: накопления задержки не обнаружено.
- Max outstanding1hop, финально0; transport write buffer0.
- По29 runtime-снимкам queue0, RAM peak1527885824байт, GPU max1590MiB,
  serviceRestarts0. Ограничения VM/Frigate не менялись.
-149 швов; максимальный sample-step .16815, общий p99 .09488.
  Это НЕ сертификат отсутствия слышимых артефактов.

Повторить тест, только когда нет пользовательской аудиосессии:

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
.venv/bin/python tests/live-rvc.py --seconds 300
```

Сохранены только синтетические материалы в основном checkout:
`experiments/phoneguy/outputs/private-wss-20260906/`:
`private-wss-input.wav`, `private-wss-output.wav`, `private-wss-seams.wav`.

### Воспроизведение и браузер

Scratch `.superpowers/sdd/2026-09-06-rvc-streaming/live-worklet.cjs 30`
использовал настоящий AudioWorklet-код, synthetic RU, реальные WSS/GPU и
виртуальный48kHz render clock на ноутбуке. Только inference выполнялся на VM.
1505frames→15hops;0underruns/0drops; maxQueue2248.69мс; processingp95=709.308мс.
Первый PCM-пакет через2.783с; первый ненулевой воспроизведённый отсчёт через8.079с
при additional5с. Это программное измерение, не физическая акустическая задержка.

Реальный in-app browser, главный домен: после разрешения mic ready, вход/выход
реагировали; первая сессия3317 входных кадров/32hops,0interruptions/0drops.
Смена5→0 очищала старый отложенный звук. Stop обнулял уровни и возвращал Start.
Повторный запуск с5с:1666frames/16hops,0interruptions/0drops. Остановлен после
проверки; микрофон не оставлен активным. Browser error/warn log пуст.
Микрофон пользователя в файл не записывался.

## Что не утверждаем

- Сходство и слышимость швов на живой речи окончательно оценивает пользователь.
- Физическая microphone-to-speaker latency не измерялась.
- Отказ в mic permission покрыт unit-тестом, отдельно в живом браузере не вызывался.
- <150мс, виртуальный микрофон Discord/игр, обучение модели не входят в задачу.
- Legacy DSP/backend код ещё существует как часть прежнего HTTP-хоста;
  AI-only клиент не имеет переключателя или автоматического fallback.

Known non-blocking: upstream empty-F0 traceback при прогреве нулями;
Starlette/AnyIO deprecations; Caddy formatting/OCSP/disabled-redirect warnings;
Docker legacy-builder warning. Ошибками загрузки модели они в прогонах не стали.

## Conference demo status

Слушательская Asterisk-конференция развёрнута на VM209, активный релиз
`20260906T231500Z`. Страница для пользователя:
`https://voice.lan.awesomeio.ru/conference/` (только через VPN). Она только
проигрывает микс: microphone policy запрещена, `getUserMedia` и аудиозагрузка
отсутствуют. Полная эксплуатационная схема, rollback и живые метрики — в
[CONFERENCE.md](CONFERENCE.md). Не трогать Frigate, DNS/VPS/firewall и
`voice-rvc.service` ради этой функции.

## Следующий исполнитель

Прочитать [HANDOFF.md](HANDOFF.md), сверить runtime read-only, затем выполнять
новую задачу. Завершённое развёртывание не повторять автоматически. Исходные
исследования и журнал сохранены; локальный main не объединён с рабочей веткой.
GitHub-публикация: https://github.com/CepbluKot/phone_guy_phone, ветка main.
