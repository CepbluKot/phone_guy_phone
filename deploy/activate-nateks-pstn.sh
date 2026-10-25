#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -t 0 ]] || { echo "Run this command from an interactive terminal; SIP passwords are entered without echo" >&2; exit 2; }

container=voice-conference-asterisk-1
[[ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" == healthy ]] || {
  echo "Asterisk is not healthy" >&2; exit 1;
}
channels=$(docker exec "$container" asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || {
  echo "Active calls prevent PSTN activation" >&2; exit 1;
}

active_compose=$(docker inspect "$container" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
case "$active_compose" in
  /opt/voice-go/releases/*/deploy/compose.conference.yaml) ;;
  *) echo "Unexpected active Asterisk release owner" >&2; exit 1 ;;
esac
release=${active_compose%/deploy/compose.conference.yaml}
[[ "${release##*/}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid active release directory" >&2; exit 1; }
[[ -f "$release/.env" ]] || { echo "Active release environment file is missing" >&2; exit 1; }

pjsip=$(docker inspect "$container" --format '{{range .Mounts}}{{if eq .Destination "/etc/asterisk/pjsip.conf"}}{{.Source}}{{end}}{{end}}')
[[ "$pjsip" == "$release/runtime/asterisk/pjsip.conf" ]] || {
  echo "Asterisk PJSIP runtime is outside the active release" >&2; exit 1;
}
runtime_dir=${pjsip%/pjsip.conf}
runtime_include="$runtime_dir/nateks-pstn.conf"
extensions="$runtime_dir/extensions.conf"
[[ -f "$runtime_include" && ! -L "$runtime_include" && -f "$extensions" && ! -L "$extensions" ]] || {
  echo "Asterisk PSTN runtime files are missing or unsafe" >&2; exit 1;
}
[[ -x "$release/deploy/configure_nateks_pstn_runtime.py" || -f "$release/deploy/configure_nateks_pstn_runtime.py" ]] || {
  echo "Nateks runtime configurator is missing from the active release" >&2; exit 1;
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/opt/voice-go/backups/nateks-pstn-$stamp"
install -d -o root -g root -m 0700 "$backup"
cp -a "$runtime_include" "$backup/nateks-pstn.conf"
cp -a "$extensions" "$backup/extensions.conf"
chmod 0600 "$backup/nateks-pstn.conf" "$backup/extensions.conf"
printf 'active_release=%s\nruntime_dir=%s\n' "$release" "$runtime_dir" > "$backup/manifest"
chmod 0600 "$backup/manifest"

changed=0
container_recreated=0
recreate_active_asterisk() {
  docker compose -p voice-conference -f "$active_compose" --env-file "$release/.env" \
    up -d --no-build --no-deps --force-recreate asterisk || return 1
  ready=0
  for attempt in $(seq 1 40); do
    if [[ "$(docker inspect "$container" --format '{{.State.Health.Status}}' 2>/dev/null || true)" == healthy ]]; then
      ready=1
      break
    fi
    sleep 2
  done
  [[ "$ready" == 1 ]] || { echo "Asterisk did not become healthy after recreation" >&2; return 1; }
}
rollback_on_error() {
  status=$?
  trap - EXIT
  if [[ "$status" != 0 && "$changed" == 1 ]]; then
    echo "Nateks activation failed; restoring protected runtime backup" >&2
    cp -a "$backup/nateks-pstn.conf" "$runtime_include"
    cp -a "$backup/extensions.conf" "$extensions"
    if [[ "$container_recreated" == 1 ]]; then
      channels=$(docker exec "$container" asterisk -rx 'core show channels count' 2>/dev/null || true)
      if [[ -z "$channels" ]] || printf '%s\n' "$channels" | grep -Eq '0 active channels'; then
        recreate_active_asterisk || echo "NATEKS_ROLLBACK_RESTART_FAILED" >&2
      else
        echo "NATEKS_ROLLBACK_RESTART_DEFERRED: active call detected; restored host files are saved in $backup" >&2
      fi
    fi
    echo "NATEKS_RUNTIME_RESTORED backup=$backup" >&2
  fi
  exit "$status"
}
trap rollback_on_error EXIT

changed=1
configure_args=("$runtime_dir")
if grep -Eq '^[[:space:]]*\[nateks-fxo\][[:space:]]*$' "$runtime_include"; then
  configure_args+=(--replace)
fi
python3 "$release/deploy/configure_nateks_pstn_runtime.py" "${configure_args[@]}"
python3 "$release/deploy/sync_asterisk_extensions_runtime.py" \
  "$extensions" "$release/conference/asterisk/extensions.conf" "$runtime_include"

channels=$(docker exec "$container" asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || {
  echo "A call started during PSTN activation" >&2; exit 1;
}
container_recreated=1
recreate_active_asterisk
docker exec "$container" test -r /etc/asterisk/nateks-pstn.conf
docker exec "$container" asterisk -rx 'pjsip show endpoints' \
  | grep -Eq 'Endpoint:[[:space:]]+nateks-fxo([/[:space:]]|$)'
docker exec "$container" asterisk -rx 'pjsip show aor 701' \
  | grep -Eq 'Aor:[[:space:]]+701([[:space:]]|$)'
docker exec "$container" asterisk -rx 'pjsip show identify nateks-fxo-identify' | grep -Fq nateks-fxo
docker exec "$container" asterisk -rx 'dialplan show nateks-inbound' | grep -Fq 'PJSIP/2015'
docker exec "$container" asterisk -rx 'dialplan show nateks-inbound-predial' \
  | grep -Fq 'CHANNEL(callgroup)=63'
outbound_context=$(docker exec "$container" asterisk -rx 'dialplan show phoneguy-sip')
printf '%s\n' "$outbound_context" | python3 "$release/deploy/verify_nateks_outbound_dialplan.py"

aor_status=$(docker exec "$container" asterisk -rx 'pjsip show aor 701')
if printf '%s\n' "$aor_status" | grep -Fq '192.168.20.236'; then
  echo "NATEKS_CONTACT_REGISTERED"
else
  echo "NATEKS_CONTACT_PENDING: peer and routes are loaded; check gateway registration status"
fi

changed=0
trap - EXIT
printf 'NATEKS_PSTN_ACTIVATION_COMPLETE backup=%s\n' "$backup"
