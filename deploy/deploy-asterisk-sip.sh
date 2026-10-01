#!/usr/bin/env bash
set -euo pipefail

approved_target=ubuntu@192.168.20.70
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
target=${DEPLOY_SIP_TARGET:-$approved_target}
[[ "$target" == "$approved_target" ]] || { echo "DEPLOY_SIP_TARGET must be $approved_target" >&2; exit 2; }
[[ "$#" == 0 ]] || { echo "Usage: $0" >&2; exit 2; }

stamp=${DEPLOY_SIP_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid SIP release stamp" >&2; exit 2; }
stage="/tmp/voice-sip-stage-$stamp"

required=(
  conference/asterisk deploy/compose.conference.yaml
  deploy/update_pjsip_wss_transport.py deploy/firewall.sh
  deploy/remote-deploy-asterisk-sip.sh deploy/rollback-asterisk-sip.sh
)
for item in "${required[@]}"; do
  [[ -e "$repo/$item" ]] || { echo "Missing SIP rollout input: $item" >&2; exit 2; }
done

if [[ "${DEPLOY_SIP_SKIP_CHECKS:-0}" != 1 ]]; then
  pytest_bin=${PYTEST_BIN:-"$repo/.venv/bin/pytest"}
  if [[ ! -x "$pytest_bin" ]]; then
    common_dir=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)
    shared_root=${common_dir%/.git}
    pytest_bin="$shared_root/.venv/bin/pytest"
  fi
  [[ -x "$pytest_bin" ]] || { echo "pytest executable not found; set PYTEST_BIN" >&2; exit 2; }
  "$pytest_bin" -q \
    tests/test_internet_sip_security.py \
    tests/test_sip_cert_bundle.py \
    tests/test_update_pjsip_transport.py \
    tests/test_browser_webrtc_config.py \
    tests/test_conference_config.py::test_module_allowlist_is_exact_and_contains_release_dependencies
  node --test "$repo"/tests/*.test.cjs
  git -C "$repo" diff --check
fi

ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "mkdir -m 0700 '$stage'"
rsync -a --relative \
  conference/asterisk deploy/compose.conference.yaml \
  deploy/update_pjsip_wss_transport.py deploy/firewall.sh \
  deploy/remote-deploy-asterisk-sip.sh deploy/rollback-asterisk-sip.sh \
  "$target:$stage/"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" \
  "sudo bash '$stage/deploy/remote-deploy-asterisk-sip.sh' '$stamp' '$stage'"
