# Voice Changer — эксплуатация

Актуальная версия и доказательства: [LIVE_STATUS.md](LIVE_STATUS.md).
Инструкция следующему агенту: [HANDOFF.md](HANDOFF.md).

Код: /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming на ноутбуке.
Выполнение: VM209, ubuntu@192.168.20.70.
UI: https://voice.lan.awesomeio.ru/ через VPN. Наушники → обновить страницу →
«Начать» → разрешить микрофон → дождаться готовности и говорить непрерывно.
Старый эффект, его ползунки и тестовый тон больше не доступны в UI.

## Возможности и ограничения

### Admin load dashboard (implementation status: local source only)

The React admin now has a Load page backed by `GET /admin/api/v1/metrics`.
It reports the one-call Go admission limit, read-only gate occupancy, RVC
health/queue freshness, a bounded five-minute p50/p95 of validated RVC
`processingMs` observations, and host/RVC CPU and RAM when their read-only
sources are mounted. The handler uses the same admin authorization policy as
phone routes and returns `Cache-Control: no-store`.

The measured capacity estimate intentionally remains **not measured**. No
isolated multi-session run has been accepted. GPU/VRAM remains unavailable
because there is no restricted live metrics source. The Go container receives
only read-only `/proc/stat`, `/proc/meminfo`, and the `voice-rvc.service`
cgroup mounts; it has no Docker socket or host PID access. RVC CPU usage is
normalized to the service CPU quota, and RVC RAM is compared with its memory
limit. These mounts have
not been validated in a deployed container yet. This feature has not been
deployed to VM209; do not treat a successful local build as evidence about the
live admin.

The Go service polls only its loopback RVC health URL (five-second interval,
two-second timeout; stale after 15 seconds). Processing samples and error
codes are bounded in memory, and the metrics path does not collect PCM, SIP
credentials, caller IDs, or provisioning responses. The existing production
limit remains one in both Go and Python.

### Physical phone discovery and provisioning prerequisites

The Go admin's phonebook maps MAC addresses to configured SIP extensions for
administrative tracking only. It does not change a handset account, Asterisk
endpoint, or voice route. Initial rows are seeded from point-in-time PJSIP and
neighbor observations; the UI labels their IP and observation date as history.
Additional phones can be entered manually. Do not interpret those rows as live
registration or automatic discovery.

Do not enable a credential-bearing provisioning URL until handset model and
firmware, certificate trust, stable approved source IP, and a constrained
Asterisk apply/reload path have been verified. The current deployment has no
verified read-only DHCP lease API and no confirmed AMI contact reader. Existing
PJSIP registrations can be inspected operationally, but an IP address alone is
not sufficient to identify or enroll a physical handset. Keep provisioning
disabled while these prerequisites are unresolved.

RVC меняет тембр моделью PhoneGuyfnaf1V1 на GPU VM209. Capture48kHz mono PCM16,
20мс кадры; выход2с фрагментами. Одновременно допускается одна RVC-сессия.
«Доп. задержка»0–10с, шаг.5с, default5. Это сдвиг непрерывного потока, не ожидание
конца фразы и не полная задержка. В синтетическом тесте первый звук с5с extra
появился через8.08с. Реальная акустическая задержка отдельно не измерялась.

Смена задержки очищает старый queued/delayed звук. Stop сразу выключает микрофон
и выход, отбрасывает незавершённый фрагмент. GPU-вызов может завершаться в фоне,
его старый результат не передаётся новой сессии. При busy подождать и повторить Start.

Служба voice-rvc active/enabled, отдельный пользователь, loopback8090,
memory2700MiB, swap0, CPU250%. VM4GiB, GTX1050Ti4GiB.
Звук/транскрипты пользователя не сохраняются, облако не используется.
Discord/OBS не получают виртуальный микрофон автоматически.
Порог<150мс исключён пользователем из задачи.

## Проверки и доставка

### RVC: изолированная доставка

