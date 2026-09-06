# Исторический снимок до развёртывания — НЕ текущая инструкция

Текущее состояние: [HANDOFF.md](HANDOFF.md) и [LIVE_STATUS.md](LIVE_STATUS.md).
Ниже сохранён прежний текст для истории; его pending-гейты уже не являются текущими.

# Phone Guy: инструкция продолжения для нового исполнителя

Дата снимка: **2026-09-06**. Это фактическая передача работы, не заявление о
готовности релиза. Все сведения о сервере перед изменениями перепроверять.

## 1. Что пользователь хочет

Говорить в микрофон браузера и непрерывно слышать свой голос, преобразованный
моделью PhoneGuyfnaf1V1. Пользователь уже одобрил качество отдельного теста
модели. Требуется интегрировать именно её, а не заменить телефонным фильтром.

Код и документы держать на ноутбуке; обработку запускать на выделенной VM209.
Сайт доступен через VPN. Дополнительная задержка регулируется от 0 до 10 с,
по умолчанию 5 с. Это НЕ ожидание конца фразы и НЕ полная задержка системы.
Требование менее 150 мс снято. Виртуальный микрофон Discord/игр не входит в задачу.

## 2. Где работать и что прочитать

1. Рабочий каталог: `/home/oleg/Documents/voice-changer/.worktrees/rvc-streaming`.
2. Этот документ — состояние и безопасная последовательность действий.
3. [Утверждённая спецификация](superpowers/specs/2026-09-06-rvc-streaming-design.md)
   — требования; при противоречии с планом приоритет у спецификации.
4. [План](superpowers/plans/2026-09-06-rvc-streaming.md) — четыре этапа.
5. [Фактические проверки](RVC_ACCEPTANCE_2026-09-06.md) — что действительно измерили.
6. `.superpowers/sdd/2026-09-06-rvc-streaming/progress.md` внутри worktree —
   журнал этапов, исправлений и проверок. Каталог игнорируется Git, но существует
   на ноутбуке. Если его нет, восстанавливать состояние по Git и этой инструкции.

Основной checkout `/home/oleg/Documents/voice-changer` — ветка `main`.
Рабочий worktree — `feature/rvc-streaming`. Не путать с открытым в IDE
`proactive-monitoring`: это другой проект, его не изменять.

## 3. Точное состояние: код не равен развёртыванию

| Компонент | Код на ноутбуке | Состояние сервера на дату снимка |
| --- | --- | --- |
| Старый телефонный DSP | `app/` | Работает в Docker; текущий сайт использует его |
| Сборка/склейка RVC-аудио | `rvc_service/chunks.py` | Работает в новом native-сервисе |
| Загрузка модели | `rvc_service/engine.py` | Модель загружена и прогрета на GPU |
| RVC WebSocket | `rvc_service/server.py` | Только `127.0.0.1:8090`, извне недоступен |
| Новый AI-only UI | `web/` | Написан и проверен unit-тестами, НЕ доставлен |
| Caddy `/ws/rvc` | Добавлен в source | Маршрут на VM ещё НЕ подключён |
| Безопасный RVC-деплой | `deploy/deploy-rvc.sh` реализован и unit-проверен | Ещё не запускался |
| Повторяемый private-WSS тест | `tests/live-rvc.py` реализован и unit-проверен | Ещё не запускался через private WSS |

Последний проверенный HEAD: `f7f1579`. Выполнены и прошли review этапы 1–3:

- Engine/chunks: `ed9e2d8`, исправление cwd `7a57a63`.
- WebSocket/service: `19d8307`, исправления отмены/Stop `4ba8ab9`.
- UI/worklet: `4f35650`, исправления transferable-буфера/таймаута `f7f1579`.

**Продолжать с live-гейтов этапа 4**, не выполнять этапы 1–3 заново.
Scoped rollout и acceptance tooling уже находятся в worktree; они требуют
независимой проверки и фактического запуска контроллером до release-выводов.

## 4. Сеть: важная особенность

```text
Браузер -- HTTPS voice.lan.awesomeio.ru --> VPN VPS --> VM209:8080 (UI/DSP)
Браузер -- WSS vm-voice-1.lan.awesomeio.ru --> VM209 Caddy:443
                                                   |-- /ws/audio --> VM209:8080
                                                   `-- /ws/rvc --> 127.0.0.1:8090
                                                       ПОКА НЕ ПОДКЛЮЧЕНО
