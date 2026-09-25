#!/usr/bin/env bash
set -euo pipefail

approved_target=ubuntu@192.168.20.70
script_path=$(readlink -f "$0")

validate_stamp() {
  if [[ ! "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
    echo "Invalid release stamp; expected YYYYMMDDTHHMMSSZ" >&2
    return 2
  fi
}

verify_venv() {
  lock=$1
  python=$2
  test -r "$lock" || { echo "Pinned RVC lock is not readable: $lock" >&2; return 1; }
  test -x "$python" || { echo "Existing RVC venv is unavailable: $python" >&2; return 1; }
  PYTHONDONTWRITEBYTECODE=1 "$python" - "$lock" <<'PY_LOCK'
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
        raise SystemExit(f"missing pinned package: {name}")
    if installed != expected:
        raise SystemExit(f"pin mismatch: {name} installed={installed} expected={expected}")
PY_LOCK
  PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 "$python" -m pip check
}

remote_rollback() {
  stamp=$1
  root_prefix=${DEPLOY_RVC_ROOT_PREFIX:-}
  if [ -n "$root_prefix" ] && { [[ "$root_prefix" != /* ]] || [ "$root_prefix" = / ]; }; then
    echo "Unsafe rollback root prefix" >&2
    return 2
  fi
  p() { printf '%s%s' "$root_prefix" "$1"; }

  backup=$(p "/opt/voice-rvc/backups/$stamp")
  voice_root=$(p /opt/voice-changer)
  if [ -e "$voice_root/.go-runtime-owner" ]; then
    echo "Go runtime owns VM209; legacy deployment refused" >&2
    return 2
  fi
  rvc_root=$(p /opt/voice-rvc)
  live_caddy=$(p /etc/caddy/Caddyfile)
  live_unit=$(p /etc/systemd/system/voice-rvc.service)
  failed=0

  echo "ROLLBACK_BEGIN stamp=$stamp"
  if [ ! -d "$backup" ]; then
    echo "ROLLBACK_FAILED missing backup: $backup" >&2
    return 1
  fi

  if ! journalctl -u voice-rvc.service --since "-30 min" --no-pager \
      > "$backup/voice-rvc-failure.log" 2>&1; then
    echo "rollback: could not retain voice-rvc journal" >&2
    failed=1
  fi
  if ! docker logs --since 30m voice-changer-voice-1 \
      > "$backup/voice-ui-failure.log" 2>&1; then
    echo "rollback: could not retain UI log" >&2
    failed=1
  fi

  if docker image inspect "voice-changer:rollback-$stamp" >/dev/null 2>&1; then
    if ! docker tag "voice-changer:rollback-$stamp" voice-changer:current; then
      echo "rollback: image tag restore failed" >&2
      failed=1
    fi
  else
    echo "rollback: rollback image is missing" >&2
    failed=1
  fi

  if [ -f "$backup/web.tar.gz" ]; then
    if ! rm -rf "$voice_root/web" || ! mkdir -p "$voice_root" \
        || ! tar -xzf "$backup/web.tar.gz" -C "$voice_root"; then
      echo "rollback: exact web tree restore failed" >&2
      failed=1
    fi
  else
    echo "rollback: web backup is missing" >&2
    failed=1
  fi
  if ! cp "$backup/.dockerignore" "$voice_root/.dockerignore"; then
    echo "rollback: .dockerignore restore failed" >&2
    failed=1
  fi
  if ! cp "$backup/source-Caddyfile" "$voice_root/deploy/Caddyfile"; then
    echo "rollback: source Caddyfile restore failed" >&2
    failed=1
  fi
  if ! cp "$backup/Caddyfile" "$live_caddy"; then
    echo "rollback: live Caddyfile restore failed" >&2
    failed=1
  fi

  previous=$(sed -n '1p' "$backup/rvc-current-target" 2>/dev/null || true)
  if [ -z "$previous" ] || ! rm -f "$rvc_root/current" \
      || ! ln -s "$previous" "$rvc_root/current"; then
    echo "rollback: RVC release link restore failed" >&2
    failed=1
  fi
  if [ -f "$backup/voice-rvc.service" ]; then
    if ! cp "$backup/voice-rvc.service" "$live_unit"; then
      echo "rollback: service unit restore failed" >&2
      failed=1
    fi
  elif ! rm -f "$live_unit"; then
    echo "rollback: new service unit removal failed" >&2
    failed=1
  fi

  if ! systemctl daemon-reload; then
    echo "rollback: systemd reload failed" >&2
    failed=1
  fi
  previous_enabled=$(sed -n '1p' "$backup/rvc-enabled" 2>/dev/null || true)
  if [ "$previous_enabled" = enabled ]; then
    if ! systemctl enable voice-rvc.service; then failed=1; fi
  elif [ "$previous_enabled" = disabled ]; then
    if ! systemctl disable voice-rvc.service; then failed=1; fi
  else
    echo "rollback: invalid previous enabled state" >&2
    failed=1
  fi

  if ! caddy validate --config "$live_caddy"; then
    echo "rollback: restored Caddy validation failed" >&2
    failed=1
  elif ! systemctl reload caddy; then
    echo "rollback: Caddy reload failed" >&2
    failed=1
  fi
  if ! docker compose -p voice-changer -f "$voice_root/compose.yaml" \
      up -d --no-build --force-recreate; then
    echo "rollback: container restore failed" >&2
    failed=1
  fi

  previous_active=$(sed -n '1p' "$backup/rvc-active" 2>/dev/null || true)
  if [ "$previous_active" = active ]; then
    if ! systemctl restart voice-rvc.service; then
      echo "rollback: RVC service restart failed" >&2
      failed=1
    fi
  elif [ "$previous_active" = inactive ]; then
    if ! systemctl stop voice-rvc.service; then
      echo "rollback: RVC service stop failed" >&2
      failed=1
    fi
  else
    echo "rollback: invalid previous active state" >&2
    failed=1
  fi

  expected_image=$(sed -n '1p' "$backup/prior-image-id" 2>/dev/null || true)
  actual_image=$(docker image inspect voice-changer:current --format '{{.Id}}' 2>/dev/null || true)
  if [ -z "$expected_image" ] || [ "$actual_image" != "$expected_image" ]; then
    echo "rollback: final image identity does not match backup" >&2
    failed=1
  fi
  if ! cmp -s "$backup/Caddyfile" "$live_caddy" \
      || ! cmp -s "$backup/source-Caddyfile" "$voice_root/deploy/Caddyfile" \
      || ! cmp -s "$backup/.dockerignore" "$voice_root/.dockerignore"; then
    echo "rollback: final Caddy/source files do not match backup" >&2
    failed=1
  fi
  if { [ -f "$backup/voice-rvc.service" ] \
        && ! cmp -s "$backup/voice-rvc.service" "$live_unit"; } \
      || { [ ! -f "$backup/voice-rvc.service" ] && [ -e "$live_unit" ]; }; then
    echo "rollback: final service unit does not match backup" >&2
    failed=1
  fi
  if [ "$(readlink "$rvc_root/current" 2>/dev/null || true)" != "$previous" ]; then
    echo "rollback: final RVC release target does not match backup" >&2
    failed=1
  fi
  if [ "$(systemctl is-enabled voice-rvc.service 2>/dev/null || true)" != "$previous_enabled" ] \
      || [ "$(systemctl is-active voice-rvc.service 2>/dev/null || true)" != "$previous_active" ]; then
    echo "rollback: final service state does not match backup" >&2
    failed=1
  fi

  healthy=0
  rollback_health_attempts=${DEPLOY_RVC_ROLLBACK_HEALTH_ATTEMPTS:-20}
  rollback_health_delay=${DEPLOY_RVC_ROLLBACK_HEALTH_DELAY:-2}
  for attempt in $(seq 1 "$rollback_health_attempts"); do
    if curl --max-time 5 -fsS http://192.168.20.70:8080/healthz >/dev/null \
        && curl --max-time 5 -fsS https://vm-voice-1.lan.awesomeio.ru/healthz >/dev/null; then
      healthy=1
      break
    fi
    sleep "$rollback_health_delay"
  done
  if [ "$healthy" -ne 1 ]; then
    echo "rollback: restored health routes failed" >&2
    failed=1
  fi

  if [ "$failed" -ne 0 ]; then
    echo "ROLLBACK_FAILED stamp=$stamp" >&2
    return 1
  fi
  echo "ROLLBACK_COMPLETE stamp=$stamp"
}

case "${1:-}" in
  --verify-venv)
    test "$#" -eq 3 || exit 2
    verify_venv "$2" "$3"
    exit
    ;;
  --remote-rollback)
    test "$#" -eq 2 || exit 2
    validate_stamp "$2"
    remote_rollback "$2"
    exit
    ;;
esac

repo=$(cd "$(dirname "$script_path")/.." && pwd)
cd "$repo"
target=${DEPLOY_RVC_TARGET:-$approved_target}
if [ "$target" != "$approved_target" ]; then
  echo "DEPLOY_RVC_TARGET must be the approved VM209: $approved_target" >&2
  exit 2
fi

run_rollback() {
  rollback_stamp=$1
  if ! ssh "$target" sudo env -u DEPLOY_RVC_ROOT_PREFIX bash -s -- \
      --remote-rollback "$rollback_stamp" < "$script_path"; then
    echo "ROLLBACK FAILED on $target for stamp $rollback_stamp" >&2
    return 1
  fi
  if ! curl --max-time 5 -fsS https://voice.lan.awesomeio.ru/healthz >/dev/null \
      || ! curl --max-time 5 -fsS https://vm-voice-1.lan.awesomeio.ru/healthz >/dev/null; then
    echo "ROLLBACK FAILED final private HTTPS health check" >&2
    return 1
  fi
}

if [ "${1:-}" = rollback ]; then
  test "$#" -eq 2 || { echo "Usage: $0 rollback <YYYYMMDDTHHMMSSZ>" >&2; exit 2; }
  validate_stamp "$2"
  if run_rollback "$2"; then
    echo "Rollback verified for stamp $2"
    exit 0
  fi
  exit 70
elif [ "$#" -ne 0 ]; then
  echo "Usage: $0 [rollback <YYYYMMDDTHHMMSSZ>]" >&2
  exit 2
fi

stamp=${DEPLOY_RVC_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
validate_stamp "$stamp"
if ! ssh "$target" 'sudo test ! -e /opt/voice-changer/.go-runtime-owner'; then
  echo "Go runtime owns VM209; legacy deployment refused" >&2
  exit 2
fi
stage="/tmp/voice-rvc-release-$stamp"
backup="/opt/voice-rvc/backups/$stamp"
health_attempts=${DEPLOY_RVC_HEALTH_ATTEMPTS:-20}
health_delay=${DEPLOY_RVC_HEALTH_DELAY:-2}
smoke_seconds=${DEPLOY_RVC_SMOKE_SECONDS:-10}
rollback_required=0

rollback_on_exit() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollback_required" -eq 1 ]; then
    echo "RVC rollout failed; restoring backup $backup" >&2
    if ! run_rollback "$stamp"; then
      exit 70
    fi
  fi
  exit "$status"
}
trap rollback_on_exit EXIT

required=(
  .dockerignore Dockerfile compose.yaml requirements.txt requirements.lock
  app web rvc_service rvc_service/requirements.lock deploy/Caddyfile
  deploy/deploy-rvc.sh deploy/voice-rvc.service tests/live-rvc.py
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
  app web rvc_service deploy/Caddyfile deploy/deploy-rvc.sh \
  deploy/voice-rvc.service tests/live-rvc.py "$target:$stage/"

rollback_required=1
ssh "$target" sudo bash -s -- "$stamp" "$stage" <<'REMOTE_PREPARE'
set -euo pipefail
stamp=$1
stage=$2
if [ -e /opt/voice-changer/.go-runtime-owner ]; then
  echo "Go runtime owns VM209; legacy deployment refused" >&2
  exit 2
fi
backup="/opt/voice-rvc/backups/$stamp"
release="/opt/voice-rvc/releases/$stamp"
assets=/opt/voice-changer/experiments/phoneguy

sudo install -d -m 0750 "$backup"
sudo cp /etc/caddy/Caddyfile "$backup/Caddyfile"
sudo cp /opt/voice-changer/.dockerignore "$backup/.dockerignore"
sudo cp /opt/voice-changer/deploy/Caddyfile "$backup/source-Caddyfile"
sudo tar -czf "$backup/web.tar.gz" -C /opt/voice-changer web
sudo systemctl is-enabled voice-rvc.service > "$backup/rvc-enabled" 2>/dev/null || true
sudo systemctl is-active voice-rvc.service > "$backup/rvc-active" 2>/dev/null || true
sudo readlink /opt/voice-rvc/current > "$backup/rvc-current-target"
if test -f /etc/systemd/system/voice-rvc.service; then
  sudo cp /etc/systemd/system/voice-rvc.service "$backup/voice-rvc.service"
fi
sudo docker image inspect voice-changer:current --format '{{.Id}}' > "$backup/prior-image-id"
sudo docker tag voice-changer:current "voice-changer:rollback-$stamp"

sudo install -d "$release" "$release/tests"
sudo rsync -a --delete "$stage/rvc_service/" "$release/rvc_service/"
sudo install -m 0644 "$stage/rvc_service/requirements.lock" "$release/requirements.lock"
sudo install -m 0755 "$stage/tests/live-rvc.py" "$release/tests/live-rvc.py"
sudo install -m 0755 "$stage/deploy/deploy-rvc.sh" "$release/deploy-rvc.sh"
sudo chown -R root:root "$release"
sudo chmod -R u=rwX,go=rX "$release"

sudo -u voice-rvc test -r "$assets/upstream/configs/config.py"
sudo -u voice-rvc test -r "$assets/models/PhoneGuyfnaf1V1.pth"
sudo -u voice-rvc test -r "$assets/models/added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
sudo -u voice-rvc test -r "$assets/upstream/assets/hubert_base"
sudo -u voice-rvc test -r "$assets/upstream/assets/rmvpe/rmvpe.pt"
sudo -u voice-rvc "$release/deploy-rvc.sh" --verify-venv \
  "$release/requirements.lock" /opt/voice-rvc/venv/bin/python

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
