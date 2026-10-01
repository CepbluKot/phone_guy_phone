# Voice Changer — эксплуатация

## Публичный SIP для физических IP-телефонов вне VPN

Физические аппараты теперь могут регистрироваться в Asterisk из обычного
интернета, без WireGuard на самом телефоне. Публичный сервер регистрации —
`phone.awesomeio.ru`, порт `5061`, транспорт TLS 1.2+, проверка сертификата
обязательна. Имя пользователя/Auth ID — назначенный внутренний номер, пароль —
уже существующий SIP-пароль этого номера. Сервер требует SRTP с SDES и не
принимает незашифрованное аудио; разрешённый кодек — G.711 A-law. В настройках
телефона включить NAT keepalive/обновление регистрации.

Публичный edge на VPS `evita` (`94.102.89.13`) перенаправляет только TCP
`5061` и UDP `10000-10019` на Asterisk VM209 (`192.168.20.70`) через WireGuard.
SIP/UDP `5060` не публикуется; публичные входящие TCP/UDP `5060` отбрасываются.
Публичные HTTP-админка, Go API, ARI и RVC через этот edge не доступны. Порт
`5061` ограничен rate-limit-ами, RTP ограничен по источнику и частоте, а VM
разрешает SIP только через VPS.

Для первой проверки использовать назначенный аппаратам номер `1988`. У `1983`
сохранён статический LAN-контакт Yealink `192.168.20.134:5062`; не удалять его
и не менять номер на телефоне, пока отдельная регистрация 1983 через интернет
не проверена и не подготовлен откат. Админская запись MAC/телефона сама по себе
не меняет SIP-настройки аппарата.

Состояние на 2026-10-01: edge включён и пережил реальную перезагрузку VPS
(загрузка после reboot — `2026-10-01 09:55:38 UTC`), Asterisk release
`20261001T124305Z`, предыдущий — `20261001T094458Z`. Публичная TLS/SIP-проверка
без SIP-учётных данных получает ожидаемый `401 Unauthorized`. Все фиксированные
endpoints требуют
SDES-SRTP, четыре SIP-пароля длинные и уникальные; сами значения не хранить в
репозитории или журнале. VM209 обновляет сертификат из ограниченного exporter
на VPS по включённому hourly timer `voice-sip-cert-sync.timer`. Регистрация,
звонки и RVC с физического аппарата из сети вне VPN ещё не приняты — см.
[LIVE_STATUS.md](LIVE_STATUS.md).

Точка отката Asterisk: `/opt/voice-go/backups/sip-20261001T124305Z`
(предыдущий релиз — `20261001T094458Z`); снимки
edge/firewall VPS `/var/backups/voice-sip-edge-20261001T091822Z` и
`/var/backups/voice-sip-edge-20261001T093927Z`; VM firewall
`/var/backups/voice-firewall-before-public-sip-20261001T092538Z`.
VM принимает SIP TLS на этом пути только от WireGuard-адреса VPS `10.19.87.1`.
Rollback проверяет число активных каналов до изменения firewall и повторно
непосредственно перед перезапуском Asterisk; при активном звонке он сообщает
`ROLLBACK_BLOCKED` и оставляет текущую версию работать.

## Публичный доступ к браузерному телефону

Публичный маршрут доступен на `https://phone.awesomeio.ru/`. Go показывает
собственную страницу входа, а после успешной проверки устанавливает
Secure/HttpOnly/SameSite=Strict cookie на 90 дней с автоматическим продлением
при использовании. В cookie хранится подписанная сессия, а не пароль; после
первого входа в этом профиле браузера ничего вводить не нужно. Другому
браузеру/устройству, приватному окну, удалённым cookie или после 90 дней без
использования потребуется войти снова. VPS Caddy завершает TLS и проксирует
только `/phone*`, защищённые cookie `/admin/assets/*` и
`/ws/phone-signaling` по WireGuard на Go-сервис VM209. Страница `/admin/`,
`/healthz` и прочие пути возвращают 404. Teleport в этом маршруте не участвует; Teleport
agent на VM выключен и удалён из автозапуска.

