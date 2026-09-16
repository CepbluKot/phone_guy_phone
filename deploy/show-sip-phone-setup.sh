#!/usr/bin/env bash
# Print one current SIP account from VM209 for intentional handset setup.
set -euo pipefail

target=ubuntu@192.168.20.70
extension=${1:-}

case "$extension" in
  1983|1987|2014) ;;
  *)
    echo "Usage: $0 {1983|1987|2014}" >&2
    exit 2
    ;;
esac

ssh "$target" sudo bash -s -- "$extension" <<'REMOTE'
set -euo pipefail
extension=$1
pjsip=$(docker inspect voice-conference-asterisk-1 \
  --format '{{range .Mounts}}{{if eq .Destination "/etc/asterisk/pjsip.conf"}}{{.Source}}{{end}}{{end}}')
runtime=${pjsip%/pjsip.conf}
password_file="$runtime/sip-$extension-password"
test -f "$password_file"

printf 'Server: 192.168.20.70\n'
printf 'Port: 5060\n'
printf 'Transport: UDP\n'
printf 'Username: %s\n' "$extension"
printf 'Password: %s\n' "$(<"$password_file")"
printf 'Codec: PCMA / G.711 A-law\n'
printf 'Dial: 600\n'
REMOTE
