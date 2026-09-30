#!/usr/bin/env bash
set -euo pipefail

target=ubuntu@192.168.20.70
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
stamp=${DEPLOY_GOWEB_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid update stamp" >&2; exit 2; }

if [[ "${DEPLOY_GOWEB_SKIP_CHECKS:-0}" != 1 ]]; then
  go test ./...
  go vet ./...
  node --test tests/*.test.cjs
  (cd admin-ui && npm test -- --run && npm run build)
  pytest_bin=${DEPLOY_GOWEB_PYTEST:-$(command -v pytest || true)}
  [[ -x "$pytest_bin" ]] || { echo "Set DEPLOY_GOWEB_PYTEST to the project test runner" >&2; exit 2; }
  "$pytest_bin" -q tests/test_bootstrap_goweb.py tests/test_goweb_stage_rollback.py tests/test_rollback_goweb_production.py tests/test_legacy_deploy_owner_guard.py tests/test_browser_webrtc_config.py tests/test_public_phone_access.py
  git diff --check
fi

stage="/tmp/voice-go-update-$stamp"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "mkdir -m 0700 '$stage' && mkdir -m 0755 '$stage/conference' && mkdir -m 0755 '$stage/deploy'"
rsync -a --delete Dockerfile.goweb go.mod go.sum cmd internal admin-ui web "$target:$stage/"
rsync -a --delete conference/asterisk "$target:$stage/conference/"
rsync -a --delete deploy/compose.goweb.yaml deploy/compose.conference.yaml deploy/Caddyfile.goweb deploy/rollback-goweb-production.sh deploy/update_pjsip_wss_transport.py deploy/phonebook.initial.json deploy/phonebook.example.json deploy/webphone-directory.initial.json deploy/webphone-directory.example.json "$target:$stage/deploy/"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" sudo bash -s -- "$stamp" "$stage" <<'REMOTE'
set -euo pipefail
stamp=$1; stage=$2
voice=/opt/voice-changer; root=/opt/voice-go; release="$root/releases/$stamp"; backup="$root/updates/$stamp"
owner=$(cat "$voice/.go-runtime-owner")
[[ "$owner" =~ ^[0-9]{8}T[0-9]{6}Z$ && ! -e "$release" && ! -e "$backup" ]] || { echo "Update owner or release state invalid" >&2; exit 1; }
go_cfg=$(docker inspect voice-go --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
ast_cfg=$(docker inspect voice-conference-asterisk-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
go_old=$(dirname "$(dirname "$go_cfg")")
ast_old=$(dirname "$(dirname "$ast_cfg")")
go_env="$go_old/.env"; ast_env="$ast_old/.env"
[[ -f "$go_env" && -f "$ast_env" && "$(docker inspect voice-go --format '{{.State.Health.Status}}')" = healthy && "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}')" = healthy ]] || { echo "Active Go/Asterisk owner is not healthy" >&2; exit 1; }
[[ "$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')" =~ 0\ active\ channels ]] || { echo "Active calls detected" >&2; exit 1; }
python3 - <<'PY'
import json, urllib.request
import time
for attempt in range(8):
    try:
        d=json.load(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=5))
        if d.get('status')=='ready' and not d.get('active',d.get('running',False)) and d.get('queuedWindows',d.get('queue',0))==0:
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise SystemExit('RVC health unavailable or busy after update')
PY
mkdir -p "$root/updates"; install -d -m 0700 "$backup"; mv "$stage" "$release"; chown -R root:root "$release"; chmod -R u=rwX,go=rX "$release"
old_runtime=$(sed -n 's/^CONFERENCE_RUNTIME=//p' "$ast_env")
fixtures=$(sed -n 's/^CONFERENCE_FIXTURES=//p' "$ast_env")
old_go_image=$(sed -n 's/^VOICE_GO_IMAGE=//p' "$go_env")
old_ast_tag=$(sed -n 's/^CONFERENCE_TAG=//p' "$ast_env")
new_runtime="$release/runtime"
webphone=/etc/voice-changer/webphone-directory.json
if [[ ! -e "$webphone" ]]; then install -o 10001 -g 10001 -m 0600 "$release/deploy/webphone-directory.initial.json" "$webphone"; fi
[[ -f "$webphone" && ! -L "$webphone" && "$(stat -c '%u:%g:%a' "$webphone")" = 10001:10001:600 ]] || { echo "Browser directory file ownership or mode invalid" >&2; exit 1; }
install -d -m 0700 "$new_runtime/asterisk"
cp -a "$old_runtime/asterisk/." "$new_runtime/asterisk/"
cp "$release/conference/asterisk/extensions.conf" "$new_runtime/asterisk/extensions.conf"
cp "$release/conference/asterisk/sorcery.conf" "$new_runtime/asterisk/sorcery.conf"
cp "$release/conference/asterisk/modules.conf" "$new_runtime/asterisk/modules.conf"
python3 "$release/deploy/update_pjsip_wss_transport.py" \
  "$new_runtime/asterisk/pjsip.conf" "$release/conference/asterisk/pjsip.conf.template"
chown root:root "$new_runtime/asterisk/extensions.conf" "$new_runtime/asterisk/sorcery.conf"; chmod 0644 "$new_runtime/asterisk/extensions.conf" "$new_runtime/asterisk/sorcery.conf"
chown --reference="$old_runtime/asterisk/modules.conf" "$new_runtime/asterisk/modules.conf"; chmod --reference="$old_runtime/asterisk/modules.conf" "$new_runtime/asterisk/modules.conf"
chown --reference="$old_runtime/asterisk/pjsip.conf" "$new_runtime/asterisk/pjsip.conf"; chmod --reference="$old_runtime/asterisk/pjsip.conf" "$new_runtime/asterisk/pjsip.conf"
printf 'VOICE_GO_IMAGE=voice-go:update-%s\nVOICE_ARI_RUNTIME=%s\nVOICE_CONFERENCE_FIXTURES=%s\nCONFERENCE_TAG=update-%s\nCONFERENCE_RUNTIME=%s\nCONFERENCE_FIXTURES=%s\n' "$stamp" "$new_runtime" "$fixtures" "$stamp" "$new_runtime" "$fixtures" > "$release/.env"
chmod 0600 "$release/.env"
cp -a /etc/caddy/Caddyfile "$backup/Caddyfile"; cp -a "$voice/deploy/Caddyfile" "$backup/source-Caddyfile"
cp -a "$go_env" "$backup/go.env"; cp -a "$ast_env" "$backup/asterisk.env"
printf 'go_release=%s\nasterisk_release=%s\nowner=%s\nold_go_image=%s\nold_asterisk_tag=%s\nold_runtime=%s\n' "$go_old" "$ast_old" "$owner" "$old_go_image" "$old_ast_tag" "$old_runtime" > "$backup/manifest"
cp "$release/deploy/Caddyfile.goweb" "$backup/new-Caddyfile"
rollback() {
  rc=$?; trap - EXIT
  if (( rc != 0 )); then
    echo "Update failed; restoring previous Go and Asterisk containers" >&2
    docker compose -p voice-go -f "$go_cfg" --env-file "$go_env" up -d --no-build --force-recreate || true
    docker compose -p voice-conference -f "$ast_cfg" --env-file "$ast_env" up -d --no-build --no-deps --force-recreate asterisk || true
    cp -a "$backup/Caddyfile" /etc/caddy/Caddyfile; cp -a "$backup/source-Caddyfile" "$voice/deploy/Caddyfile"; caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy || true
  fi
  exit "$rc"
}
trap rollback EXIT

docker build -f "$release/Dockerfile.goweb" -t "voice-go:update-$stamp" "$release"
docker build -f "$release/conference/asterisk/Dockerfile" -t "voice-conference-asterisk:update-$stamp" "$release/conference/asterisk"
docker compose -p voice-go -f "$release/deploy/compose.goweb.yaml" --env-file "$release/.env" config --quiet
docker compose -p voice-conference -f "$release/deploy/compose.conference.yaml" --env-file "$release/.env" config --quiet
caddy validate --config "$release/deploy/Caddyfile.goweb"
docker compose -p voice-conference -f "$release/deploy/compose.conference.yaml" --env-file "$release/.env" up -d --no-build --no-deps --force-recreate asterisk
ready=0; for _ in $(seq 1 30); do if [[ "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)" = healthy ]]; then ready=1; break; fi; sleep 2; done
[[ $ready = 1 ]]
docker compose -p voice-go -f "$release/deploy/compose.goweb.yaml" --env-file "$release/.env" up -d --no-build --force-recreate voice-go
ready=0; for _ in $(seq 1 30); do if curl --max-time 3 -fsS http://192.168.20.70:8080/healthz >/dev/null; then ready=1; break; fi; sleep 2; done
[[ $ready = 1 ]]
docker exec voice-conference-asterisk-1 asterisk -rx 'module show like res_pjsip_transport_websocket.so' | grep -Eq 'Running'
docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transports' | grep -q transport-wss
asterisk_ip=$(docker inspect voice-conference-asterisk-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
[[ "$asterisk_ip" == 172.19.0.2 ]] || { echo "Asterisk Docker IP changed; ICE host-candidate mapping needs review" >&2; exit 1; }
docker exec voice-conference-asterisk-1 grep -Fqx '172.19.0.2 => 192.168.20.70' /etc/asterisk/rtp.conf
docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-wss' | grep -q 'external_media_address      : 192.168.20.70'
docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-wss' | grep -q 'local_net                   : 172.19.0.0/255.255.0.0'
python3 - <<'PY'
import json, urllib.request
for origin in ('https://voice-phone.lan.awesomeio.ru','https://voice.lan.awesomeio.ru','https://vm-voice-1.lan.awesomeio.ru'):
    req=urllib.request.Request('http://192.168.20.70:8080/phone/api/v1/config',headers={'Origin':origin})
    d=json.load(urllib.request.urlopen(req,timeout=5)); assert d['signalingUrl']=='wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling'
for origin in ('https://voice-admin.lan.awesomeio.ru','https://voice.lan.awesomeio.ru','https://vm-voice-1.lan.awesomeio.ru'):
    req=urllib.request.Request('http://192.168.20.70:8080/admin/api/v1/auth-mode',headers={'Origin':origin})
    d=json.load(urllib.request.urlopen(req,timeout=5)); assert d['required'] is False
PY
cp "$release/deploy/Caddyfile.goweb" /etc/caddy/Caddyfile; cp "$release/deploy/Caddyfile.goweb" "$voice/deploy/Caddyfile"; caddy validate --config /etc/caddy/Caddyfile; systemctl reload caddy
curl --max-time 5 -fsS https://vm-voice-1.lan.awesomeio.ru/phone/ | grep -q 'phone-'
printf 'GOWEB_UPDATE_COMPLETE stamp=%s previous=%s backup=%s oldAsteriskTag=%s\n' "$stamp" "$owner" "$backup" "$old_ast_tag"
trap - EXIT
REMOTE
echo "Go production update completed: $stamp"
