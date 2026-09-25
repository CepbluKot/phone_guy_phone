#!/usr/bin/env bash
set -euo pipefail

validate_stamp() {
  [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || {
    echo "Invalid Go staging stamp; expected YYYYMMDDTHHMMSSZ" >&2
    return 2
  }
}

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi
stamp=$1
validate_stamp "$stamp"

root_prefix=${DEPLOY_GOWEB_ROOT_PREFIX:-}
if [ -n "$root_prefix" ] && { [[ "$root_prefix" != /* ]] || [ "$root_prefix" = / ] || [[ "$root_prefix" == *..* ]]; }; then
  echo "Unsafe Go staging rollback root prefix" >&2
  exit 2
fi
path() { printf '%s%s' "$root_prefix" "$1"; }

stage="$(path /opt/voice-go)/staging/$stamp"
staging_root="$(path /opt/voice-go)/staging"
if [ -L "$(path /opt/voice-go)" ] || [ -L "$staging_root" ] || [ -L "$stage" ] \
    || [ ! -d "$stage" ] || [ "$(cat "$stage/.voice-go-stage-stamp" 2>/dev/null || true)" != "$stamp" ]; then
  echo "Go staging rollback refused: matching stage marker is missing" >&2
  exit 1
fi
if [ ! -f "$stage/compose.yaml" ] || [ ! -f "$stage/.env" ]; then
  echo "Go staging rollback refused: stage manifest is incomplete" >&2
  exit 1
fi

project="voice-go-stage-${stamp,,}"
docker compose -p "$project" -f "$stage/compose.yaml" --env-file "$stage/.env" down --remove-orphans
remaining=$(docker ps -aq --filter "label=com.docker.compose.project=$project")
if [ -n "$remaining" ]; then
  echo "Go staging rollback incomplete: candidate containers remain" >&2
  exit 1
fi
rm -rf -- "$stage"
echo "STAGE_ROLLBACK_COMPLETE stamp=$stamp"
