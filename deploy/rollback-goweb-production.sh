#!/usr/bin/env bash
set -euo pipefail

validate_stamp() {
  [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || {
    echo "Invalid Go production stamp; expected YYYYMMDDTHHMMSSZ" >&2
    return 2
  }
}

if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi
stamp=$1
validate_stamp "$stamp"

root_prefix=${DEPLOY_GOWEB_ROOT_PREFIX:-}
if [[ -n "$root_prefix" ]] && { [[ "$root_prefix" != /* ]] || [[ "$root_prefix" == / ]] || [[ "$root_prefix" == *..* ]]; }; then
  echo "Unsafe Go production rollback root prefix" >&2
  exit 2
fi
path() { printf '%s%s' "$root_prefix" "$1"; }

backup="$(path /opt/voice-go)/backups/$stamp"
manifest="$backup/manifest"
go_release="$(path /opt/voice-go)/releases/$stamp"
caddy_live=$(path /etc/caddy/Caddyfile)
caddy_source=$(path /opt/voice-changer/deploy/Caddyfile)
conference_root=$(path /opt/voice-conference)
failed=0

if [ ! -d "$backup" ] || [ -L "$backup" ] || [ ! -f "$manifest" ] || [ -L "$manifest" ]; then
  echo "ROLLBACK_FAILED missing or unsafe production snapshot: $backup" >&2
  exit 1
fi

manifest_stamp=
conference_release=
selfmonitor_was_active=
selfmonitor_was_enabled=
http_container_was_running=
phonebook_was_present=
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    stamp=*) manifest_stamp=${line#stamp=} ;;
    conference_release=*) conference_release=${line#conference_release=} ;;
    selfmonitor_was_active=*) selfmonitor_was_active=${line#selfmonitor_was_active=} ;;
    selfmonitor_was_enabled=*) selfmonitor_was_enabled=${line#selfmonitor_was_enabled=} ;;
    http_container_was_running=*) http_container_was_running=${line#http_container_was_running=} ;;
    phonebook_was_present=*) phonebook_was_present=${line#phonebook_was_present=} ;;
    *) echo "ROLLBACK_FAILED invalid production manifest" >&2; exit 1 ;;
  esac
done < "$manifest"
if [[ "$manifest_stamp" != "$stamp" || ! "$conference_release" =~ ^[0-9]{8}T[0-9]{6}Z$ \
    || "$selfmonitor_was_active" != active \
    || "$selfmonitor_was_enabled" != enabled \
    || "$http_container_was_running" != stopped \
    || ( "$phonebook_was_present" != true && "$phonebook_was_present" != false ) ]]; then
  echo "ROLLBACK_FAILED production snapshot does not match expected stopped-HTTP baseline" >&2
  exit 1
fi

conference_compose="$conference_root/releases/$conference_release/deploy/compose.conference.yaml"
conference_env="$conference_root/releases/$conference_release/.env"
if [[ ! -f "$go_release/deploy/compose.goweb.yaml" || ! -f "$go_release/.env" \
    || ! -f "$conference_compose" || ! -f "$conference_env" \
    || ! -f "$backup/Caddyfile" || ! -f "$backup/source-Caddyfile" \
    || ! -f "$backup/selfmonitor.service" || ! -f "$backup/selfmonitor.env" \
    || ! -f "$backup/voice-routing.json" || ! -f "$backup/admin-password" \
    || ! -s "$backup/asterisk-image-id" || ! -s "$backup/controller-image-id" \
    || ! -s "$backup/route-config-sha256" || ! -s "$backup/admin-password-sha256" ]]; then
  echo "ROLLBACK_FAILED production snapshot is incomplete" >&2
  exit 1
fi
if { [ "$phonebook_was_present" = true ] && { [ ! -f "$backup/phonebook.json" ] || [ ! -s "$backup/phonebook-sha256" ]; }; } \
    || { [ "$phonebook_was_present" = false ] && [ ! -f "$backup/phonebook-absent" ]; }; then
  echo "ROLLBACK_FAILED phonebook snapshot does not match manifest" >&2
  exit 1
fi

echo "ROLLBACK_BEGIN stamp=$stamp"
docker compose -p voice-go -f "$go_release/deploy/compose.goweb.yaml" --env-file "$go_release/.env" down --remove-orphans || failed=1
if [ -n "$(docker ps -q --filter name=voice-go 2>/dev/null || true)" ]; then
  echo "rollback: Go container remains running" >&2
  failed=1
fi

if ! cp -a "$backup/Caddyfile" "$caddy_live" \
    || ! cp -a "$backup/source-Caddyfile" "$caddy_source" \
    || ! cp -a "$backup/selfmonitor.service" "$(path /etc/systemd/system/voice-selfmonitor.service)" \
    || ! cp -a "$backup/selfmonitor.env" "$(path /etc/voice-selfmonitor.env)" \
    || ! cp -a "$backup/voice-routing.json" "$(path /etc/voice-changer/voice-routing.json)" \
    || ! cp -a "$backup/admin-password" "$(path /etc/voice-changer-admin/password)"; then
  echo "rollback: saved files could not be restored" >&2
  failed=1
fi
if [ "$phonebook_was_present" = true ]; then
  cp -a "$backup/phonebook.json" "$(path /etc/voice-changer/phonebook.json)" || failed=1
else
  rm -f -- "$(path /etc/voice-changer/phonebook.json)" || failed=1
fi

if ! caddy validate --config "$caddy_live" || ! systemctl reload caddy; then
  echo "rollback: restored Caddy config failed validation or reload" >&2
  failed=1
fi
if ! cmp -s "$backup/Caddyfile" "$caddy_live" \
    || ! cmp -s "$backup/source-Caddyfile" "$caddy_source" \
    || ! cmp -s "$backup/voice-routing.json" "$(path /etc/voice-changer/voice-routing.json)" \
    || ! cmp -s "$backup/admin-password" "$(path /etc/voice-changer-admin/password)"; then
  echo "rollback: restored config bytes do not match the snapshot" >&2
  failed=1
fi
if [ "$phonebook_was_present" = true ] \
    && { ! cmp -s "$backup/phonebook.json" "$(path /etc/voice-changer/phonebook.json)" \
      || ! printf '%s  %s\n' "$(cat "$backup/phonebook-sha256")" "$(path /etc/voice-changer/phonebook.json)" | sha256sum -c - >/dev/null; }; then
  echo "rollback: restored phonebook does not match the snapshot" >&2
  failed=1
fi
if ! printf '%s  %s\n' "$(cat "$backup/route-config-sha256")" "$(path /etc/voice-changer/voice-routing.json)" | sha256sum -c - >/dev/null \
    || ! printf '%s  %s\n' "$(cat "$backup/admin-password-sha256")" "$(path /etc/voice-changer-admin/password)" | sha256sum -c - >/dev/null; then
  echo "rollback: route config or admin secret checksum mismatch" >&2
  failed=1
fi

systemctl daemon-reload || failed=1
systemctl enable voice-selfmonitor.service || failed=1

if ! docker compose -p voice-conference -f "$conference_compose" --env-file "$conference_env" up -d --no-build --force-recreate; then
  echo "rollback: prior conference containers could not be restored" >&2
  failed=1
fi
systemctl restart voice-selfmonitor.service || failed=1

if [ "$http_container_was_running" = stopped ] \
    && [ -n "$(docker ps -q --filter name=voice-changer-voice-1 2>/dev/null || true)" ]; then
  echo "rollback: legacy HTTP container unexpectedly started during rollback" >&2
  failed=1
fi

for attempt in $(seq 1 "${DEPLOY_GOWEB_ROLLBACK_HEALTH_ATTEMPTS:-20}"); do
  controller=$(docker inspect voice-conference-controller-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)
  asterisk=$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)
  selfmonitor=$(curl --max-time 3 -fsS http://127.0.0.1:8096/healthz 2>/dev/null || true)
  rvc=$(curl --max-time 3 -fsS http://127.0.0.1:8090/healthz 2>/dev/null || true)
  if [ "$controller" = healthy ] && [ "$asterisk" = healthy ] \
      && [ "$selfmonitor" = '{"status":"ready"}' ] \
      && printf '%s' "$rvc" | grep -q '"status":"ready"'; then
    break
  fi
  if [ "$attempt" -eq "${DEPLOY_GOWEB_ROLLBACK_HEALTH_ATTEMPTS:-20}" ]; then
    echo "rollback: prior conference, self-monitor, or RVC health did not recover" >&2
    failed=1
  fi
  sleep "${DEPLOY_GOWEB_ROLLBACK_HEALTH_DELAY:-2}"
done

if [ -n "$(docker ps -q --filter name=voice-go 2>/dev/null || true)" ]; then
  echo "rollback: Go container remains running after restore" >&2
  failed=1
fi
if [ "$(docker inspect voice-conference-asterisk-1 --format '{{.Image}}' 2>/dev/null || true)" != "$(cat "$backup/asterisk-image-id")" ] \
    || [ "$(docker inspect voice-conference-controller-1 --format '{{.Image}}' 2>/dev/null || true)" != "$(cat "$backup/controller-image-id")" ]; then
  echo "rollback: prior container image identity does not match snapshot" >&2
  failed=1
fi
if [ "$selfmonitor_was_active" = active ] && ! systemctl is-active --quiet voice-selfmonitor.service; then
  echo "rollback: self-monitor active state differs from snapshot" >&2
  failed=1
fi
if [ "$selfmonitor_was_enabled" = enabled ] && ! systemctl is-enabled --quiet voice-selfmonitor.service; then
  echo "rollback: self-monitor enabled state differs from snapshot" >&2
  failed=1
fi

if [ "$failed" -ne 0 ]; then
  echo "ROLLBACK_FAILED stamp=$stamp" >&2
  exit 1
fi
rm -f -- "$(path /opt/voice-changer)/.go-runtime-owner"
echo "ROLLBACK_COMPLETE stamp=$stamp"
