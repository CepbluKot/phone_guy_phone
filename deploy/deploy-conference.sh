#!/usr/bin/env bash
# Deploy only the Asterisk listening demo and the HTTP source it consumes.
set -euo pipefail

approved_target=ubuntu@192.168.20.70
script_path=$(readlink -f "$0")

validate_stamp() {
  [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || {
    echo "Invalid conference release stamp; expected YYYYMMDDTHHMMSSZ" >&2
    return 2
  }
}

verify_ari_events_module() {
  grep -E '^res_ari_events\.so[[:space:]].*[[:space:]][[:digit:]]+[[:space:]]+Running[[:space:]]'
}

remote_rollback() {
  stamp=$1
  root_prefix=${DEPLOY_CONFERENCE_ROOT_PREFIX:-}
  if [ -n "$root_prefix" ] && { [[ "$root_prefix" != /* ]] || [ "$root_prefix" = / ]; }; then
    echo "Unsafe conference rollback root prefix" >&2
    return 2
  fi
  path() { printf '%s%s' "$root_prefix" "$1"; }
  backup=$(path "/opt/voice-conference/backups/$stamp")
  conference_root=$(path /opt/voice-conference)
  http_root=$(path /opt/voice-changer)
  live_caddy=$(path /etc/caddy/Caddyfile)
  failed=0

  echo "ROLLBACK_BEGIN stamp=$stamp"
  if [ ! -d "$backup" ]; then
    echo "ROLLBACK_FAILED missing backup: $backup" >&2
    return 1
  fi
  docker compose -p voice-conference -f "$conference_root/releases/$stamp/deploy/compose.conference.yaml" \
      --env-file "$conference_root/releases/$stamp/.env" down --remove-orphans || failed=1
  if ! docker image inspect "voice-changer:conference-rollback-$stamp" >/dev/null 2>&1 \
      || ! docker tag "voice-changer:conference-rollback-$stamp" voice-changer:current; then
    echo "rollback: prior HTTP image is unavailable" >&2
    failed=1
  fi
  if ! tar -xzf "$backup/http-source.tar.gz" -C "$http_root" \
      || ! cp "$backup/Caddyfile" "$live_caddy" \
      || ! cp "$backup/source-Caddyfile" "$http_root/deploy/Caddyfile"; then
    echo "rollback: source or Caddy restore failed" >&2
    failed=1
  fi
  if ! caddy validate --config "$live_caddy" || ! systemctl reload caddy; then
    echo "rollback: Caddy restore validation/reload failed" >&2
    failed=1
  fi
  if ! docker compose -p voice-changer -f "$http_root/compose.yaml" \
      up -d --no-build --force-recreate; then
    echo "rollback: HTTP container restore failed" >&2
    failed=1
  fi
  healthy=0
  for attempt in $(seq 1 "${DEPLOY_CONFERENCE_ROLLBACK_HEALTH_ATTEMPTS:-20}"); do
    if curl --max-time 5 -fsS http://127.0.0.1:8090/healthz | grep -q '"status":"ready"' \
        && curl --max-time 5 -fsS http://192.168.20.70:8080/healthz >/dev/null; then
      healthy=1
      break
    fi
    sleep "${DEPLOY_CONFERENCE_ROLLBACK_HEALTH_DELAY:-1}"
  done
  if [ "$healthy" -ne 1 ]; then
    echo "rollback: old RVC/HTTP health check failed" >&2
    failed=1
  fi
  if [ "$failed" -ne 0 ]; then
    echo "ROLLBACK_FAILED stamp=$stamp" >&2
    return 1
  fi
  echo "ROLLBACK_COMPLETE stamp=$stamp"
}

case "${1:-}" in
  --verify-ari-events-module)
    test "$#" -eq 1 || exit 2
    verify_ari_events_module
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
target=${DEPLOY_CONFERENCE_TARGET:-$approved_target}
if [ "$target" != "$approved_target" ]; then
  echo "DEPLOY_CONFERENCE_TARGET must be the approved VM209: $approved_target" >&2
  exit 2
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: $0" >&2
  exit 2
fi
git_common_dir=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)
main_repo=$(dirname "$git_common_dir")
if [ -x "$repo/.venv/bin/python" ]; then
  python_bin="$repo/.venv/bin/python"
elif [ -x "$main_repo/.venv/bin/python" ]; then
  python_bin="$main_repo/.venv/bin/python"
else
  echo "No project Python environment found for conference checks" >&2
  exit 2
fi

stamp=${DEPLOY_CONFERENCE_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
validate_stamp "$stamp"
stage="/tmp/voice-conference-stage-$stamp"
backup="/opt/voice-conference/backups/$stamp"
rollback_required=0

run_rollback() {
  # A preflight can fail before the remote side has made a snapshot. In that
  # case there is nothing owned by this rollout to restore; do not turn the
  # useful preflight error into a misleading rollback failure.
  rollback_probe_status=0
  ssh "$target" "test -d '$backup'" || rollback_probe_status=$?
  if [ "$rollback_probe_status" -eq 1 ]; then
    echo "No conference snapshot was created; rollback is not required" >&2
    return 0
  fi
  if [ "$rollback_probe_status" -ne 0 ]; then
    echo "Unable to determine whether conference rollback is required" >&2
    return 1
  fi
  if ! ssh "$target" sudo env -u DEPLOY_CONFERENCE_ROOT_PREFIX bash -s -- \
      --remote-rollback "$stamp" < "$script_path"; then
    echo "ROLLBACK FAILED on $target for stamp $stamp" >&2
    return 1
  fi
}

rollback_on_error() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollback_required" -eq 1 ]; then
    echo "conference rollout failed; restoring $backup" >&2
    run_rollback || exit 70
  fi
  exit "$status"
}
trap rollback_on_error EXIT

required=(
  .dockerignore Dockerfile compose.yaml requirements.txt requirements.lock app web
  conference deploy/Caddyfile deploy/compose.conference.yaml deploy/deploy-conference.sh deploy/voice-routing.yaml
  tests/live-conference.py tests/live-sip-preflight.py tests/fixtures/sipp-auth-conference.xml
)
for item in "${required[@]}"; do
  test -e "$item" || { echo "Missing rollout input: $item" >&2; exit 2; }
done
if [ "${DEPLOY_CONFERENCE_SKIP_CHECKS:-0}" != 1 ]; then
  "$python_bin" -m pytest -q
  node --test tests/*.test.cjs
  git diff --check
fi

ssh "$target" "rm -rf '$stage' && mkdir -p '$stage'"
rsync -a --relative --exclude='__pycache__' --exclude='*.pyc' \
  .dockerignore Dockerfile compose.yaml requirements.txt requirements.lock app web conference \
  deploy/Caddyfile deploy/compose.conference.yaml deploy/deploy-conference.sh deploy/voice-routing.yaml \
  tests/live-conference.py tests/live-sip-preflight.py tests/fixtures/sipp-auth-conference.xml "$target:$stage/"

# run_rollback first checks for the snapshot, so this is harmless for an early
# preflight failure and protects failures after the remote snapshot is made.
rollback_required=1
ssh "$target" sudo bash -s -- "$stamp" "$stage" <<'REMOTE_PREPARE'
set -euo pipefail
stamp=$1
stage=$2
root=/opt/voice-conference
backup="$root/backups/$stamp"
release="$root/releases/$stamp"
runtime="$root/runtime/$stamp"
fixtures="$root/fixtures/$stamp"

for command in docker caddy ss espeak-ng ffmpeg openssl; do command -v "$command" >/dev/null; done
test "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)" -ge $((1300 * 1024)) \
  || { echo "Unsafe memory headroom before build" >&2; exit 1; }
test "$(df -Pm /opt | awk 'NR==2 {print $4}')" -ge 4096 \
  || { echo "Unsafe disk headroom before build" >&2; exit 1; }
conference_contour_is_idle() {
  docker ps --format '{{.Names}}' | grep -Fxq voice-conference-asterisk-1 \
    && docker ps --format '{{.Names}}' | grep -Fxq voice-conference-controller-1 \
    && curl -fsS http://127.0.0.1:8091/healthz | grep -q '"status":"idle"'
}
ports_in_use=0
for port in 8091 8092; do
  ss -ltn "sport = :$port" | grep -q LISTEN && ports_in_use=1
done
if [ "$ports_in_use" -eq 1 ] && ! conference_contour_is_idle; then
  echo "Required loopback port is already in use by a non-idle conference contour" >&2
  exit 1
fi
curl -fsS http://127.0.0.1:8090/healthz | python3 -c '
import json, sys
v=json.load(sys.stdin)
queued = v.get("queuedWindows", v.get("queue"))
assert v.get("status") == "ready" and not v.get("active") and not v.get("running") and queued == 0
'

install -d -m 0750 "$backup" "$release" "$runtime/asterisk" "$fixtures"
systemctl show voice-rvc.service \
  -p NRestarts -p ExecMainStartTimestampMonotonic \
  > "$backup/voice-rvc-service-state-before"
cp /etc/caddy/Caddyfile "$backup/Caddyfile"
cp /opt/voice-changer/deploy/Caddyfile "$backup/source-Caddyfile"
docker image inspect voice-changer:current --format '{{.Id}}' > "$backup/prior-http-image-id"
docker tag voice-changer:current "voice-changer:conference-rollback-$stamp"
tar -C /opt/voice-changer -czf "$backup/http-source.tar.gz" \
  Dockerfile .dockerignore compose.yaml requirements.txt requirements.lock app web deploy/Caddyfile

rsync -a --delete "$stage/" "$release/"
chmod -R u=rwX,go=rX "$release"
PYTHONPATH="$release" python3 -m conference.scenario "$fixtures"
test -s "$fixtures/A.pcm" && test -s "$fixtures/B.pcm" && test -s "$fixtures/C.pcm"
# The controller runs as unprivileged UID 10001.  Fixtures are generated by
# root during deployment, so make the release-specific directory traversable
# and the PCM inputs readable without granting any write access.
chmod 0755 "$fixtures"
chmod 0644 "$fixtures"/*.pcm
password=$(openssl rand -hex 32)
printf '%s\n' "$password" > "$runtime/asterisk/ari-password"
sed "s/__ARI_PASSWORD__/$password/" "$release/conference/asterisk/ari.conf.template" > "$runtime/asterisk/ari.conf"
sed 's/^bindaddr=.*/bindaddr=0.0.0.0/' "$release/conference/asterisk/http.conf" > "$runtime/asterisk/http.conf"
cp "$release/conference/asterisk/pjsip.conf.template" "$runtime/asterisk/pjsip.conf"
install -m 0644 "$release/conference/asterisk/extensions.conf" "$runtime/asterisk/extensions.conf"
install -m 0644 "$release/conference/asterisk/modules.conf" "$runtime/asterisk/modules.conf"
install -d -m 0755 "$runtime/asterisk/sounds"
install -m 0644 "$release/conference/asterisk/sounds/phoneguy.wav" "$runtime/asterisk/sounds/phoneguy.wav"
install -m 0644 "$release/conference/asterisk/sounds/scary-music.wav" "$runtime/asterisk/sounds/scary-music.wav"
install -m 0644 "$release/conference/asterisk/sounds/mr-beast-phoneguy.wav" "$runtime/asterisk/sounds/mr-beast-phoneguy.wav"
install -m 0644 "$release/conference/asterisk/sounds/fnaf1-night1-original.wav" "$runtime/asterisk/sounds/fnaf1-night1-original.wav"
for extension in 1983 1987 2014; do
  password=$(openssl rand -hex 32)
  printf '%s\n' "$password" > "$runtime/asterisk/sip-$extension-password"
  sed -i "s/__SIP_${extension}_PASSWORD__/$password/" "$runtime/asterisk/pjsip.conf"
done
install -m 0644 "$release/deploy/voice-routing.yaml" "$runtime/voice-routing.yaml"
chown -R 10001:10001 "$runtime/asterisk"
chmod 0600 "$runtime/asterisk/ari.conf" "$runtime/asterisk/ari-password" "$runtime/asterisk/pjsip.conf" "$runtime/asterisk"/sip-*-password
chmod 0644 "$runtime/asterisk/http.conf"

monitor="$backup/asterisk-build-mem-kib"
docker build -f "$release/conference/asterisk/Dockerfile" -t "voice-conference-asterisk:$stamp" "$release/conference/asterisk" &
build_pid=$!
( while kill -0 "$build_pid" 2>/dev/null; do awk '/MemAvailable:/ {print $2}' /proc/meminfo >> "$monitor"; sleep 1; done ) &
watch_pid=$!
wait "$build_pid"
wait "$watch_pid" || true
minimum=$(awk 'NR == 1 || $1 < minimum { minimum=$1 } END { print minimum + 0 }' "$monitor")
test "$minimum" -ge $((700 * 1024)) || { echo "Unsafe memory headroom during Asterisk build: ${minimum}KiB" >&2; exit 1; }
docker build -f "$release/conference/Dockerfile" -t "voice-conference-controller:$stamp" "$release"

cat > "$release/.env" <<EOF
CONFERENCE_TAG=$stamp
CONFERENCE_RUNTIME=$runtime
CONFERENCE_FIXTURES=$fixtures
EOF
chmod 0600 "$release/.env"
REMOTE_PREPARE

ssh "$target" sudo bash -s -- "$stamp" <<'REMOTE_SWITCH'
set -euo pipefail
stamp=$1
root=/opt/voice-conference
release="$root/releases/$stamp"
candidate="/etc/caddy/Caddyfile.conference-$stamp"
rsync -a --delete "$release/app/" /opt/voice-changer/app/
rsync -a --delete "$release/web/" /opt/voice-changer/web/
install -m 0644 "$release/Dockerfile" /opt/voice-changer/Dockerfile
install -m 0644 "$release/.dockerignore" /opt/voice-changer/.dockerignore
install -m 0644 "$release/compose.yaml" /opt/voice-changer/compose.yaml
install -m 0644 "$release/requirements.txt" /opt/voice-changer/requirements.txt
install -m 0644 "$release/requirements.lock" /opt/voice-changer/requirements.lock
docker build -t "voice-changer:conference-$stamp" /opt/voice-changer
docker tag "voice-changer:conference-$stamp" voice-changer:current
docker compose -p voice-changer -f /opt/voice-changer/compose.yaml up -d --no-build --force-recreate
docker compose -p voice-conference -f "$release/deploy/compose.conference.yaml" --env-file "$release/.env" up -d --no-build
for attempt in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8091/healthz | grep -q '"status":"idle"' && break
  test "$attempt" -lt 30 || { echo "Conference controller did not become idle" >&2; exit 1; }
  sleep 1
done
docker exec "voice-conference-asterisk-1" asterisk -rx "module show like res_ari_events" \
  | "$release/deploy/deploy-conference.sh" --verify-ari-events-module
docker exec "voice-conference-asterisk-1" /usr/local/bin/asterisk-healthcheck
docker exec -i "voice-conference-controller-1" python - <<'PY'
import asyncio
import base64
from urllib.parse import urlencode
import uuid

from websockets.asyncio.client import connect


async def main():
    password = open("/run/conference/ari-password", encoding="utf-8").read().strip()
    token = base64.b64encode(f"phoneguy:{password}".encode()).decode()
    url = "ws://127.0.0.1:8092/ari/events?" + urlencode(
        {"app": "conference-preflight-" + uuid.uuid4().hex}
    )
    async with connect(
        url,
        additional_headers={"Authorization": "Basic " + token},
        open_timeout=10,
        close_timeout=2,
    ):
        print("ARI_EVENTS_WEBSOCKET_OK")


asyncio.run(main())
PY
install -m 0644 "$release/deploy/Caddyfile" "$candidate"
caddy validate --config "$candidate"
install -m 0644 "$release/deploy/Caddyfile" /opt/voice-changer/deploy/Caddyfile
cp "$candidate" /etc/caddy/Caddyfile
systemctl reload caddy
ss -ltn | grep -F '127.0.0.1:8091' >/dev/null
ss -ltn | grep -F '127.0.0.1:8092' >/dev/null
docker exec "voice-conference-asterisk-1" asterisk -V
REMOTE_SWITCH

healthy=0
for attempt in $(seq 1 "${DEPLOY_CONFERENCE_HEALTH_ATTEMPTS:-20}"); do
  if curl --max-time 5 -fsS https://voice.lan.awesomeio.ru/healthz >/dev/null \
      && curl --max-time 5 -fsS https://vm-voice-1.lan.awesomeio.ru/healthz >/dev/null; then
    healthy=1; break
  fi
  sleep "${DEPLOY_CONFERENCE_HEALTH_DELAY:-2}"
done
test "$healthy" -eq 1
if [ -n "${DEPLOY_CONFERENCE_LIVE_CLIENT:-}" ]; then
  "$DEPLOY_CONFERENCE_LIVE_CLIENT"
else
  "$python_bin" tests/live-conference.py --seconds 10
fi
ssh "$target" sudo bash -s -- "$stamp" <<'REMOTE_SIP_ONLY'
set -euo pipefail
stamp=$1
before=/opt/voice-conference/backups/$stamp/voice-rvc-service-state-before
after=$(mktemp)
trap 'rm -f "$after"' EXIT
systemctl show voice-rvc.service \
  -p NRestarts -p ExecMainStartTimestampMonotonic > "$after"
cmp -s "$before" "$after" || {
  echo "voice-rvc.service changed during conference deploy" >&2
  exit 1
}
docker update --restart=no voice-changer-voice-1 >/dev/null
docker stop voice-changer-voice-1 >/dev/null
test "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}')" = healthy
test "$(docker inspect voice-conference-controller-1 --format '{{.State.Health.Status}}')" = healthy
curl -fsS http://127.0.0.1:8090/healthz | python3 -c '
import json, sys
value = json.load(sys.stdin)
assert value == {"status": "ready", "active": False, "running": False, "queuedWindows": 0}
'
REMOTE_SIP_ONLY
echo "DEPLOY_COMPLETE stamp=$stamp backup=$backup"
