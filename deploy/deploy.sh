#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pytest -q
node --test tests/*.test.cjs
git diff --check
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target=ubuntu@192.168.20.70
stage=/tmp/voice-release-$stamp
ssh "$target" "mkdir -p '$stage'"
rsync -a app web deploy Dockerfile .dockerignore requirements.txt requirements.lock compose.yaml "$target:$stage/"
ssh "$target" "sudo install -m 600 '$stage/deploy/60-voice-vpn.yaml' /etc/netplan/60-voice-vpn.yaml && sudo netplan generate && sudo netplan apply"
ssh "$target" "if sudo docker image inspect voice-changer:current >/dev/null 2>&1; then sudo docker tag voice-changer:current voice-changer:rollback-$stamp; fi"
ssh "$target" "sudo cp -a /opt/voice-changer /opt/voice-changer-backup-$stamp && sudo cp -a '$stage/.' /opt/voice-changer/ && sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml build && sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml up -d && sudo cp /opt/voice-changer/deploy/voice-firewall.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable voice-firewall.service && sudo systemctl restart voice-firewall.service"
ssh "$target" 'sudo cp /opt/voice-changer/deploy/voice-cert-sync.service /opt/voice-changer/deploy/voice-cert-sync.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl start voice-cert-sync.service && sudo cp /opt/voice-changer/deploy/Caddyfile /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy && sudo systemctl enable --now voice-cert-sync.timer'
for attempt in {1..20}; do
  if curl --max-time 3 -fsS https://voice.lan.awesomeio.ru/healthz; then
    curl --max-time 3 -fsS https://vm-voice-1.lan.awesomeio.ru/healthz
    echo "Deployment ready; previous source: /opt/voice-changer-backup-$stamp"
    exit 0
  fi
  sleep 2
done
echo 'Health check failed; inspect the container and restore the backup if needed.' >&2
exit 1
