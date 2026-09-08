#!/usr/bin/env bash
# Deploy only the experimental FCPE canary.  The production GPT v2 unit is
# observed before/after but is never stopped, restarted, or reconfigured here.
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${VOICE_RTRVC_TARGET:-ubuntu@192.168.20.70}"

if [[ "${1:-}" == "--check" ]]; then
    bash -n "$0"
    printf '%s\n' 'deploy-rtrvc-canary.sh syntax is valid'
    exit 0
fi

if ! [[ "$target" =~ ^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$ ]]; then
    printf '%s\n' "invalid VOICE_RTRVC_TARGET: $target" >&2
    exit 2
fi

test_python="${VOICE_RTRVC_TEST_PYTHON:-$repo_root/.venv/bin/python}"
if [[ ! -x "$test_python" ]]; then
    printf '%s\n' "test interpreter is not executable: $test_python" >&2
    exit 2
fi
"$test_python" -m pytest -q \
    "$repo_root/tests/test_rt_server.py" \
    "$repo_root/tests/test_rtrvc_service_config.py" \
    "$repo_root/tests/test_live_rtrvc_tool.py" \
    "$repo_root/tests/test_deploy_rtrvc_canary.py"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage="/tmp/voice-rtrvc-stage-$stamp"

ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "install -d -m 0755 '$stage'"
rsync -a --delete \
    "$repo_root/rvc_service" \
    "$repo_root/web-rt" \
    "$repo_root/tests/live-rtrvc.py" \
    "$repo_root/deploy/requirements-rtrvc.lock" \
    "$repo_root/deploy/voice-rtrvc-canary.service" \
    "$repo_root/deploy/Caddyfile" \
    "$target:$stage/"

ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
    "sudo bash -seuo pipefail -s -- '$stamp' '$stage'" <<'REMOTE'
stamp="$1"
stage="$2"
root=/opt/voice-rtrvc
release="$root/releases/$stamp"
current="$root/current"
backup="$root/backups/$stamp"
unit=/etc/systemd/system/voice-rtrvc-canary.service
caddyfile=/etc/caddy/Caddyfile
venv="$root/venv"
fcpe_pkgs="$root/fcpe_pkgs"
had_unit=0
had_current=0
prior_restarts="$(systemctl show voice-rvc.service -p NRestarts --value)"

rollback() {
    status=$?
    set +e
    systemctl disable --now voice-rtrvc-canary.service
    if [[ "$had_unit" == 1 ]]; then
        install -m 0644 "$backup/voice-rtrvc-canary.service" "$unit"
    else
        rm -f "$unit"
    fi
    if [[ "$had_current" == 1 ]]; then
        ln -sfn "$(<"$backup/current-target")" "$current"
    else
        rm -f "$current"
    fi
    install -m 0644 "$backup/Caddyfile" "$caddyfile"
    systemctl daemon-reload
    caddy validate --config "$caddyfile" && systemctl reload caddy
    systemctl is-active --quiet voice-rvc.service
    exit "$status"
}
trap rollback ERR

install -d -m 0755 "$root/releases" "$root/backups" "$backup"
install -m 0644 "$caddyfile" "$backup/Caddyfile"
if [[ -e "$unit" ]]; then
    had_unit=1
    install -m 0644 "$unit" "$backup/voice-rtrvc-canary.service"
fi
if [[ -L "$current" ]]; then
    had_current=1
    readlink "$current" > "$backup/current-target"
fi
systemctl is-enabled voice-rtrvc-canary.service > "$backup/was-enabled" 2>&1 || true
systemctl is-active voice-rtrvc-canary.service > "$backup/was-active" 2>&1 || true
systemctl show voice-rvc.service -p NRestarts --no-pager > "$backup/voice-rvc-before"

if [[ ! -x "$venv/bin/python" ]]; then
    install -d -m 0755 "$root"
    cp -al /opt/voice-rvc/venv "$venv"
fi
install -d -o voice-rvc -g voice-rvc -m 0755 "$fcpe_pkgs"
install -d -o voice-rvc -g voice-rvc -m 0755 /var/cache/voice-rtrvc/numba
runuser -u voice-rvc -- "$venv/bin/python" -m pip install --no-deps \
    --target "$fcpe_pkgs" -r "$stage/requirements-rtrvc.lock"
runuser -u voice-rvc -- env PYTHONPATH="$fcpe_pkgs" \
    NUMBA_CACHE_DIR=/var/cache/voice-rtrvc/numba XDG_CACHE_HOME=/var/cache/voice-rtrvc \
    "$venv/bin/python" -c \
    'import torchfcpe, einops, local_attention, hyper_connections'

install -d -m 0755 "$release"
cp -a "$stage/rvc_service" "$stage/web-rt" "$stage/live-rtrvc.py" "$release/"
chown -R voice-rvc:voice-rvc "$release"
ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$current"
install -m 0644 "$stage/voice-rtrvc-canary.service" "$unit"
install -m 0644 "$stage/Caddyfile" "$caddyfile"
systemctl daemon-reload
caddy validate --config "$caddyfile"
systemctl reload caddy
systemctl enable voice-rtrvc-canary.service
systemctl restart voice-rtrvc-canary.service

for attempt in $(seq 1 95); do
    if curl -fsS http://127.0.0.1:8093/healthz | "$venv/bin/python" -c \
        'import json, sys; body=json.load(sys.stdin); assert body["status"] == "ready"; assert set(body["variants"]) == {"v2-fcpe"}'; then
        break
    fi
    if [[ "$attempt" == 95 ]]; then
        false
    fi
    sleep 1
done

runuser -u voice-rvc -- env PYTHONPATH="$fcpe_pkgs" "$venv/bin/python" \
    "$current/live-rtrvc.py" --url ws://127.0.0.1:8093/ws/rvc \
    --origin https://voice-claude.lan.awesomeio.ru \
    --health-url http://127.0.0.1:8093/healthz --blocks 3
runuser -u voice-rvc -- env PYTHONPATH="$fcpe_pkgs" "$venv/bin/python" \
    "$current/live-rtrvc.py" --url wss://voice-claude.lan.awesomeio.ru/ws/rvc \
    --origin https://voice-claude.lan.awesomeio.ru \
    --health-url https://voice-claude.lan.awesomeio.ru/healthz --blocks 3

systemctl is-active --quiet voice-rvc.service
[[ "$(systemctl show voice-rvc.service -p NRestarts --value)" == "$prior_restarts" ]]
trap - ERR
printf '%s\n' "FCPE canary release $stamp is healthy; GPT v2 was not restarted."
REMOTE