Внешний доступ требует только TCP 443 для HTTPS/WSS и TURN over TLS на TCP 5349.
TURN выдаёт короткоживущие credentials; relay UDP ограничен портами
49160–49219, а coturn может обращаться только к Asterisk VM209 на UDP
10000–10019. SIP, ARI, RVC, Go HTTP и Asterisk RTP listeners не проксируются
через публичный HTTPS-маршрут. Owner credential verifier и случайный ключ
подписи cookie хранятся в `/etc/voice-phone-auth/public-auth.json` с владельцем
UID/GID `10001:10001` и правами `0400`; пароль не сохранять в репозитории.

Проверено для Go release `20260930T185105Z`: входная страница доступна без Basic
Auth prompt; неверный пароль и Origin отклоняются; 90-дневная cookie открывает
phone API и signaling WSS; logout очищает cookie; admin и health маршруты дают
404. Caddy Basic Auth snippet выведен из активного каталога и сохранён вместе
с прежним Caddyfile в `/var/backups/caddy-phone-cookie-20260930T185105Z`.
Двухбраузерный звонок и реальный микрофон для этой версии отдельно не
проверялись; см. [BROWSER_CALL_ACCEPTANCE.md](BROWSER_CALL_ACCEPTANCE.md).
На VM209 `/etc/voice-phone-auth/public-cookie-route.enabled` блокирует ручной
rollback Go на релиз без cookie-auth. Перед намеренным откатом на старый Go
сначала восстановить owner-only auth на внешнем Caddy, и лишь затем создать
`/etc/voice-phone-auth/public-basic-restored.enabled`.

Для публичного браузерного телефона read-only `GET /phone/api/v1/config`
допускает отсутствие `Origin` только если браузер прислал
`Sec-Fetch-Site: same-origin`, а `Host` точно совпадает с настроенным
разрешённым origin. Так работают браузерные same-origin GET-запросы в Chrome.
Регистрация, heartbeat, завершение звонка и прочие изменяющие запросы по-прежнему
проверяют точный `Origin`.
В публичном интерфейсе при отключённом телефоне отображается статус
«Публичный доступ защищён»; приватная сеть обозначается только на LAN-домене.

На VM после удаления устаревшего listener `127.0.0.1:8181` сохранён приватный
HTTPS health и страница телефона. TURN активен на VPS и принимает только TLS
control listener 5349 плюс настроенный UDP relay range.

Фактический звонок из двух браузеров через внешнюю сеть с проверкой двустороннего
звука не выполнялся в этом обновлении. Эти проверки подтверждают маршрут,
вход, API, сигнализационный handshake и TURN-конфигурацию, но не сквозной звук.
Admin остаётся приватным.

Актуальная версия и доказательства: [LIVE_STATUS.md](LIVE_STATUS.md).
Инструкция следующему агенту: [HANDOFF.md](HANDOFF.md).

Код: /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming на ноутбуке.
Выполнение: VM209, ubuntu@192.168.20.70.
Телефон: https://voice-phone.lan.awesomeio.ru/phone/; админка:
https://voice-admin.lan.awesomeio.ru/admin/ (через VPN). Старый домен
`voice.lan.awesomeio.ru` перенаправляет эти разделы на новые адреса. Наушники → обновить страницу →
«Начать» → разрешить микрофон → дождаться готовности и говорить непрерывно.
Старый эффект, его ползунки и тестовый тон больше не доступны в UI.

## Возможности и ограничения

### Browser calls and per-phone voice profiles

The Go call controller treats both `StasisEnd` and `ChannelDestroyed` as a
call-leg termination. Asterisk emits `StasisEnd` when a channel leaves the ARI
application; `ChannelDestroyed` is not guaranteed for application-scoped
channels. Ending either leg closes the browser/physical peer legs, the bridge,
and any active RVC session.

