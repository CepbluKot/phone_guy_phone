# FCPE canary: эксплуатация и границы

Обновлено 2026-09-08. Это отдельная private/VPN-демка быстрого движка
`infer/rtrvc.py`. Она существует для сравнения и резерва; рабочий голосовой
маршрут Phone Guy остаётся GPT v2 на `voice-rvc.service`.

## Что открыть

- Основной выбранный голос: `https://voice.lan.awesomeio.ru/low-latency/`.
- FCPE-canary: `https://voice-claude.lan.awesomeio.ru/`.
- Прямая готовность canary: `https://voice-claude.lan.awesomeio.ru/healthz`.

Оба домена доступны только из домашней сети или VPN. Для живой страницы нужны
наушники и разрешение микрофона. Canary не подключён к Asterisk-конференции и
никогда не назначается телефонному участнику автоматически.

## Точная схема

```text
браузер по VPN → voice-claude.lan.awesomeio.ru:443
                    Caddy на VM209 → 127.0.0.1:8093
                    voice-rtrvc-canary.service → FCPE, block 0.3 s

основной GPT v2: voice-rvc.service → 127.0.0.1:8090
```

Canary имеет собственные release-каталог, venv snapshot, FCPE package directory,
cache и systemd unit под `/opt/voice-rtrvc`. Он не меняет
`/opt/voice-rvc/venv`, `voice-rvc.service`, Asterisk, Frigate, DNS, firewall
или Proxmox.

## Подтверждённый live результат

Релиз `20260908T180350Z` был принят синтетическим probe без пользовательских
аудиоданных:

- loopback `ws://127.0.0.1:8093/ws/rvc` и private WSS прошли;
- получено три последовательных FCPE-блока по 0.3 с, все непустые;
- p95 inference: 122–126 ms на блок, RTF около 0.41;
- `voice-rvc.service` остался active, `NRestarts=0`;
- canary: `NRestarts=0`, host memory peak около 969 MiB, GPU около 834 MiB;
- GPT v2 занял около 1038 MiB GPU в тот же момент; суммарно остаётся запас
  на GTX 1050 Ti 4 GiB.

Это protocol/performance acceptance, а не обещание одинаковой субъективной
похожести на любой фразе и не измерение физической microphone-to-speaker
задержки.

## Доставка и откат

Код доставляется только scoped-скриптом из checkout с подготовленным Python
test environment:

```bash
VOICE_RTRVC_TEST_PYTHON=/home/oleg/Documents/voice-changer/.venv/bin/python \
  ./deploy/deploy-rtrvc-canary.sh
```

Перед переключением скрипт запускает contract-тесты. На VM он сохраняет Caddy,
предыдущий unit/current target и state в
`/opt/voice-rtrvc/backups/<UTC-stamp>`, валидирует Caddy, затем проверяет
health и один и тот же audio protocol локально и через WSS. Любой провал
включает автоматический rollback canary-состояния и отдельно убеждается, что
GPT v2 жив. Скрипт не выполняет `restart voice-rvc.service`.

Для ручной диагностики, не меняющей сервер:

```bash
ssh ubuntu@192.168.20.70 \
  'systemctl status voice-rtrvc-canary.service --no-pager; curl -fsS http://127.0.0.1:8093/healthz'
```

Если canary больше не нужен, сначала отключить **только** его unit и вернуть
точный Caddy/current target из выбранного backup. Не удалять `/opt/voice-rvc`
и не использовать broad cleanup-команды.
