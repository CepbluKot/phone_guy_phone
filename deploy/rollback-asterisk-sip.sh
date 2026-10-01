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
firewall_source=/opt/voice-changer/deploy/firewall.sh

case "$old_release" in "$go_root"/releases/*) ;; *) echo "Invalid previous release path" >&2; exit 1 ;; esac
[[ -f "$old_release/deploy/compose.conference.yaml" && -f "$old_release/.env" ]] || {
  echo "Previous Asterisk compose release is incomplete" >&2; exit 1;
}
[[ -f "$backup/firewall.sh" && -s "$backup/asterisk-image-id" ]] || {
  echo "SIP rollout backup is incomplete" >&2; exit 1;
}

iptables -D DOCKER-USER -i eth0 -p tcp --dport 5061 -j VOICE_SIP_INGRESS 2>/dev/null || true
iptables -D DOCKER-USER -i eth0 -p udp --dport 10000:10019 -j VOICE_SIP_INGRESS 2>/dev/null || true
iptables -F VOICE_SIP_INGRESS 2>/dev/null || true
iptables -X VOICE_SIP_INGRESS 2>/dev/null || true
install -o root -g root -m 0755 "$backup/firewall.sh" "$firewall_source"
/bin/sh "$firewall_source"

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
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Rollback left an active call" >&2; exit 70; }
printf 'ASTERISK_SIP_ROLLBACK_COMPLETE stamp=%s\n' "$stamp"
