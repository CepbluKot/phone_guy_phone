#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid backup stamp" >&2; exit 1; }
backup="/var/backups/voice-sip-edge-$stamp"

install -d -o root -g root -m 0700 "$backup"
/usr/sbin/nft list ruleset > "$backup/nft-ruleset.txt"
chmod 0600 "$backup/nft-ruleset.txt"
for path in \
  /usr/local/sbin/voice-sip-edge \
  /etc/voice-sip-edge/voice-sip-edge.nft \
  /etc/systemd/system/voice-sip-edge.service; do
  if [[ -e "$path" ]]; then
    cp -a "$path" "$backup/$(basename "$path")"
  else
    printf '%s\n' "$path" >> "$backup/absent-before-install"
  fi
done
chmod 0600 "$backup"/* 2>/dev/null || true

/usr/sbin/nft --check --file "$repo/deploy/sip-edge/voice-sip-edge.nft"
install -d -o root -g root -m 0750 /etc/voice-sip-edge
install -o root -g root -m 0644 "$repo/deploy/sip-edge/voice-sip-edge.nft" /etc/voice-sip-edge/voice-sip-edge.nft
install -o root -g root -m 0755 "$repo/deploy/sip-edge/voice-sip-edge" /usr/local/sbin/voice-sip-edge
install -o root -g root -m 0644 "$repo/deploy/voice-sip-edge.service" /etc/systemd/system/voice-sip-edge.service
systemctl daemon-reload

printf 'SIP_EDGE_INSTALLED backup=%s enabled=%s\n' "$backup" "$(systemctl is-enabled voice-sip-edge.service 2>/dev/null || printf disabled)"