```

Главный домен разрешается в `10.19.87.1`; native-домен — `192.168.20.70`.
Прокси главного домена идёт прямо к Docker, минуя Caddy VM.
Поэтому в новом клиенте оба аудиорежима используют фиксированный native-домен.
**Не заменять его на `location.host`: главный домен не знает RVC-маршрут.**
Публичный DNS, VPS-прокси и сеть менять для этого этапа не требуется.

## 5. Сервер и файлы

SSH: `ubuntu@192.168.20.70`, sudo без пароля; секреты в документы не копировать.

| Путь/объект | Назначение |
| --- | --- |
| `/opt/voice-changer` | Старое production-приложение и отдельные эксперименты |
| `voice-changer-voice-1` | Docker-контейнер DSP/UI, образ `voice-changer:current` |
| `/opt/voice-rvc/venv` | Отдельное готовое Python3.12 окружение RVC |
| `/opt/voice-rvc/current` | Symlink на `/opt/voice-rvc/releases/4ba8ab9` |
| `/etc/systemd/system/voice-rvc.service` | Новый сервис: active, но НЕ enabled |
| `/etc/caddy/Caddyfile` | Текущий reverse proxy без RVC-маршрута |
| `/opt/voice-changer/experiments/phoneguy` | Read-only assets для inference |
| `/opt/voice-rvc/smoke-output` | Только синтетические тестовые WAV |

VM209: 4 GiB RAM; GTX1050Ti4GiB. Сервис voice-rvc: отдельный непривилегированный
пользователь, группы video/render, CPU250%, память2700MiB, swap0, один worker.
GPU-зависимости не добавлять в небольшой DSP-контейнер.

Модель в `models/PhoneGuyfnaf1V1.pth`, индекс
`models/added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index` относительно assets root.
HuBERT находится в `upstream/assets/hubert_base`, RMVPE — в `upstream/assets/rmvpe`.
Upstream commit: `81eed5e8f68b6bed1789f682fe78cdd324495afc`.
SHA256 исходного ZIP: `1ff846db9b9ab508b15dff3891da113d004047a378662e2acfe1de0abff109de`.

`rvc_service/requirements.lock` — экспорт точных версий проверенного runtime,
без хешей; это НЕ корневой hash-locked файл DSP. Для PyTorch нужен cu118 index,
указанный в заголовке. Venv принадлежит `ubuntu:ubuntu`, mode775; scoped deploy
проверяет pins read-only и fail-closed, но не пишет marker/chown/pip install.
Не переустанавливать уже рабочее окружение без отдельного плана и rollback.
Не отключать `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`, не заменять модель случайным архивом.

## 6. Как устроен аудиопоток

DSP — обычные звуковые эффекты. RVC — нейросетевая замена тембра.
«Отсчёт» — одно числовое значение звука; «кадр» — 20 мс входа;
«hop» — 2 секунды нового выходного звука; «окно» — hop с соседним контекстом.

Вход: mono48kHz, PCM16 little-endian, 960 отсчётов = 1920 байт на кадр.
RVC ждёт 2 с речи и .1 с lookahead; добавляет .5 с прошлого контекста.
Внутреннее окно: 124800 отсчётов (2.6 с). Каждый выход: ровно 96000 отсчётов
(2 с / 192000 байт). После первого окна следующие приходят каждые 100 кадров.
Склейка: 40 мс overlap/crossfade, поиск выравнивания ±20 мс.

Пресет фиксирован: speaker0, pitch0, RMVPE, index .6, protect .33, RMS1, FP32.
Никакого дополнительного телефонного фильтра по умолчанию.
Максимум одна активная RVC-сессия, одна выполняемая конверсия и одно ожидающее окно.
Перегрузка — явная ошибка, не бесконечный рост очереди и не скрытый переход на DSP.

Worklet принимает двухсекундные пакеты; предел очереди12с, стартовый запас250мс.
Затем добавляется выбранная задержка5с. Ожидаемый полный лаг порядка8с при default,
но физически он ещё НЕ измерен. UI показывает оценку, не акустическое измерение.
Stop очищает воспроизведение сразу, остаток незаконченной речи отбрасывается.
Выполняющийся GPU-вызов завершается, результат игнорируется; лишь затем новая сессия.

## 7. Протокол, чтобы не сломать клиент

WebSocket `/ws/rvc` требует Origin одного из двух приватных HTTPS-доменов.
Первое сообщение клиента:

```json
{"type":"start","version":1,"sampleRate":48000,"channels":1,"sampleFormat":"s16le"}
```

Сервер сначала может вернуть `warming` (лимит90с), затем `ready` с теми же
параметрами и `frameBytes:1920`, `outputSamples:96000`.
В фазе подключения клиент имеет отдельный10с таймаут; после ready отсутствие
полезного прогресса ограничено10с. Вход отправлять только после ready.

Перед каждым бинарным выходом приходит JSON:

```json
{"type":"metrics","outputStart":0,"outputSamples":96000,"consumedSamples":96000,"processingMs":700}
```

700 — пример, не фиксированная величина. Следующий `outputStart` равен96000,
`consumedSamples` равен192000. Это индексы отсчётов, не номера входных кадров.
**Не требовать равенства количества отправленных и полученных пакетов в RVC.**
Stop: `{"type":"stop"}` → `{"type":"stopped"}`.
Ошибки: `busy`, `invalid_start`, `invalid_frame`, `overloaded`,
`model_unavailable`, `stalled`; JSON содержит type/error, code и message.
DSP остаётся на `/ws/audio` со старым протоколом.

Не читать ArrayBuffer после передачи в worklet через transfer list:
он уже detached. Такой дефект исправлен в `f7f1579`, тест использует реальный
`structuredClone(..., {transfer})`, а не мягкий mock.

## 8. С чего начать следующий запуск: только чтение

На ноутбуке:

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
git status --short
git log -8 --oneline
.venv/bin/pytest -q
node --test tests/*.test.cjs
```

