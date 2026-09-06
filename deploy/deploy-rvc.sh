#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

target=${DEPLOY_RVC_TARGET:-ubuntu@192.168.20.70}
stamp=${DEPLOY_RVC_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
stage="/tmp/voice-rvc-release-$stamp"
backup="/opt/voice-rvc/backups/$stamp"
health_attempts=${DEPLOY_RVC_HEALTH_ATTEMPTS:-20}
health_delay=${DEPLOY_RVC_HEALTH_DELAY:-2}
smoke_seconds=${DEPLOY_RVC_SMOKE_SECONDS:-10}
rollback_required=0

if [[ ! "$target" =~ ^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$ ]]; then
  echo "DEPLOY_RVC_TARGET must be user@host" >&2
  exit 2
fi
if [[ ! "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Invalid release stamp; expected YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi

rollback() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollback_required" -eq 1 ]; then
    echo "RVC rollout failed; restoring backup $backup" >&2
    ssh "$target" sudo bash -s -- "$stamp" <<'REMOTE_ROLLBACK' || true
set -euo pipefail
stamp=$1
backup="/opt/voice-rvc/backups/$stamp"
echo ROLLBACK_BEGIN voice-changer:rollback-$stamp /etc/caddy/Caddyfile /opt/voice-changer /opt/voice-rvc/current
test -d "$backup" || exit 0
sudo journalctl -u voice-rvc.service --since "-30 min" --no-pager > "$backup/voice-rvc-failure.log" 2>&1 || true
sudo docker logs --since 30m voice-changer-voice-1 > "$backup/voice-ui-failure.log" 2>&1 || true
if sudo docker image inspect "voice-changer:rollback-$stamp" >/dev/null 2>&1; then
  sudo docker tag "voice-changer:rollback-$stamp" voice-changer:current
fi
if test -f "$backup/voice-source.tar.gz"; then
  sudo tar -xzf "$backup/voice-source.tar.gz" -C /opt/voice-changer
fi
if test -f "$backup/Caddyfile"; then
  sudo cp "$backup/Caddyfile" /etc/caddy/Caddyfile
  sudo caddy validate --config /etc/caddy/Caddyfile
  sudo systemctl reload caddy
fi
if test -s "$backup/rvc-current-target"; then
  previous=$(cat "$backup/rvc-current-target")
  sudo ln -sfn "$previous" /opt/voice-rvc/current.restore
  sudo mv -Tf /opt/voice-rvc/current.restore /opt/voice-rvc/current
fi
if test -f "$backup/voice-rvc.service"; then
  sudo cp "$backup/voice-rvc.service" /etc/systemd/system/voice-rvc.service
fi
sudo systemctl daemon-reload
if test -f "$backup/rvc-enabled" && grep -qx enabled "$backup/rvc-enabled"; then
  sudo systemctl enable voice-rvc.service
else
  sudo systemctl disable voice-rvc.service >/dev/null 2>&1 || true
fi
sudo systemctl restart voice-rvc.service || true
sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml up -d --no-build --force-recreate || true
echo ROLLBACK_COMPLETE
REMOTE_ROLLBACK
  fi
  exit "$status"
}
trap rollback EXIT

required=(
  .dockerignore Dockerfile compose.yaml requirements.txt requirements.lock
  app web rvc_service rvc_service/requirements.lock deploy/Caddyfile
  deploy/voice-rvc.service tests/live-rvc.py
)
for path in "${required[@]}"; do
  test -e "$path" || { echo "Missing rollout input: $path" >&2; exit 2; }
done

if [ "${DEPLOY_RVC_SKIP_CHECKS:-0}" != 1 ]; then
  .venv/bin/pytest -q
  node --test tests/*.test.cjs
  git diff --check
fi

ssh "$target" "rm -rf '$stage' && mkdir -p '$stage'"
rsync -a --relative --exclude='__pycache__' --exclude='*.pyc' \
  .dockerignore Dockerfile compose.yaml requirements.txt requirements.lock \
  app web rvc_service deploy/Caddyfile deploy/voice-rvc.service tests/live-rvc.py \
  "$target:$stage/"

# From this point any failed gate restores only this project's image/source,
# native Caddy file and prior RVC release/service state.
rollback_required=1
ssh "$target" sudo bash -s -- "$stamp" "$stage" <<'REMOTE_PREPARE'
set -euo pipefail
stamp=$1
stage=$2
backup="/opt/voice-rvc/backups/$stamp"
release="/opt/voice-rvc/releases/$stamp"
assets=/opt/voice-changer/experiments/phoneguy

sudo install -d -m 0750 "$backup"
sudo cp /etc/caddy/Caddyfile "$backup/Caddyfile"
sudo systemctl is-enabled voice-rvc.service > "$backup/rvc-enabled" 2>/dev/null || true
sudo readlink -f /opt/voice-rvc/current > "$backup/rvc-current-target" || true
if test -f /etc/systemd/system/voice-rvc.service; then
  sudo cp /etc/systemd/system/voice-rvc.service "$backup/voice-rvc.service"
fi
sudo tar -czf "$backup/voice-source.tar.gz" -C /opt/voice-changer \
  app web deploy/Caddyfile Dockerfile .dockerignore requirements.txt requirements.lock compose.yaml
if sudo docker image inspect voice-changer:current >/dev/null 2>&1; then
  sudo docker tag voice-changer:current "voice-changer:rollback-$stamp"
fi

sudo install -d -o voice-rvc -g voice-rvc "$release" "$release/tests"
sudo rsync -a --delete "$stage/rvc_service/" "$release/rvc_service/"
sudo install -o voice-rvc -g voice-rvc -m 0644 \
  "$stage/rvc_service/requirements.lock" "$release/requirements.lock"
sudo install -o voice-rvc -g voice-rvc -m 0755 \
  "$stage/tests/live-rvc.py" "$release/tests/live-rvc.py"
sudo chown -R voice-rvc:voice-rvc "$release"
sudo chmod -R u=rwX,go=rX "$release"

sudo -u voice-rvc test -r "$assets/upstream/configs/config.py"
sudo -u voice-rvc test -r "$assets/models/PhoneGuyfnaf1V1.pth"
sudo -u voice-rvc test -r "$assets/models/added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
sudo -u voice-rvc test -r "$assets/upstream/assets/hubert_base"
sudo -u voice-rvc test -r "$assets/upstream/assets/rmvpe/rmvpe.pt"

lock_sha=$(sha256sum "$release/requirements.lock" | cut -d' ' -f1)
marker=/opt/voice-rvc/venv/.rvc-requirements.sha256
if ! test -x /opt/voice-rvc/venv/bin/python; then
  sudo install -d -o voice-rvc -g voice-rvc /opt/voice-rvc
  sudo -u voice-rvc python3 -m venv /opt/voice-rvc/venv
fi
pins_match=0
if sudo -u voice-rvc /opt/voice-rvc/venv/bin/python - "$release/requirements.lock" <<'PY_LOCK'
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

for raw in Path(sys.argv[1]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    name, expected = line.split("==", 1)
    try:
        installed = version(name)
    except PackageNotFoundError:
        raise SystemExit(1)
    if installed != expected:
        raise SystemExit(1)
PY_LOCK
then
  pins_match=1
fi
if test "$pins_match" -ne 1; then
  sudo -u voice-rvc /opt/voice-rvc/venv/bin/python -m pip install \
    --extra-index-url https://download.pytorch.org/whl/cu118 \
    -r "$release/requirements.lock"
fi
if ! test -f "$marker" || ! grep -qx "$lock_sha" "$marker"; then
  printf '%s\n' "$lock_sha" | sudo -u voice-rvc tee "$marker" >/dev/null
fi
sudo -u voice-rvc /opt/voice-rvc/venv/bin/python -m pip check

sudo install -m 0644 "$stage/deploy/voice-rvc.service" /etc/systemd/system/voice-rvc.service
sudo ln -sfn "$release" /opt/voice-rvc/current.next
sudo mv -Tf /opt/voice-rvc/current.next /opt/voice-rvc/current
sudo systemctl daemon-reload
sudo systemctl restart voice-rvc.service
for attempt in $(seq 1 95); do
  if curl -fsS http://127.0.0.1:8090/healthz | grep -q '"status":"ready"'; then
    break
  fi
  test "$attempt" -lt 95 || exit 1
  sleep 1
done
sudo -u voice-rvc /opt/voice-rvc/venv/bin/python "$release/tests/live-rvc.py" \
  --url ws://127.0.0.1:8090/ws/rvc \
  --origin https://voice.lan.awesomeio.ru \
  --samples-dir "$assets/samples/input" --seconds 2 \
  --skip-health --skip-runtime-stats > "$backup/loopback-gate.json"
REMOTE_PREPARE

ssh "$target" sudo bash -s -- "$stamp" "$stage" <<'REMOTE_SWITCH'
set -euo pipefail
stamp=$1
stage=$2
candidate="/etc/caddy/Caddyfile.rvc-$stamp"

# Only the UI inputs are updated. The experiment tree is neither copied nor
# used as a Docker context after the deployed .dockerignore is installed.
sudo rsync -a --delete "$stage/web/" /opt/voice-changer/web/
sudo install -m 0644 "$stage/.dockerignore" /opt/voice-changer/.dockerignore
sudo docker build -t "voice-changer:rvc-$stamp" /opt/voice-changer

sudo install -m 0644 "$stage/deploy/Caddyfile" "$candidate"
sudo caddy validate --config "$candidate"
sudo install -m 0644 "$stage/deploy/Caddyfile" /opt/voice-changer/deploy/Caddyfile
sudo cp "$candidate" /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo docker tag "voice-changer:rvc-$stamp" voice-changer:current
sudo docker compose -p voice-changer -f /opt/voice-changer/compose.yaml \
  up -d --no-build --force-recreate
sudo systemctl enable voice-rvc.service
REMOTE_SWITCH

healthy=0
for attempt in $(seq 1 "$health_attempts"); do
  if curl --max-time 5 -fsS https://voice.lan.awesomeio.ru/healthz >/dev/null \
      && curl --max-time 5 -fsS https://vm-voice-1.lan.awesomeio.ru/healthz >/dev/null; then
    healthy=1
    break
  fi
  sleep "$health_delay"
done
test "$healthy" -eq 1

if [ -n "${DEPLOY_RVC_LIVE_CLIENT:-}" ]; then
  "$DEPLOY_RVC_LIVE_CLIENT"
else
  .venv/bin/python tests/live-rvc.py --seconds "$smoke_seconds"
fi

# A manual worker restart must warm cleanly and admit a new private-WSS session.
ssh "$target" sudo bash -s <<'REMOTE_RESTART'
set -euo pipefail
sudo systemctl restart voice-rvc.service
for attempt in $(seq 1 95); do
  if curl -fsS http://127.0.0.1:8090/healthz | grep -q '"status":"ready"'; then
    exit 0
  fi
  test "$attempt" -lt 95 || exit 1
  sleep 1
done
REMOTE_RESTART
if [ -n "${DEPLOY_RVC_LIVE_CLIENT:-}" ]; then
  "$DEPLOY_RVC_LIVE_CLIENT"
else
  .venv/bin/python tests/live-rvc.py --seconds 2
fi

rollback_required=0
ssh "$target" "rm -rf '$stage'" || echo "Warning: remote stage remains at $stage" >&2
echo "RVC rollout ready. Rollback stamp: $stamp"
echo "Backup: $target:$backup"