Перед запуском проверить `git status`, прочитать
[RVC acceptance](RVC_ACCEPTANCE_2026-09-06.md) и убедиться, что текущая VM209
совпадает с baseline. Скрипт рассчитан только на уже настроенную VM209: он не
создаёт VM, не меняет DNS/VPS, firewall, netplan, ресурсы и Frigate.

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
./deploy/deploy-rvc.sh
```

Скрипт сначала выполняет Python/Node unit-тесты. Затем он сохраняет конкретный
`voice-changer:rollback-<UTC stamp>`, точный архив дерева `web`, изменяемые
source-файлы, Caddy, unit, прежнюю release-ссылку, active/enabled-state в
`/opt/voice-rvc/backups/<UTC stamp>`. Каталог `experiments` не копируется.
RVC release запускается и проходит paced loopback speech gate до смены UI.
Caddy валидируется до reload; добавляется только `/ws/rvc` на
`127.0.0.1:8090`, а HTTP/static catch-all остаётся на `.70:8080`. Старый DSP
образ сохраняется для отката, но его профиль не требуется в финальном UI.

После переключения скрипт проверяет оба private HTTPS health, private WSS,
ручной restart worker и reconnect. Любой провал запускает возврат сохранённых
image/source/UI/Caddy/unit/release targets и сохраняет ограниченные журналы без
PCM. Stamp и backup path печатаются в конце успешного запуска.

Полная синтетическая приёмка private WSS (RU и EN, 960 отсчётов каждые20мс):

```bash
.venv/bin/python tests/live-rvc.py --seconds 300 \
  --output-dir /home/oleg/Documents/voice-changer/experiments/phoneguy/outputs/private-wss-acceptance
