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
rollback_on_error() {
  status=$?
  trap - EXIT
  if [[ "$status" != 0 && "$changed" == 1 ]]; then
    echo "Nateks activation failed; restoring protected runtime backup" >&2
    cp -a "$backup/nateks-pstn.conf" "$runtime_include"
    cp -a "$backup/extensions.conf" "$extensions"
    docker exec "$container" asterisk -rx 'pjsip reload' >/dev/null 2>&1 || true
    docker exec "$container" asterisk -rx 'dialplan reload' >/dev/null 2>&1 || true
    echo "NATEKS_RUNTIME_RESTORED backup=$backup" >&2
  fi
  exit "$status"
}
trap rollback_on_error EXIT

changed=1
python3 "$release/deploy/configure_nateks_pstn_runtime.py" "$runtime_dir" --replace
python3 "$release/deploy/sync_asterisk_extensions_runtime.py" \
  "$extensions" "$release/conference/asterisk/extensions.conf" "$runtime_include"

channels=$(docker exec "$container" asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || {
  echo "A call started during PSTN activation" >&2; exit 1;
}
docker exec "$container" asterisk -rx 'pjsip reload' >/dev/null
docker exec "$container" asterisk -rx 'dialplan reload' >/dev/null
docker exec "$container" test -r /etc/asterisk/nateks-pstn.conf
docker exec "$container" asterisk -rx 'pjsip show endpoint nateks-fxo' \
  | grep -Eq 'Endpoint:[[:space:]]+nateks-fxo([/[:space:]]|$)'
docker exec "$container" asterisk -rx 'pjsip show aor 701' | grep -Fq 'Aor: 701'
docker exec "$container" asterisk -rx 'pjsip show identify nateks-fxo-identify' | grep -Fq nateks-fxo
docker exec "$container" asterisk -rx 'dialplan show nateks-inbound' | grep -Fq 'PJSIP/2015'
docker exec "$container" asterisk -rx 'dialplan show nateks-inbound-predial' \
  | grep -Fq 'CHANNEL(callgroup)=63'
for pattern in _7XXXXXXXXXX _8XXXXXXXXXX; do
  docker exec "$container" asterisk -rx "dialplan show ${pattern}@phoneguy-sip" \
    | grep -Fq 'nateks-fxo'
done

aor_status=$(docker exec "$container" asterisk -rx 'pjsip show aor 701')
if printf '%s\n' "$aor_status" | grep -Fq '192.168.20.236'; then
  echo "NATEKS_CONTACT_REGISTERED"
else
  echo "NATEKS_CONTACT_PENDING: peer and routes are loaded; check gateway registration status"
fi

changed=0
trap - EXIT
printf 'NATEKS_PSTN_ACTIVATION_COMPLETE backup=%s\n' "$backup"
