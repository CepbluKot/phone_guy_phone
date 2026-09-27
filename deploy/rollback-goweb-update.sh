#!/usr/bin/env bash
set -euo pipefail

target=ubuntu@192.168.20.70
if [[ "$#" -ne 1 || ! "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Usage: $0 UPDATE_STAMP" >&2
  exit 2
fi
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" sudo bash -s -- "$1" <<'REMOTE'
set -euo pipefail
stamp=$1; root=/opt/voice-go; backup="$root/updates/$stamp"; manifest="$backup/manifest"
[[ -f "$manifest" && ! -L "$manifest" ]] || { echo "Update backup not found" >&2; exit 1; }
declare -A values=()
while IFS='=' read -r key value; do
  case "$key" in go_release|asterisk_release|owner|old_go_image|old_asterisk_tag|old_runtime) values[$key]=$value ;; *) echo "Invalid update manifest" >&2; exit 1 ;; esac
done < "$manifest"
[[ "${values[owner]:-}" =~ ^[0-9]{8}T[0-9]{6}Z$ && "$(cat /opt/voice-changer/.go-runtime-owner)" = "${values[owner]}" ]] || { echo "Go owner changed since update" >&2; exit 1; }
for key in go_release asterisk_release old_runtime; do [[ "${values[$key]:-}" == /opt/voice-go/releases/* && -d "${values[$key]}" ]] || { echo "Invalid rollback release path" >&2; exit 1; }; done
[[ -f "$backup/go.env" && -f "$backup/asterisk.env" && -f "$backup/Caddyfile" && -f "$backup/source-Caddyfile" ]] || { echo "Update snapshot is incomplete" >&2; exit 1; }
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Active calls detected" >&2; exit 1; }
docker compose -p voice-conference -f "${values[asterisk_release]}/deploy/compose.conference.yaml" --env-file "$backup/asterisk.env" up -d --no-build --no-deps --force-recreate asterisk
docker compose -p voice-go -f "${values[go_release]}/deploy/compose.goweb.yaml" --env-file "$backup/go.env" up -d --no-build --force-recreate voice-go
cp -a "$backup/Caddyfile" /etc/caddy/Caddyfile
cp -a "$backup/source-Caddyfile" /opt/voice-changer/deploy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
for _ in $(seq 1 30); do
  if [[ "$(docker inspect voice-go --format '{{.State.Health.Status}}' 2>/dev/null || true)" = healthy \
      && "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)" = healthy ]] \
      && curl --max-time 3 -fsS http://192.168.20.70:8080/healthz >/dev/null; then
    echo "GOWEB_UPDATE_ROLLBACK_COMPLETE stamp=$stamp"
    exit 0
  fi
  sleep 2
done
echo "ROLLBACK_FAILED Go/Asterisk health did not recover" >&2
exit 70
REMOTE