```

JSON фиксирует фактическую длительность, sample timeline, processing RTF,
send drift, лаг, очередь, RAM/GPU и рестарты. WAV-артефакты содержат только
утверждённые synthetic fixtures и нужны для отдельного прослушивания швов.
Тест не использует микрофон и не доказывает акустическую end-to-end задержку.

Существующий `/opt/voice-rvc/venv` используется только если все установленные
версии точно совпадают с `rvc_service/requirements.lock` и `pip check` чист.
Скрипт не меняет owner/mode, не пишет marker и не переустанавливает этот venv.
При несовпадении pins доставка останавливается до переключения UI; исправлять
окружение следует отдельно с новым явным планом, сохранив старое для отката.

Для ручного отката использовать только stamp, напечатанный доставкой:

```bash
./deploy/deploy-rvc.sh rollback <YYYYMMDDTHHMMSSZ>
```

Эта команда вызывает ту же реализацию, что failure trap, и завершается успешно
только после проверки image identity, точного web/source/Caddy, release-ссылки,
active/enabled-state, Caddy config и health. `ROLLBACK_FAILED` или exit70 означает
незавершённый откат и требует ручного разбора. Перед запуском проверить наличие
`/opt/voice-rvc/backups/<stamp>` и `voice-changer:rollback-<stamp>`; не выбирать
«последний» тег вслепую.

### Не использовать старую доставку

Старый deploy/deploy.sh предназначался для первоначального DSP-развёртывания,
копирует большой experiments и затрагивает сеть. Для текущего проекта не запускать.
Восстановление только тегированием образа неполно: используйте scoped rollback выше,
который также возвращает web/Caddy/service/release state.

Корневой requirements.lock с хешами относится к HTTP-контейнеру.
rvc_service/requirements.lock — отдельные точные pins нейросетевого окружения.
Не смешивать их и не обновлять зависимости вместе с обычным UI-деплоем.

## Сеть и изоляция

- DNS service: `voice.lan.awesomeio.ru → 10.19.87.1`.
- DNS VM: `vm-voice-1.lan.awesomeio.ru → 192.168.20.70`.
- Caddy на VPN VPS проксирует `192.168.20.70:8080`.
- UI остаётся на прежнем домене; аудио идёт напрямую по TLS к
  `vm-voice-1.lan.awesomeio.ru:443`. Это исключает лишний путь через VPS
  для клиента в домашней LAN. Сам UI также доступен на домене VM.
- Caddy в VM принимает HTTPS только на LAN-адресе; UFW разрешает 443
  из домашней LAN и VPN. Сертификат обновляется каждые 12 часов через
  `voice-cert-sync.timer`. SSH-ключ VM на VPS ограничен forced-command,
  который отдаёт только существующий wildcard-сертификат и его ключ.
  Приватные ключи находятся только на серверных хостах, вне Git.
- Docker публикует только LAN IPv4, без IPv6/all-interface listener.
- `voice-firewall.service` после Docker устанавливает DOCKER-USER правило:
  разрешены источники `192.168.20.12` (SNAT домашнего VPN-шлюза) и
  `10.19.87.1`; остальные входящие подключения к 8080 отбрасываются.
- UFW сам по себе не ограничивает опубликованный Docker-порт.
- `deploy/60-voice-vpn.yaml` задаёт прямой маршрут к VPN через `.12`.
- Контейнер: UID 10001, read-only root, без capabilities, 512 MiB, 2 CPU,
  максимум 128 процессов, ротация логов. Один Uvicorn worker необходим
  для ограничения одной сессии.
- VM: 4 vCPU, 4 GiB RAM, 64 GiB local-lvm. Frigate имеет свою VM 208;
  физический Proxmox хост остаётся общим.

## Браузерный внутренний телефон

**Статус:** размещено на VM209, release `20260926T180933Z`. Проверены `/phone/`
через `voice.lan`, Go API, WSS через Caddy VM, успешная SIP-регистрация браузера
и немедленное освобождение lease. После проверки временный ник удалён, каталог
восстановлен, Go перезапущен; сессий и динамических `web-*` endpoint нет.
Физические регистрации `1983` и `1988` сохранены; активных каналов нет.

React-страница `/phone/` использует Go API на том же HTTPS origin для каталога,
выдачи временной сессии, heartbeat и освобождения. SIP.js подключается напрямую
по `wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling`; Caddy VM переписывает
только этот маршрут на loopback Asterisk HTTP `/ws`. ARI остаётся на
`127.0.0.1:8092`, RTP — в существующем частном диапазоне `10000-10019/udp`.

Go хранит каталог в `/etc/voice-changer/webphone-directory.json` (schemaVersion
1, mode `0600`, UID/GID `10001`). Там только ник и настроенный внутренний номер.
На один номер разрешена одна браузерная сессия; физический телефон остаётся
зарегистрированным. Go продлевает lease каждые 10 секунд, истёкший lease
отзывает динамические PJSIP endpoint/AOR/auth через ARI до освобождения номера.
Временный пароль выдаётся только в ответе claim по HTTPS и находится в памяти
страницы. Не копировать claim-ответы, пароли, SIP payload или аудио в логи и
файлы.

Проверка при диагностике:

```bash
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show transports"'
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show endpoints"'
curl -fsS https://voice.lan.awesomeio.ru/phone/api/v1/directory
```

Не открывать публичные SIP/RTP/ARI порты ради браузера. Если WSS-регистрация,
DTLS-SRTP или приватный RTP путь не подтверждаются, отключить браузерную сессию
и использовать Go production rollback stamp из deployment output.

На VM первые две попытки update были автоматически отменены preflight/smoke:
runtime Asterisk переопределил модульный список и smoke обращался не к тому
приватному адресу. Исправленный update прошёл полностью; сохранённая точка
возврата: `/opt/voice-go/updates/20260926T180933Z`. Для отката именно этого
обновления из checkout выполнить:

```bash
./deploy/rollback-goweb-update.sh 20260926T180933Z
```

Регистрация в браузере подтверждает SIP signaling/authentication. Реальный
звонок, DTLS-SRTP/RTP аудио, параллельный ответ двух устройств и обработку RVC
в разговоре ещё не проверяли: такая проверка позвонила бы подключённым физическим
аппаратам. Не считать эти call/media сценарии принятой live-приёмкой.

```bash
curl -fsS https://voice.lan.awesomeio.ru/healthz
ssh ubuntu@192.168.20.70 'sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml ps'
ssh ubuntu@192.168.20.70 'sudo systemctl status voice-firewall --no-pager; sudo iptables -nvL VOICE_INGRESS'
ssh ubuntu@192.168.20.70 nvidia-smi
```

## Если тишина

1. Обновить вкладку, закрыть остальные сеансы; надеть наушники.
2. Нажать «Начать», разрешить нужный микрофон, проверить входной индикатор.
3. Дождаться первого2с фрагмента, обработки и выбранной дополнительной задержки.
4. Проверить выход/устройство воспроизведения/mute вкладки.
5. RVC-счётчики НЕ равны: один выход примерно на100 входных кадров.
6. При ошибке прочитать статус, проверить отдельно RVC health и ограниченный journal.
   Подробная таблица симптомов: [HANDOFF.md](HANDOFF.md#8-если-тишина).

Для первичной настройки прямого TLS на новой VM нужны Caddy, отдельный
SSH-ключ `/root/.ssh/voice-cert-reader`, регистрация его публичной части
через `deploy/install-vps-export.sh` на VPS и проверенный known_hosts.
Скрипт доставки предполагает выполненную первичную настройку; текущая VM
настроена. В случае замены VPS сначала независимо проверьте новый host key.