Последняя локальная проверка Task4:73 Python +19 Node проходят; две известные
deprecation warnings из Starlette/AnyIO. `.venv` — ссылка на окружение основного
checkout, использовать только unit-тесты. Не запускать здесь Engine/GPU inference.

Read-only проверки сервера с ноутбука:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 ubuntu@192.168.20.70 'curl -fsS http://127.0.0.1:8090/healthz'
ssh ubuntu@192.168.20.70 'systemctl is-active voice-rvc caddy; systemctl is-enabled voice-rvc; readlink /opt/voice-rvc/current'
ssh ubuntu@192.168.20.70 'systemctl show voice-rvc -p MemoryCurrent -p MemoryPeak -p NRestarts'
ssh ubuntu@192.168.20.70 'sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml ps'
ssh ubuntu@192.168.20.70 nvidia-smi
curl -fsS https://voice.lan.awesomeio.ru/healthz
curl -fsS https://vm-voice-1.lan.awesomeio.ru/healthz
```

RVC-health ожидается `status:ready, active:false, running:false, queuedWindows:0`.
503/warming при загрузке нормально до90с. 503/model_unavailable — ошибка загрузки.
Два HTTPS health сейчас относятся к DSP, **не доказывают готовность RVC**.
`curl 127.0.0.1:8080` на VM не обязан работать: Docker опубликован на LAN IP.

## 9. Оставшаяся работа, строго по порядку

1. Перепроверить раздел8. Сохранить исходное состояние Git/runtime. Не терять
   незакоммиченные spec-status, acceptance report, requirements.lock и эти документы.
2. Прочитать brief этапа4 в `.superpowers/sdd/2026-09-06-rvc-streaming/task-4-brief.md`.
3. Перепроверить scoped `deploy/deploy-rvc.sh`, `tests/live-rvc.py` и focused
   tests перед запуском. Старый `deploy/deploy.sh` копирует большой experiments
   и меняет сетевые настройки: **не использовать его для этого релиза**.
4. Убедиться, что `.dockerignore` исключает experiments, .worktrees и .superpowers.
   Иначе build context может включить многогигабайтные окружения и worktree.
5. Новый deploy должен сохранить конкретный rollback-тег старого образа, UI/source,
   compose, Caddy config, unit и текущую release-ссылку/состояние enabled.
   Не делать полную копию `/opt/voice-changer`: там большой experiment venv.
6. Стейджить release отдельно. Проверить unit и loopback readiness/реальную конверсию
   до переключения UI. Не допускать одновременно двух GPU-процессов загрузки модели.
7. В native Caddy добавить только `/ws/rvc` → `127.0.0.1:8090`, сохранить DSP catchall.
   Проверить `caddy validate` перед reload. Не переносить `/healthz` целиком на RVC.
8. Доставить подготовленный AI-only UI из worktree, проверить
   обе HTTPS-точки, новый WSS и сохранённый HTTP/static catch-all.
   Успешный native-сервис enable для загрузки VM. Проверить restart/readiness/reconnect.
9. Выполнить5мин paced test через приватный WSS, а не loopback. Проверить порядок,
   sample counts, отсутствие роста лага, ограниченность RAM/GPU/очередей.
10. Проверить реальный UI: только Phone Guy, Start/Stop, уровни и задержка0–10с;
    запрос/отказ микрофона, готовность, непрерывную речь, повторный запуск и смену
    задержки. Убедиться, что Stop отбрасывает незавершённый фрагмент. Не записывать
    микрофон пользователя.
11. Проверить откат по сохранённым конкретным targets. При провале live-гейтов
    вернуть старые image/UI/Caddy; оставить диагностические логи без аудио.
    Ручная команда: `./deploy/deploy-rvc.sh rollback <UTC-stamp>`; успех требует
    финальной проверки image/tree/Caddy/service-state/health, exit70 — failure.
12. Обновить acceptance фактическими числами, провести финальный review всей ветки,
    повторить тесты и зафиксировать только scoped changes. Merge/push отдельно
    согласовать; не удалять worktree/исследования автоматически.

Это план предстоящих live-действий. Скрипты из пункта3 созданы, но на VM ещё
не запускались. Не писать в отчёте, что deployment/private WSS/rollback прошли,
пока контроллер не запишет фактические результаты.

## 10. Что уже проверено, а что нет

- Последний повтор unit:59 Python /19 Node passed,2 известных warnings.
- Реальная GPU-конверсия RU/EN: по8с, ровно384000 отсчётов48kHz, без clipping.
- Loopback5мин:150 выходов,300с звука; processing p95≈709мс на2с речи.
  Средний лаг доставки относительно конца hop .7812→.7848с, роста очереди нет.
- Последняя память службы: current1485328384, peak1503526912 байт; NRestarts0.
  GPU на холостом ходу1055MiB из4096MiB. Это снимок, не постоянные гарантии.
- Численный скачок на границах RU .00018/.00140/.00113; EN .01273/.00180/.01337.
  Нет clipping. Такие числа НЕ доказывают сохранение тембра и незаметность швов.
- **НЕ выполнены:** private-WSS5мин, новый UI в браузере, полный deployment/rollback,
  recovery-gate после окончательного deployment и субъективное прослушивание стрима.

Синтетические WAV для сравнения на ноутбуке:
`/home/oleg/Documents/voice-changer/experiments/phoneguy/outputs/streaming-core/`.
Исходные RU/EN: `experiments/phoneguy/samples/input/` в основном checkout и на VM.
Scratch `paced-loopback.py` использовался на VM. Scratch `live-worklet.cjs` подготовлен
для Node+реального кода AudioWorklet+WSS, прошёл только синтаксическую проверку,
**ещё не запускался по сети**. Не считать его готовым regression-тестом до прогона.

## 11. Диагностика без догадок

| Симптом | Проверить первым |
| --- | --- |
| На сайте видны профиль или DSP-настройки | Новый UI ещё не доставлен / старая вкладка |
| RVC WSS не подключается, DSP работает | Native Caddy `/ws/rvc`, Origin, URL; не переустанавливать GPU |
| `busy` | Другая вкладка или ещё завершающийся GPU-вызов после Stop |
| Входной индикатор0 | Разрешение микрофона, выбранное устройство, mute |
| Есть вход, нет выхода | Ready/error, получение2с пакетов, runtime health |
| Есть выход, не слышно | Дополнительные5с, наушники/выход/mute вкладки |
| `overloaded` | Processing time, очередь и сетевой лаг; не увеличивать буферы вслепую |
| `model_unavailable` | Короткий journal, чтение assets от voice-rvc, pinned env, GPU |

Команда ограниченного журнала:
`ssh ubuntu@192.168.20.70 'sudo journalctl -u voice-rvc -n 60 --no-pager'`.
Не выгружать весь journal и не добавлять логирование PCM/микрофона.
Известный minor: upstream печатает caught empty-F0 traceback при прогреве нулями;
загрузка при этом завершается успешно. Триажить отдельно, не скрывать все ошибки.

## 12. Запреты и границы

- Не трогать Frigate/VM208, passthrough других VM, CPU/RAM VM и общую сеть.
- Не публиковать сервис наружу; 8090 должен остаться loopback.
- Не запускать inference на ноутбуке и не отправлять речь в облако.
- Не записывать пользовательский микрофон; сохранять можно только synthetic fixtures.
- Не использовать git reset --hard, git clean -fdx, массовое удаление или rsync --delete
  на production/основном checkout. Там есть независимые незакоммиченные исследования.
- Не делать silent fallback на DSP и не менять одобренный пресет ради теста.
- Не заявлять «всё готово» по unit-тестам или серверному RTF: нужен реальный private
  path и browser, а слышимое качество окончательно подтверждает пользователь.

## 13. Готовый запрос для следующего LLM

> Продолжи проект Phone Guy. Работай в
> /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming.
> Прочитай docs/HANDOFF.md, утверждённую RVC-spec и журнал этапов. Этапы1–3
> уже реализованы и проверены; начни с read-only сверки состояния, затем этап4.
> Код/доки на ноутбуке, inference только VM209, Frigate не трогать.
> Не путай написанный UI с развёрнутым: production пока DSP, RVC только loopback.
> Проверь и запусти безопасный scoped deployment с откатом, проверь private-WSS5мин и
> браузер, обнови фактический acceptance. Не запускай старый deploy/deploy.sh,
> не повторяй завершённые этапы, не выдавай unit-проверки за живую приёмку.

