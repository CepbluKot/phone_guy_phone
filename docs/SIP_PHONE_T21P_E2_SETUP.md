# Подключение Yealink SIP-T21P E2

## Что подготовлено

Asterisk и Phone Guy GPT v2 уже работают на отдельной VM209. Телефону нужен
только доступ к приватному адресу `192.168.20.70`: напрямую из домашней LAN или
через роутер с WireGuard. SIP и RTP в публичный интернет не выставлены.

Номера с пасхалками:

| Номер | Голос | Назначение |
| --- | --- | --- |
| `1983` | исходный | реквизитный телефон / обычный участник |
| `1987` | Phone Guy GPT v2 | основной обработанный абонент |
| `2014` | исходный | ещё один обычный участник |

Все набирают одну комнату: **`600`**. Одновременно модель обрабатывает одного
абонента; второй Phone Guy-сеанс будет отклонён без утечки исходного голоса.

## Что подключить физически

1. Подключить Ethernet к разъёму `Internet` телефона.
2. Подать питание штатным адаптером или совместимым PoE.
3. Дождаться адреса по DHCP и открыть этот адрес в браузере из той же сети.
4. Если телефон сохранил чужую корпоративную конфигурацию, сделать factory
   reset удержанием `OK` примерно 10 секунд и подтвердить сброс.

## Получить актуальные параметры аккаунта

Пароли генерируются при развёртывании и хранятся только на VM209. Команда ниже
показывает один выбранный аккаунт; её вывод нельзя сохранять в Git или скриншотах:

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/fcpe-canary-productization
./deploy/show-sip-phone-setup.sh 1987
```

Для реквизитного телефона без обработки вместо `1987` использовать `1983`.

## Поля Account 1 в Yealink

| Поле Yealink | Значение |
| --- | --- |
| Line Active | Enabled |
| Label / Display Name | любое понятное имя |
| Register Name | номер из команды, например `1987` |
| User Name | тот же номер |
| Password | значение `Password` из команды |
| SIP Server 1 / Server Host | `192.168.20.70` |
| Port | `5060` |
| Transport | `UDP` |
| NAT Traversal | Disabled |

В разделе codecs поднять **PCMA / G.711 A-law** на первое место. STUN, SIP TLS,
outbound proxy и внешний registrar для этой схемы не нужны.

После сохранения Account 1 должен стать `Registered`. Затем снять трубку,
набрать `600` и подтвердить вызов. Второй участник делает то же со своего
softphone или другого SIP-аккаунта.

## Проверки без вывода секретов

```bash
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show endpoints"'
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "pjsip show contacts"'
ssh ubuntu@192.168.20.70 'sudo docker exec voice-conference-asterisk-1 asterisk -rx "core show channels"'
ssh ubuntu@192.168.20.70 'curl -fsS http://127.0.0.1:8090/healthz'
```

Воспроизводимый синтетический тест двух виртуальных участников запускается на
VM209 и не печатает пароли:

```bash
stamp=$(ssh ubuntu@192.168.20.70 'sudo docker inspect voice-conference-controller-1 --format "{{.Config.Image}}"' | sed 's/.*://')
ssh ubuntu@192.168.20.70 "sudo python3 /opt/voice-conference/releases/$stamp/tests/live-sip-preflight.py --scenario /opt/voice-conference/releases/$stamp/tests/fixtures/sipp-auth-conference.xml"
```

Успех: оба `sip...Exit` равны `0`, `rvcInferenceObserved=true`, после звонка
`channelsAfter=0`, `privateBridgeAfter=false`, RVC снова `active=false`.

## Если не регистрируется или нет звука

- Проверить, что телефон видит маршрут до `192.168.20.70` и использует UDP 5060.
- Снова получить текущий пароль: после нового deploy он может измениться.
- Убедиться, что включён PCMA, а не только G.722/G.729.
- Для удалённой площадки проверить WireGuard-роутер рядом с телефоном; сам
  T21P E2 WireGuard не поднимает.
- Не включать публичный port-forward для 5060 или RTP.
- При `busy` убедиться, что другой `1987` уже не говорит; подождать освобождения
  текущего GPU-вызова и позвонить снова.
