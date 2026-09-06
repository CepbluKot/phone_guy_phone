# Phone Guy: начать здесь

Обновлено **2026-09-06 после реального развёртывания**.
**Проект уже работает. Не разрабатывать и не развёртывать заново без новой задачи.**

## 1. Что сейчас работает

Сайт: https://voice.lan.awesomeio.ru/ через VPN.
Единственный голос — PhoneGuyfnaf1V1 (RVC). Пользователь одобрил модель.
Интерфейс: «Начать», «Остановить», уровни входа/выхода, дополнительная задержка
0–10с (default5). Старый эффект, его настройки, выбор профиля и тестовый тон удалены
из интерфейса. Старый образ сохранён исключительно для аварийного отката.

Надеть наушники → обновить страницу → «Начать» → разрешить микрофон →
говорить непрерывно. При default5 первый звук в синтетическом worklet-тесте
появился через8.08с: накопление/обработка добавляются к выбранным5с.
Это непрерывная обработка кусками, не ожидание окончания фразы и не <150мс.
Stop немедленно выключает захват/выход и отбрасывает незавершённую речь.

Полная фактическая приёмка, версия, результаты и ограничения:
[LIVE_STATUS.md](LIVE_STATUS.md).

## 2. Где код и где сервер

| Объект | Точный адрес |
| --- | --- |
| Рабочая ветка | feature/rvc-streaming |
| Код/доки на ноутбуке | /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming |
| Основной checkout с исследованиями | /home/oleg/Documents/voice-changer |
| Финальный код | 41d8a28; последующие doc-only коммиты не требуют доставки |
| VM для выполнения | VM209, ubuntu@192.168.20.70 |
| RVC release | /opt/voice-rvc/releases/20260906T161258Z |
| Активная ссылка | /opt/voice-rvc/current → этот release |
| Python окружение | /opt/voice-rvc/venv |
| Служба модели | voice-rvc.service, active и enabled |
| HTTP/static контейнер | voice-changer-voice-1, /opt/voice-changer |
| Rollback snapshot | /opt/voice-rvc/backups/20260906T161258Z |

Ветка не слита с main и не отправлена в remote. Основной checkout содержит
незакоммиченные исследования; их не удалять/не перезаписывать.
Открытый в IDE proactive-monitoring — другой проект, не трогать.

## 3. Сеть — не менять URL на location.host

```text
Браузер -- HTTPS voice.lan.awesomeio.ru --> VPN VPS10.19.87.1 --> VM209:8080 (UI)
Браузер -- WSS vm-voice-1.lan.awesomeio.ru --> VM209 Caddy:443
                                                     /ws/rvc --> 127.0.0.1:8090
```

Главный домен идёт через VPS прямо в HTTP-контейнер, минуя native Caddy.
Поэтому клиент намеренно использует фиксированный native WSS URL.
8090 слушает только loopback. Оба HTTPS health относятся к HTTP-сервису,
а готовность RVC проверяется отдельно на VM через127.0.0.1:8090/healthz.
Старый /ws/audio остаётся в legacy backend, но новый клиент его не использует.
Удаление UI не было переписыванием HTTP/backend-инфраструктуры.

## 4. Карта файлов

- web/index.html — только одобренные элементы управления.
- web/app.js — микрофон, протокол RVC, статусы, ошибки, Stop. Assets v6.
- web/audio-worklet.js —20мс захват и ограниченная очередь воспроизведения.
- rvc_service/chunks.py — сборка окон, точные sample counts, склейка.
- rvc_service/engine.py — однократная загрузка/прогрев модели, только VM/GPU.
- rvc_service/server.py — FastAPI/WebSocket, единственная активная сессия.
- deploy/voice-rvc.service — отдельный системный пользователь и ограничения.
- deploy/Caddyfile — приватный маршрут, остальные HTTP-запросы в контейнер.
- deploy/deploy-rvc.sh — доставка и точный проверяемый откат.
- tests/live-rvc.py — воспроизводимый paced-тест RU/EN через private WSS.
- tests/test_rvc_*.py, test_deploy_rvc.py, test_live_rvc_tool.py — unit-гейты.
- tests/client.test.cjs и audio-worklet.test.cjs — браузерная логика без GPU.
- [Спецификация](superpowers/specs/2026-09-06-rvc-streaming-design.md) —
  читать верхнюю AI-only поправку: она отменяет старые требования DSP fallback.
- [История приёмки](RVC_ACCEPTANCE_2026-09-06.md) — хронология, не список TODO.
- [Эксплуатация](OPERATIONS.md) — команды доставки/диагностики/отката.

## 5. Протокол и числа, которые нельзя случайно изменить

PCM16 little-endian, mono48kHz. Входной кадр960 отсчётов=1920байт=20мс.
Hop2с=96000 отсчётов; контекст.5с и lookahead.1с; окно124800 отсчётов.
Первый результат после105 входных кадров, следующие каждые100.
Выходной бинарный пакет всегда192000байт. Склейка40мс, поиск±20мс.

Модель: speaker0, pitch0, RMVPE, index.6, protect.33, RMS1, FP32.
Выход модели32kHz приводится к48kHz. Без дополнительного телефонного фильтра.
Одна активная RVC-сессия, один GPU-вызов и максимум одно ожидающее окно.
Перегрузка завершает сессию явно, не растит очередь бесконечно.
Worklet queue≤12с, стартовый jitter reserve250мс, additional delay0–10с.

Start:
```json
{"type":"start","version":1,"sampleRate":48000,"channels":1,"sampleFormat":"s16le"}
```

