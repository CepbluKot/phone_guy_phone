#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid backup stamp" >&2; exit 1; }
backup="/var/backups/voice-sip-cert-sync-$stamp"

install -d -o root -g root -m 0700 "$backup" /usr/local/lib/voice
for path in \
  /usr/local/sbin/voice-sip-cert-sync \
  /usr/local/lib/voice/validate_sip_cert_bundle.py \
  /etc/systemd/system/voice-sip-cert-sync.service \
  /etc/systemd/system/voice-sip-cert-sync.timer; do
  if [[ -e "$path" ]]; then
    cp -a "$path" "$backup/$(basename "$path")"
  else
    printf '%s\n' "$path" >> "$backup/absent-before-install"
  fi
done
chmod 0600 "$backup"/* 2>/dev/null || true

install -o root -g root -m 0755 "$repo/deploy/sync-sip-cert.sh" /usr/local/sbin/voice-sip-cert-sync
install -o root -g root -m 0644 "$repo/deploy/validate_sip_cert_bundle.py" /usr/local/lib/voice/validate_sip_cert_bundle.py
install -o root -g root -m 0644 "$repo/deploy/voice-sip-cert-sync.service" /etc/systemd/system/voice-sip-cert-sync.service
install -o root -g root -m 0644 "$repo/deploy/voice-sip-cert-sync.timer" /etc/systemd/system/voice-sip-cert-sync.timer
systemctl daemon-reload

printf 'SIP_CERT_SYNC_INSTALLED backup=%s timer_enabled=%s\n' "$backup" "$(systemctl is-enabled voice-sip-cert-sync.timer 2>/dev/null || printf disabled)"