Voice profiles are resolved by configured SIP extension for both call
directions. A `phone-guy` profile on extension `1988` processes speech from that
phone whether it starts or answers the call; other participants should hear
the converted speech. While a processed call is active, `/admin/` → Load should
show an active processed call and fresh RVC processing samples. These
operational samples are short-lived and are not a call recording or durable
history.

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

- DNS service: `voice-admin.lan.awesomeio.ru`, `voice-phone.lan.awesomeio.ru`
  и совместимый `voice.lan.awesomeio.ru` → `10.19.87.1`.
- DNS VM: `vm-voice-1.lan.awesomeio.ru → 192.168.20.70`.
- Caddy на VPN VPS проксирует `192.168.20.70:8080`.
- Админка и телефон используют отдельные хосты; edge Caddy разрешает на них
  `/admin/` и `/phone/` соответственно. Телефон также получает общие статические
  файлы Vite из `/admin/assets/`, но админский UI и API на его хосте закрыты.
  SIP-сигнализация идёт по WSS к `vm-voice-1.lan.awesomeio.ru:443`,
  а медиапоток WebRTC использует DTLS-SRTP на приватных UDP-портах VM.
  Это исключает лишний путь через VPS для клиента в домашней LAN.
  Сам UI также доступен на домене VM.
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

**Статус на 2026-09-27:** размещено на VM209, release
`20260927T175856Z`. Go и Asterisk health-check прошли, ARI endpoint resource
загружен. Владелец подтвердил браузерный звонок между двумя профилями после
повторного подключения браузеров. Вызов на физический аппарат и обработка
разговора через RVC этой проверкой не подтверждались.

Это историческая проверка и она не заменяет приёмку текущего релиза после
изменений SIP, UI звонка, завершения вызова или WebRTC-аудио. Для таких
изменений обязательна отдельная проверка реального звонка между двумя
независимыми браузерными сессиями: [Browser-to-browser call acceptance](BROWSER_CALL_ACCEPTANCE.md).
Не засчитывать сборку и unit-тесты вместо этого этапа.

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

Каталог `/phone/` автоматически обновляет статусы раз в 2 секунды. Статус
браузера берётся из активной Go-сессии; статус физического аппарата — из ARI
состояния его PJSIP endpoint. При временной ошибке чтения Asterisk для
физического аппарата показывается «статус неизвестен».

Для браузерного звонка оба участника должны подключить страницу к разным
внутренним номерам и видеть зелёный статус «Готов принимать звонки». В списке
адресатов браузер должен отображаться как «в сети»; входящий вызов открывает
модалку «Вам звонят». Номер, введённый в форме регистрации, не занят браузером,
пока сессия не подключена.

Браузерные SIP-сессии и lease хранятся в памяти Go. Перезапуск или деплой Go
сбрасывает их; heartbeat страницы обнаруживает потерю и возвращает форму
подключения. После такого перезапуска нужно заново подключить каждый браузер.
Пока получатель не подключён и не виден как «браузер · в сети», вызов не сможет
показать ему входящую модалку. Не перезапускать Go или Asterisk при активном
вызове; deployment preflight блокирует такой перезапуск.

Проверка при диагностике:

```bash
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show transports"'
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show endpoints"'
curl -fsS https://voice.lan.awesomeio.ru/phone/api/v1/directory
```

Не открывать публичные SIP/RTP/ARI порты ради браузера. Если WSS-регистрация,
DTLS-SRTP или приватный RTP путь не подтверждаются, отключить браузерную сессию
и использовать Go production rollback stamp из deployment output.

Для отката release `20260927T175856Z` из checkout выполнить:

```bash
./deploy/rollback-goweb-update.sh 20260927T175856Z
```

Пользователь подтвердил, что браузерный вызов после подключения обеих страниц
работает. Отдельно не проверены качество и двусторонняя слышимость на всех
моделях браузеров, одновременный ответ браузера и физического аппарата, а также
преобразование живого телефонного разговора через RVC.

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