Нужен Origin одного из двух приватных HTTPS-доменов. Отправлять PCM после ready.
Перед каждым выходом приходит metrics:
```json
{"type":"metrics","outputStart":0,"outputSamples":96000,"consumedSamples":96000,"processingMs":700}
```
700 — пример. Следующие outputStart96000, consumedSamples192000.
Это cumulative sample positions, НЕ номера входных пакетов. Вход/выход не1:1.
Stop: {"type":"stop"} → {"type":"stopped"}.
Ошибки: busy, invalid_start, invalid_frame, overloaded, model_unavailable, stalled.

preparing: ожидание разрешения микрофона, сетевой таймер ещё не запущен.
connecting:10с от создания WebSocket; warming:90с; ready:10с без прогресса.
После Stop результат текущего GPU-вызова игнорируется; новую сессию допускают
только после его завершения. Нельзя пропускать старую речь в новую сессию.
ArrayBuffer после передачи через transfer list уже detached: уровень
считать ДО передачи. Регрессия проверяет настоящий structuredClone transfer.

## 6. Модель, зависимости и ограничения VM

Assets root: /opt/voice-changer/experiments/phoneguy.
Внутри: models/PhoneGuyfnaf1V1.pth,
models/added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index,
upstream/assets/hubert_base, upstream/assets/rmvpe/rmvpe.pt.
Upstream pinned81eed5e8f68b6bed1789f682fe78cdd324495afc.
ZIP SHA2561ff846db9b9ab508b15dff3891da113d004047a378662e2acfe1de0abff109de.

VM4GiB, GTX1050Ti4GiB, voice-rvc memory2700MiB/swap0/CPU250%/один worker.
Рабочий venv принадлежит ubuntu:ubuntu и используется службой для чтения.
deploy проверяет все версии по rvc_service/requirements.lock и pip check;
при несовпадении останавливается, не чинит shared venv автоматически.
Этот lock без хешей — не путать с корневым hash-locked DSP requirements.lock.
TORCH_FORCE_WEIGHTS_ONLY_LOAD=1 и offline-настройки сохранять.
На ноутбуке .venv — ссылка на unit-test окружение; Engine здесь не запускать.

## 7. Первый шаг нового агента — сверить, а не менять

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
git status --short
git log -5 --oneline
.venv/bin/pytest -q
node --test tests/*.test.cjs
ssh -o BatchMode=yes -o ConnectTimeout=8 ubuntu@192.168.20.70 'curl -fsS http://127.0.0.1:8090/healthz'
ssh ubuntu@192.168.20.70 'systemctl is-active voice-rvc caddy; systemctl is-enabled voice-rvc; readlink /opt/voice-rvc/current'
curl -fsS https://voice.lan.awesomeio.ru/healthz
```

Ожидается73Python/20Node passing,2 известных dependency deprecation warnings.
Без открытого браузерного сеанса RVC health: ready/activefalse/runningfalse/queue0.
503warming при перезапуске допустим до90с. 503model_unavailable — проблема.
Никаких дополнительных деплоев только ради проверки: сначала понять запрос.

## 8. Если тишина

| Наблюдение | Что проверить |
| --- | --- |
| Старые ползунки на сайте | Обновить вкладку; cachev6; сверить deployed assets |
| Вход0 | Разрешение/выбранный микрофон/mute |
| Вход есть, выхода нет | Ready/error, nativeWSS, RVC health,2с пакеты |
| Выход есть, звука нет | Наушники/устройство вывода/mute вкладки; учесть extra5с |
| busy | Другая вкладка или незавершившийся GPU-вызов после Stop |
| overloaded | Реальные inference timings/queue/network; не увеличивать буферы вслепую |

Журнал ограничивать: ssh ubuntu@192.168.20.70 'sudo journalctl -u voice-rvc -n 60 --no-pager'.
Не сохранять PCM/транскрипты пользователя. Known minor: caught empty-F0 traceback
при нулевом прогреве upstream; он не препятствует подтверждённой работе.

## 9. Что осталось и что уже НЕ надо делать

Развёртывание, private-WSS5мин, restart/reconnect, откат и browser Start/Stop/delay
**уже проверены**. Код прошёл task- и whole-branch review с устранением P2.
Осталось субъективное подтверждение пользователем качества/швов на его речи.
Отказ в доступе к микрофону проверен unit-тестом, не отдельным browser-denial сценарием.
Физическая акустическая задержка не измерялась; численные/виртуальные тесты — не она.
Не обещать гарантированную незаметность швов по числам; сохранены synthetic samples.

Не менять Frigate/VM208, GPU passthrough, ресурсы VM, DNS/VPS/firewall.
Не публиковать8090 наружу, не отправлять речь в облако, не запускать inference на ноутбуке.
Не применять git reset --hard/git clean -fdx/массовые удаления.
Не использовать старый deploy/deploy.sh — только scoped скрипт.
Не удалять основной checkout/эксперименты/worktree. Секреты в Git не переносить.

## 10. Запрос для следующей LLM

> Продолжи по новой задаче пользователя в
> /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming.
> Сначала прочитай docs/HANDOFF.md и docs/LIVE_STATUS.md.
> AI-only Phone Guy уже развёрнут, код41d8a28, release20260906T161258Z;
> не повторяй завершённый план. Начни с read-only сверки, сохрани dirty files.
> Inference только VM209, Frigate/сеть/ресурсы не трогать.
> Для изменения сначала тест, затем scoped delivery/rollback и живое подтверждение.

Исторические материалы: [предыдущий handoff](HANDOFF_PREDEPLOY_2026-09-06.md),
план и .superpowers/sdd/2026-09-06-rvc-streaming в worktree.
Они сохранены для разбора решений; актуальный runtime описан выше и в LIVE_STATUS.
