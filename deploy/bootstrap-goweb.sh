#!/usr/bin/env bash
set -euo pipefail

approved_target=ubuntu@192.168.20.70
target=${DEPLOY_GOWEB_TARGET:-$approved_target}
if [[ "$target" != "$approved_target" ]]; then
  echo "DEPLOY_GOWEB_TARGET must be the approved VM209: $approved_target" >&2
  exit 2
fi
if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 <admin-password-file>" >&2
  exit 2
fi

password_file=$1
if [[ -L "$password_file" || ! -f "$password_file" ]]; then
  echo "Admin password must be a regular, non-symlink file" >&2
  exit 2
fi
mode=$(stat -c '%a' -- "$password_file")
if (( (8#$mode & 077) != 0 )); then
  echo "Admin password file must not be accessible by group or others" >&2
  exit 2
fi
password_size=$(wc -c < "$password_file")
if (( password_size < 24 || password_size > 256 )); then
  echo "Admin password file must contain 24 to 256 bytes" >&2
  exit 2
fi
if [[ "$(wc -l < "$password_file")" -ne 0 ]] || LC_ALL=C grep -q $'\r' "$password_file"; then
  echo "Admin password file must contain no line breaks" >&2
  exit 2
fi

repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
route_config="$repo/deploy/voice-routing.example.json"
test -s "$route_config" || { echo "Missing route configuration template" >&2; exit 2; }

# Refuse to replace either file. This command only bootstraps files; it does not
# start Go, stop Python services, alter Asterisk, or switch live routing.
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  'sudo test ! -e /etc/voice-changer/voice-routing.json && sudo test ! -L /etc/voice-changer && sudo test ! -e /etc/voice-changer-admin/password && sudo test ! -L /etc/voice-changer-admin'
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  'sudo install -d -o 10001 -g 10001 -m 0750 /etc/voice-changer && sudo install -d -o root -g 10001 -m 0750 /etc/voice-changer-admin'
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  'sudo install -o 10001 -g 10001 -m 0640 /dev/stdin /etc/voice-changer/voice-routing.json' < "$route_config"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  'sudo install -o 10001 -g 10001 -m 0400 /dev/stdin /etc/voice-changer-admin/password' < "$password_file"
echo "GOWEB_BOOTSTRAP_FILES_READY target=$target"
