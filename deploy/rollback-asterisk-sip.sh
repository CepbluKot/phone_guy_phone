#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ "$#" == 1 && "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || {
  echo "Usage: rollback-asterisk-sip.sh YYYYMMDDTHHMMSSZ" >&2; exit 2;
}
stamp=$1
go_root=/opt/voice-go
backup="$go_root/backups/sip-$stamp"
old_release=$(cat "$backup/previous-release")
rollback_release="$go_root/releases/$stamp"
firewall_source=/opt/voice-changer/deploy/firewall.sh
rollback_firewall="$rollback_release/deploy/firewall.sh"

case "$old_release" in "$go_root"/releases/*) ;; *) echo "Invalid previous release path" >&2; exit 1 ;; esac
[[ -f "$old_release/deploy/compose.conference.yaml" && -f "$old_release/.env" ]] || {
  echo "Previous Asterisk compose release is incomplete" >&2; exit 1;
}
[[ -f "$rollback_release/deploy/update_pjsip_wss_transport.py" \
  && -f "$rollback_release/conference/asterisk/pjsip.conf.template" \
  && -f "$rollback_firewall" && -f "$rollback_release/deploy/compose.conference.yaml" \
  && -f "$old_release/deploy/compose.conference.yaml" \
  && -f "$old_release/runtime/asterisk/pjsip.conf" ]] || {
  echo "Rollback cannot restore extension 1234 TLS/5062 compatibility" >&2; exit 1;
}
[[ -f "$backup/firewall.sh" && -s "$backup/asterisk-image-id" ]] || {
  echo "SIP rollout backup is incomplete" >&2; exit 1;
}

assert_no_active_calls() {
  local channels
  channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
  if ! printf '%s\n' "$channels" | grep -Eq '0 active channels'; then
    echo "ROLLBACK_BLOCKED active-call; retry after calls end" >&2
    return 75
  fi
}

# Leave the live firewall and release untouched if a call is already active.
assert_no_active_calls

iptables -D DOCKER-USER -i eth0 -p tcp --dport 5061 -j VOICE_SIP_INGRESS 2>/dev/null || true
iptables -D DOCKER-USER -i eth0 -p udp --dport 5060 -j VOICE_SIP_INGRESS 2>/dev/null || true
iptables -D DOCKER-USER -i eth0 -p udp --dport 10000:10019 -j VOICE_SIP_INGRESS 2>/dev/null || true
iptables -F VOICE_SIP_INGRESS 2>/dev/null || true
iptables -X VOICE_SIP_INGRESS 2>/dev/null || true
install -o root -g root -m 0755 "$rollback_firewall" "$firewall_source"
/bin/sh "$firewall_source"

# Recheck immediately before replacing Asterisk in case a call started during
# the brief firewall restore window.
assert_no_active_calls
if [[ ! -e "$backup/pjsip-before-1234-transport-migration.conf" ]]; then
  cp -a "$old_release/runtime/asterisk/pjsip.conf" \
    "$backup/pjsip-before-1234-transport-migration.conf"
fi
if [[ ! -e "$backup/compose-before-1234-transport-migration.yaml" ]]; then
  cp -a "$old_release/deploy/compose.conference.yaml" \
    "$backup/compose-before-1234-transport-migration.yaml"
fi
python3 - "$old_release/deploy/compose.conference.yaml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
mapping = '      - "192.168.20.70:5063:5063/tcp"'
if mapping not in text:
    anchor = '      - "192.168.20.70:5061:5061/tcp"\n'
    if text.count(anchor) != 1:
        raise SystemExit("rollback compose has no unique TCP/5061 mapping")
    text = text.replace(anchor, anchor + mapping + "\n", 1)
path.write_text(text)
PY
python3 "$rollback_release/deploy/update_pjsip_wss_transport.py" \
  "$old_release/runtime/asterisk/pjsip.conf" \
  "$rollback_release/conference/asterisk/pjsip.conf.template"
docker compose -p voice-conference -f "$old_release/deploy/compose.conference.yaml" \
  --env-file "$old_release/.env" up -d --no-build --no-deps --force-recreate asterisk
ready=0
for attempt in $(seq 1 40); do
  if [[ "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)" == healthy ]]; then
    ready=1
    break
  fi
  sleep 2
done
[[ "$ready" == 1 ]] || { echo "ROLLBACK_FAILED previous Asterisk health check failed" >&2; exit 70; }
docker exec voice-conference-asterisk-1 asterisk -rx 'ari show apps' | grep -q voice-control
docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show endpoint 1234' \
  | grep -Eq 'transport[[:space:]]+:[[:space:]]+transport-tls-1234-internet-legacy([[:space:]]|$)'
docker port voice-conference-asterisk-1 5063/tcp \
  | grep -Fxq '192.168.20.70:5063'
openssl s_client -connect 192.168.20.70:5063 -servername phone.awesomeio.ru \
  -verify_hostname phone.awesomeio.ru -verify_return_error \
  -tls1 -cipher 'AES128-SHA:@SECLEVEL=0' </dev/null >/dev/null 2>&1
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -s 192.168.20.12 -j ACCEPT
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -s 10.19.87.1 -j ACCEPT
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -j DROP
iptables -C DOCKER-USER -i eth0 -p tcp --dport 5063 -j VOICE_SIP_INGRESS
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Rollback left an active call" >&2; exit 70; }
printf 'ASTERISK_SIP_ROLLBACK_COMPLETE stamp=%s\n' "$stamp"
