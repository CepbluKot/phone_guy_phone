#!/usr/bin/env bash
set -euo pipefail

approved_target=ubuntu@192.168.20.70
script_path=$(readlink -f "$0")

validate_stamp() {
  [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid Go release stamp" >&2; return 2; }
}

remote_deploy() {
  stamp=$1
  prefix=${DEPLOY_GOWEB_ROOT_PREFIX:-}
  if [ -n "$prefix" ] && { [[ "$prefix" != /* ]] || [[ "$prefix" == / ]] || [[ "$prefix" == *..* ]]; }; then
    echo "Unsafe Go deployment root prefix" >&2
    return 2
  fi
  p() { printf '%s%s' "$prefix" "$1"; }
  voice_root=$(p /opt/voice-changer)
  go_root=$(p /opt/voice-go)
  release="$go_root/releases/$stamp"
  backup="$go_root/backups/$stamp"
  caddy_live=$(p /etc/caddy/Caddyfile)
  caddy_source="$voice_root/deploy/Caddyfile"
  phonebook_file=$(p /etc/voice-changer/phonebook.json)
  webphone_file=$(p /etc/voice-changer/webphone-directory.json)
  public_auth_file=$(p /etc/voice-phone-auth/public-auth.json)
  # Go owns the release tree for both the web and conference containers.
  conf_root=$go_root

  if [ -e "$voice_root/.go-runtime-owner" ] || [ -e "$backup" ] \
      || [ -n "$(find "$(p /opt/voice-go/staging)" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]; then
    echo "Go preflight: an active owner, backup, or unrolled staging release exists" >&2
    return 1
  fi
  for name in voice-conference-controller-1 voice-conference-asterisk-1; do
    [ "$(docker inspect "$name" --format '{{.State.Health.Status}}' 2>/dev/null || true)" = healthy ] || {
      echo "Go preflight: legacy container is not healthy: $name" >&2; return 1;
    }
  done
  [ "$(systemctl is-active voice-selfmonitor.service)" = active ] \
      && [ "$(systemctl is-enabled voice-selfmonitor.service)" = enabled ] \
      || { echo "Go preflight: legacy self-monitor state changed" >&2; return 1; }
  [ "$(docker inspect voice-changer-voice-1 --format '{{.State.Status}}' 2>/dev/null || true)" = exited ] || {
    echo "Go preflight: legacy HTTP container is not stopped" >&2; return 1;
  }
  rvc=$(curl --max-time 5 -fsS http://127.0.0.1:8090/healthz) || { echo "Go preflight: RVC health unavailable" >&2; return 1; }
  printf '%s' "$rvc" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="ready" and not d.get("active",d.get("running",False)) and d.get("queuedWindows",d.get("queue",0))==0' || {
    echo "Go preflight: RVC is not ready and idle" >&2; return 1;
  }
  [ -f "$caddy_live" ] && [ -f "$caddy_source" ] || { echo "Go preflight: Caddy files missing" >&2; return 1; }
  ip -o -4 addr show | grep -q '192\.168\.20\.70/' || { echo "Go preflight: private web address is not assigned" >&2; return 1; }
  [ -f "$(p /etc/voice-changer/voice-routing.json)" ] && [ -f "$(p /etc/voice-changer-admin/password)" ] || {
    echo "Go preflight: admin bootstrap files missing" >&2; return 1;
  }
  [ -f "$public_auth_file" ] && [ ! -L "$public_auth_file" ] \
      && [ "$(stat -c '%u:%g:%a' "$public_auth_file")" = 10001:10001:400 ] \
      || { echo "Go preflight: public phone auth file missing or has unsafe permissions" >&2; return 1; }
  setpriv --reuid 10001 --regid 10001 --clear-groups test -r "$public_auth_file" || {
    echo "Go preflight: Go UID cannot read public phone auth file" >&2; return 1;
  }
  if [ -e "$phonebook_file" ] || [ -L "$phonebook_file" ]; then
    [ -f "$phonebook_file" ] && [ ! -L "$phonebook_file" ] || { echo "Go preflight: phonebook path is not a regular file" >&2; return 1; }
    [ "$(stat -c '%u:%g:%a' "$phonebook_file")" = 10001:10001:600 ] || { echo "Go preflight: phonebook owner or mode is invalid" >&2; return 1; }
    python3 - "$phonebook_file" <<'PY' || { echo "Go preflight: phonebook state is invalid" >&2; return 1; }
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("schemaVersion") == 1 and isinstance(d.get("revision"), int) and d["revision"] > 0
assert isinstance(d.get("devices"), list)
for item in d["devices"]:
    assert item.get("extension", "") in {"", "1983", "1987", "1988", "2014"}
PY
  fi
  if [ -e "$webphone_file" ] || [ -L "$webphone_file" ]; then
    [ -f "$webphone_file" ] && [ ! -L "$webphone_file" ] || { echo "Go preflight: browser phone directory is not a regular file" >&2; return 1; }
    [ "$(stat -c '%u:%g:%a' "$webphone_file")" = 10001:10001:600 ] || { echo "Go preflight: browser directory owner or mode is invalid" >&2; return 1; }
    python3 - "$webphone_file" <<'PY' || { echo "Go preflight: browser phone directory is invalid" >&2; return 1; }
import json, sys
d=json.load(open(sys.argv[1])); assert d.get('schemaVersion')==1 and isinstance(d.get('revision'),int) and d['revision']>0
assert isinstance(d.get('people'),list) and len(d['people'])<=4
seen=set()
for person in d['people']:
    assert person.get('extension') in {'1983','1987','1988','2014'}
    assert isinstance(person.get('nickname'),str) and 0<len(person['nickname'].strip())<=48
    assert person['extension'] not in seen; seen.add(person['extension'])
PY
  fi
  [ "$(stat -c '%u:%g:%a' "$(p /etc/voice-changer-admin/password)")" = 10001:10001:400 ] || {
    echo "Go preflight: admin password file owner or mode is invalid" >&2; return 1;
  }
  setpriv --reuid 10001 --regid 10001 --clear-groups test -r "$(p /etc/voice-changer-admin/password)" || {
    echo "Go preflight: Go UID cannot read admin password" >&2; return 1;
  }
  python3 - "$(p /etc/voice-changer/voice-routing.json)" <<'PY' || { echo "Go preflight: initial phone roles changed" >&2; return 1; }
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("schemaVersion") == 1 and d.get("revision") == 1
assert set(d.get("extensions", {})) == {"1983", "1987", "1988", "2014"}
assert set(d["extensions"].values()) == {"original"}
PY
  channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
  printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Go preflight: active calls detected" >&2; return 1; }
  endpoints=$(docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show endpoints' \
    | awk '/^[[:space:]]*Endpoint:[[:space:]]+[0-9]+/ {print $2}' | sort -n | paste -sd, -)
  [ "$endpoints" = 1983,1987,1988,2014 ] || { echo "Go preflight: endpoint inventory changed" >&2; return 1; }
  docker exec voice-conference-asterisk-1 asterisk -rx 'module show like res_ari_events.so' \
    | grep -Eq '^res_ari_events\.so[[:space:]].*[[:space:]][[:digit:]]+[[:space:]]+Running[[:space:]]' \
    || { echo "Go preflight: ARI events module is unavailable" >&2; return 1; }
  [ "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)" -ge $((2200 * 1024)) ] || { echo "Go preflight: memory headroom too low" >&2; return 1; }
  [ "$(df -Pm "$(p /opt)" | awk 'NR==2 {print $4}')" -ge 4096 ] || { echo "Go preflight: /opt disk headroom too low" >&2; return 1; }

  active_compose=$(docker inspect voice-conference-asterisk-1 --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}')
  case "$active_compose" in "$conf_root"/releases/*/deploy/compose.conference.yaml) ;; *) echo "Go preflight: unexpected conference project owner" >&2; return 1 ;; esac
  conf_release=$(basename "$(dirname "$(dirname "$active_compose")")")
  [[ "$conf_release" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Go preflight: active conference release path is invalid" >&2; return 1; }
  old_compose="$conf_root/releases/$conf_release/deploy/compose.conference.yaml"
  old_env="$conf_root/releases/$conf_release/.env"
  [ -f "$old_compose" ] && [ -f "$old_env" ] || { echo "Go preflight: active conference release is incomplete" >&2; return 1; }
  runtime=$(sed -n 's/^CONFERENCE_RUNTIME=//p' "$old_env")
  old_runtime=$runtime
  new_runtime="$release/runtime"
  fixtures=$(sed -n 's/^CONFERENCE_FIXTURES=//p' "$old_env")
  [ -f "$old_runtime/asterisk/ari-password" ] && [ -d "$old_runtime/asterisk/sounds" ] \
    && [ -d "$fixtures" ] || { echo "Go preflight: ARI, sound, or fixture runtime is missing" >&2; return 1; }
  for item in ari.conf ari-password http.conf pjsip.conf modules.conf; do
    [ -f "$old_runtime/asterisk/$item" ] || { echo "Go preflight: Asterisk runtime file is missing: $item" >&2; return 1; }
  done
  for item in Dockerfile.goweb go.mod go.sum cmd internal admin-ui web conference/asterisk deploy/compose.goweb.yaml deploy/compose.conference.yaml deploy/Caddyfile.goweb deploy/phonebook.initial.json deploy/phonebook.example.json deploy/webphone-directory.initial.json deploy/webphone-directory.example.json deploy/rollback-goweb-production.sh deploy/update_pjsip_wss_transport.py deploy/configure-public-phone-auth.py; do
    [ -e "$release/$item" ] || { echo "Go release file is missing: $item" >&2; return 2; }
  done

  install -d -o root -g root -m 0700 "$new_runtime/asterisk"
  for item in ari.conf ari-password http.conf pjsip.conf modules.conf; do
    cp -a "$old_runtime/asterisk/$item" "$new_runtime/asterisk/"
  done
  python3 "$release/deploy/update_pjsip_wss_transport.py" \
    "$new_runtime/asterisk/pjsip.conf" "$release/conference/asterisk/pjsip.conf.template"
  cp -a "$old_runtime/asterisk/sounds" "$new_runtime/asterisk/"
  cp "$release/conference/asterisk/extensions.conf" "$new_runtime/asterisk/extensions.conf"
  cp "$release/conference/asterisk/sorcery.conf" "$new_runtime/asterisk/sorcery.conf"
  chown root:root "$new_runtime/asterisk/extensions.conf"
  chmod 0644 "$new_runtime/asterisk/extensions.conf"
  chown root:root "$new_runtime/asterisk/sorcery.conf"
  chmod 0644 "$new_runtime/asterisk/sorcery.conf"
  install -d -o root -g root -m 0750 "$backup"
  chmod 0700 "$backup"
  printf 'VOICE_GO_IMAGE=voice-go:production-%s\nVOICE_ARI_RUNTIME=%s\nVOICE_CONFERENCE_FIXTURES=%s\nVOICE_PHONE_TURN_SECRET_HOST_FILE=/etc/voice-phone/turn-shared-secret\nVOICE_PHONE_PUBLIC_AUTH_HOST_FILE=/etc/voice-phone-auth/public-auth.json\nCONFERENCE_TAG=%s\nCONFERENCE_RUNTIME=%s\nCONFERENCE_FIXTURES=%s\n' \
    "$stamp" "$new_runtime" "$fixtures" "$stamp" "$new_runtime" "$fixtures" > "$release/.env"
  chmod 0600 "$release/.env"
  cp -a "$caddy_live" "$backup/Caddyfile"
  cp -a "$caddy_source" "$backup/source-Caddyfile"
  cp -a "$(p /etc/systemd/system/voice-selfmonitor.service)" "$backup/selfmonitor.service"
  cp -a "$(p /etc/voice-selfmonitor.env)" "$backup/selfmonitor.env"
  cp -a "$(p /etc/voice-changer/voice-routing.json)" "$backup/voice-routing.json"
  phonebook_was_present=false
  if [ -f "$phonebook_file" ]; then
    cp -a "$phonebook_file" "$backup/phonebook.json"
    sha256sum "$phonebook_file" | awk '{print $1}' > "$backup/phonebook-sha256"
    phonebook_was_present=true
  else
    : > "$backup/phonebook-absent"
  fi
  cp -a "$(p /etc/voice-changer-admin/password)" "$backup/admin-password"
  docker inspect voice-conference-asterisk-1 --format '{{.Image}}' > "$backup/asterisk-image-id"
  docker inspect voice-conference-controller-1 --format '{{.Image}}' > "$backup/controller-image-id"
  sha256sum "$(p /etc/voice-changer/voice-routing.json)" | awk '{print $1}' > "$backup/route-config-sha256"
  sha256sum "$(p /etc/voice-changer-admin/password)" | awk '{print $1}' > "$backup/admin-password-sha256"
  printf 'stamp=%s\nconference_release=%s\nselfmonitor_was_active=active\nselfmonitor_was_enabled=enabled\nhttp_container_was_running=stopped\nphonebook_was_present=%s\n' "$stamp" "$conf_release" "$phonebook_was_present" > "$backup/manifest"
  chmod 0600 "$backup/manifest" "$backup"/*-image-id "$backup"/*-sha256

  rollback_required=1
  rollback_on_error() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$rollback_required" -eq 1 ]; then
      echo "Go rollout failed; restoring production snapshot $stamp" >&2
      if ! bash "$release/deploy/rollback-goweb-production.sh" "$stamp"; then
        echo "ROLLBACK_FAILED stamp=$stamp" >&2
        exit 70
      fi
    fi
    exit "$status"
  }
  trap rollback_on_error EXIT

  if [ "$phonebook_was_present" = false ]; then
    install -o 10001 -g 10001 -m 0600 "$release/deploy/phonebook.initial.json" "$phonebook_file"
  fi
  if [ ! -e "$webphone_file" ]; then
    install -o 10001 -g 10001 -m 0600 "$release/deploy/webphone-directory.initial.json" "$webphone_file"
  fi

  docker build -f "$release/Dockerfile.goweb" -t "voice-go:production-$stamp" "$release"
  docker build -f "$release/conference/asterisk/Dockerfile" -t "voice-conference-asterisk:$stamp" "$release/conference/asterisk"
  docker compose -p voice-go -f "$release/deploy/compose.goweb.yaml" --env-file "$release/.env" config --quiet
  docker compose -p voice-conference -f "$release/deploy/compose.conference.yaml" --env-file "$release/.env" config --quiet
  caddy validate --config "$release/deploy/Caddyfile.goweb"

  docker compose -p voice-conference -f "$old_compose" --env-file "$old_env" stop controller
  systemctl disable --now voice-selfmonitor.service
  docker compose -p voice-conference -f "$release/deploy/compose.conference.yaml" --env-file "$release/.env" \
    up -d --no-build --no-deps --force-recreate asterisk
  asterisk_ready=0
  for attempt in $(seq 1 30); do
    if [ "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)" = healthy ]; then
      asterisk_ready=1
      break
    fi
    sleep 2
  done
  [ "$asterisk_ready" -eq 1 ] || { echo "New Asterisk image failed health check" >&2; exit 1; }
  asterisk_ip=$(docker inspect voice-conference-asterisk-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
  [ "$asterisk_ip" = 172.19.0.2 ] || { echo "Asterisk Docker IP changed; ICE host-candidate mapping needs review" >&2; exit 1; }
  docker exec voice-conference-asterisk-1 grep -Fqx '172.19.0.2 => 192.168.20.70' /etc/asterisk/rtp.conf
  docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-wss' | grep -q 'external_media_address      : 192.168.20.70'
  docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-wss' | grep -q 'local_net                   : 172.19.0.0/255.255.0.0'

  docker compose -p voice-go -f "$release/deploy/compose.goweb.yaml" --env-file "$release/.env" up -d --no-build
  go_ready=0
  for attempt in $(seq 1 30); do
    if curl --max-time 3 -fsS http://192.168.20.70:8080/healthz >/dev/null \
        && curl --max-time 3 -fsS http://127.0.0.1:8096/healthz | grep -q '"status":"ready"'; then
      go_ready=1
      break
    fi
    sleep 2
  done
  [ "$go_ready" -eq 1 ] || { echo "Go web or self-monitor health failed" >&2; exit 1; }
  apps=$(docker exec voice-conference-asterisk-1 asterisk -rx 'ari show apps')
  printf '%s\n' "$apps" | grep -q voice-control && printf '%s\n' "$apps" | grep -q selfmonitor \
      || { echo "Go ARI applications did not register" >&2; exit 1; }
  docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show 600@phoneguy-sip' | grep -q 'Stasis(voice-control' \
      || { echo "Go SIP route is not loaded" >&2; exit 1; }
  docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show 1900@phoneguy-sip' | grep -q 'Stasis(voice-control' \
      || { echo "Go callback route is not loaded" >&2; exit 1; }
  docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show 1999@phoneguy-sip' | grep -q 'Stasis(selfmonitor)' \
      || { echo "Go self-monitor route is not loaded" >&2; exit 1; }

  python3 - <<'PY'
import json, urllib.request
for origin in ('https://voice-admin.lan.awesomeio.ru', 'https://voice-phone.lan.awesomeio.ru', 'https://voice.lan.awesomeio.ru', 'https://vm-voice-1.lan.awesomeio.ru'):
    headers = {'Origin': origin}
    def get(path):
        with urllib.request.urlopen(urllib.request.Request('http://192.168.20.70:8080' + path, headers=headers), timeout=5) as response:
            return json.load(response)
    assert get('/admin/api/v1/auth-mode')['required'] is False
    routes = get('/admin/api/v1/voice-routes')
    assert set(routes['extensions']) == {'1983', '1987', '1988', '2014'}
    phones = get('/admin/api/v1/phones')
    assert phones['revision'] > 0 and len(phones['devices']) >= 2
    macs = {device['mac'] for device in phones['devices']}
    assert {'00:15:65:89:b3:85', '00:0b:82:f4:f2:8b'} <= macs
print('passwordless admin and phonebook smoke passed for both private UI origins')
PY
  cp "$release/deploy/Caddyfile.goweb" "$caddy_live"
  chown --reference="$backup/Caddyfile" "$caddy_live"
  chmod --reference="$backup/Caddyfile" "$caddy_live"
  cp "$release/deploy/Caddyfile.goweb" "$caddy_source"
  chown --reference="$backup/source-Caddyfile" "$caddy_source"
  chmod --reference="$backup/source-Caddyfile" "$caddy_source"
  caddy validate --config "$caddy_live"
  systemctl reload caddy
  printf '%s\n' "$stamp" > "$voice_root/.go-runtime-owner"
  chown root:root "$voice_root/.go-runtime-owner"
  chmod 0600 "$voice_root/.go-runtime-owner"
  rollback_required=0
  trap - EXIT
  printf 'GO_DEPLOY_COMPLETE stamp=%s image=voice-go:production-%s backup=%s\n' "$stamp" "$stamp" "$backup"
}

case "${1:-}" in
  --remote-deploy)
    [ "$#" -eq 2 ] || exit 2
    validate_stamp "$2"
    remote_deploy "$2"
    exit
    ;;
  rollback)
    [ "$#" -eq 2 ] || { echo "Usage: $0 rollback YYYYMMDDTHHMMSSZ" >&2; exit 2; }
    validate_stamp "$2"
    target=${DEPLOY_GOWEB_TARGET:-$approved_target}
    [ "$target" = "$approved_target" ] || { echo "DEPLOY_GOWEB_TARGET must be $approved_target" >&2; exit 2; }
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" sudo bash -s -- "$2" < "$(dirname "$script_path")/rollback-goweb-production.sh"
    exit
    ;;
esac

repo=$(cd "$(dirname "$script_path")/.." && pwd)
cd "$repo"
target=${DEPLOY_GOWEB_TARGET:-$approved_target}
[ "$target" = "$approved_target" ] || { echo "DEPLOY_GOWEB_TARGET must be $approved_target" >&2; exit 2; }
[ "$#" -eq 0 ] || { echo "Usage: $0 [rollback YYYYMMDDTHHMMSSZ]" >&2; exit 2; }
if [ "${DEPLOY_GOWEB_SKIP_CHECKS:-0}" != 1 ]; then
  go test ./...
  go vet ./...
  node --test tests/*.test.cjs
  (cd admin-ui && npm test -- --run && npm run build)
  pytest_bin=${DEPLOY_GOWEB_PYTEST:-}
  if [ -z "$pytest_bin" ] && [ -x .venv/bin/pytest ]; then pytest_bin=.venv/bin/pytest; fi
  if [ -z "$pytest_bin" ]; then pytest_bin=$(command -v pytest || true); fi
  [ -n "$pytest_bin" ] && [ -x "$pytest_bin" ] || { echo "Set DEPLOY_GOWEB_PYTEST to the project test runner" >&2; exit 2; }
  "$pytest_bin" -q tests/test_bootstrap_goweb.py tests/test_goweb_stage_rollback.py tests/test_rollback_goweb_production.py tests/test_legacy_deploy_owner_guard.py tests/test_public_phone_access.py
  git diff --check
fi

stamp=${DEPLOY_GOWEB_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
validate_stamp "$stamp"
remote_stage="/tmp/voice-go-release-$stamp"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "mkdir -m 0700 '$remote_stage' && mkdir -m 0755 '$remote_stage/conference' && mkdir -m 0755 '$remote_stage/deploy'"
if ! rsync -a --delete Dockerfile.goweb go.mod go.sum cmd internal admin-ui web \
    "$target:$remote_stage/"; then
  ssh "$target" "rm -rf '$remote_stage'" || true
  exit 1
fi
if ! rsync -a --delete conference/asterisk "$target:$remote_stage/conference/"; then
  ssh "$target" "rm -rf '$remote_stage'" || true
  exit 1
fi
if ! rsync -a --delete deploy/compose.goweb.yaml deploy/compose.conference.yaml deploy/Caddyfile.goweb deploy/rollback-goweb-production.sh deploy/update_pjsip_wss_transport.py deploy/configure-public-phone-auth.py deploy/phonebook.initial.json deploy/phonebook.example.json deploy/webphone-directory.initial.json deploy/webphone-directory.example.json \
    "$target:$remote_stage/deploy/"; then
  ssh "$target" "rm -rf '$remote_stage'" || true
  exit 1
fi
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" sudo bash -s -- "$stamp" "$remote_stage" <<'REMOTE_STAGE'
set -euo pipefail
stamp=$1
stage=$2
root=/opt/voice-go
release="$root/releases/$stamp"
test ! -e "$release" || { echo "Go release already exists" >&2; exit 1; }
install -d -o root -g root -m 0750 "$root/releases"
mv "$stage" "$release"
chown -R root:root "$release"
chmod -R u=rwX,go=rX "$release"
REMOTE_STAGE
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" sudo bash -s -- --remote-deploy "$stamp" < "$script_path"; then
  echo "Go production deployment did not complete; inspect the snapshot and owner marker on $target" >&2
  exit 1
fi
