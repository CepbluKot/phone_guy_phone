#!/usr/bin/env bash
# Prune old Go/Asterisk release directories on VM209 without removing anything a container or a rollback still needs.
# Run ON the VM as root. Dry run by default; pass --apply to delete.
#   sudo deploy/prune-go-releases.sh [--keep N] [--apply]
#
# A release is kept when ANY of these is true (the 2026-10-24 cleanup missed the second one and broke the Go container on its next
# recreate, because its .env pointed VOICE_ARI_RUNTIME at an older release):
#   - it is one of the newest N releases (default 8),
#   - it is the Go runtime owner (/opt/voice-changer/.go-runtime-owner),
#   - a path under it is a bind-mount source, a compose config file or an .env value of ANY container (running or not),
#   - the .env of a kept release refers to it,
#   - one of the newest two update manifests (/opt/voice-go/updates/*/manifest) refers to it (rollback).
set -euo pipefail

keep=8; apply=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) keep=${2:?--keep needs a number}; shift 2 ;;
    --apply) apply=1; shift ;;
    *) echo "usage: $0 [--keep N] [--apply]" >&2; exit 2 ;;
  esac
done
[[ "$keep" =~ ^[0-9]+$ ]] || { echo "--keep must be a number" >&2; exit 2; }

root=${PRUNE_GO_ROOT:-/opt/voice-go}
owner_file=${PRUNE_OWNER_FILE:-/opt/voice-changer/.go-runtime-owner}
releases=$root/releases
stamp_re='[0-9]{8}T[0-9]{6}Z'
declare -A keepset

mark() { # stamp
  [[ "$1" =~ ^$stamp_re$ ]] && keepset[$1]=1 || true
}
mark_paths() { # reads text on stdin, marks every release stamp found under $releases
  while read -r s; do mark "$s"; done < <(grep -oE "$releases/$stamp_re" | sed "s|$releases/||" | sort -u)
}

# 1. newest N and the runtime owner
while read -r s; do mark "$s"; done < <(ls -1 "$releases" | sort -r | head -"$keep")
[ -f "$owner_file" ] && mark "$(cat "$owner_file")"

# 2. everything every container (running or not) points at: mounts, compose labels, env
if command -v docker >/dev/null 2>&1; then
  for c in $(docker ps -aq); do
    # process substitution, not a pipe: marking must happen in this shell, not in a subshell
    mark_paths < <(docker inspect "$c" --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}{{range $k,$v := .Config.Labels}}{{$v}}{{"\n"}}{{end}}{{range .Config.Env}}{{.}}{{"\n"}}{{end}}')
  done
fi

# 3. the .env files of kept releases (their VOICE_ARI_RUNTIME / CONFERENCE_RUNTIME may name older releases); repeat until stable
for _ in 1 2 3; do
  for s in "${!keepset[@]}"; do
    [ -f "$releases/$s/.env" ] && mark_paths < "$releases/$s/.env"
  done
done

# 4. rollback manifests of the newest two updates
for m in $(ls -1dt $root/updates/*/manifest 2>/dev/null | head -2); do mark_paths < "$m"; done

printf 'keeping %s release(s): %s\n' "${#keepset[@]}" "$(printf '%s\n' "${!keepset[@]}" | sort | tr '\n' ' ')"
removed=0
while read -r r; do
  [[ "$r" =~ ^$stamp_re$ ]] || continue
  [ -n "${keepset[$r]:-}" ] && continue
  if [ "$apply" = 1 ]; then rm -rf -- "${releases:?}/$r"; echo "removed $r"; else echo "would remove $r"; fi
  removed=$((removed + 1))
done < <(ls -1 "$releases" | sort)
echo "$([ "$apply" = 1 ] && echo removed || echo 'would remove') $removed release(s)"
