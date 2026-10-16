#!/usr/bin/env bash
set -euo pipefail
trap 'status=$?; printf "SIP_ROLLOUT_FAILURE line=%s status=%s\n" "$LINENO" "$status" >&2' ERR
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ "$#" == 2 ]] || { echo "Usage: remote-deploy-asterisk-sip.sh STAMP STAGE" >&2; exit 2; }
stamp=$1
stage=$2
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid SIP release stamp" >&2; exit 2; }
[[ "$stage" == "/tmp/voice-sip-stage-$stamp" ]] || { echo "Invalid SIP stage path" >&2; exit 2; }

go_root=/opt/voice-go
new_release="$go_root/releases/$stamp"
backup="$go_root/backups/sip-$stamp"
firewall_source=/opt/voice-changer/deploy/firewall.sh
active_compose=$(docker inspect voice-conference-asterisk-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
case "$active_compose" in
  "$go_root"/releases/*/deploy/compose.conference.yaml) ;;
  *) echo "Unexpected Asterisk release owner" >&2; exit 1 ;;
esac
old_release=$(dirname "$(dirname "$active_compose")")
old_env="$old_release/.env"
[[ "${old_release##*/}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Invalid active release directory" >&2; exit 1; }
[[ -f "$old_env" && -f "$active_compose" && -f "$firewall_source" ]] || { echo "Active release or firewall source is missing" >&2; exit 1; }
[[ ! -e "$new_release" && ! -e "$backup" ]] || { echo "SIP release stamp already exists" >&2; exit 1; }
[[ "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}')" == healthy ]] || {
  echo "Asterisk is not healthy" >&2; exit 1;
}
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Active calls prevent Asterisk restart" >&2; exit 1; }
[[ "$(df -Pm /opt | awk 'NR==2 {print $4}')" -ge 4096 ]] || { echo "Insufficient /opt disk headroom" >&2; exit 1; }
[[ -s /etc/voice-certs/phone-sip/current/fullchain.pem && -s /etc/voice-certs/phone-sip/current/key.pem ]] || {
  echo "Validated phone TLS certificate is not staged" >&2; exit 1;
}

install -d -o root -g root -m 0700 "$backup"
printf '%s\n' "$old_release" > "$backup/previous-release"
docker inspect voice-conference-asterisk-1 --format '{{.Image}}' > "$backup/asterisk-image-id"
cp -a "$firewall_source" "$backup/firewall.sh"
iptables-save -t filter > "$backup/iptables-filter.rules"
chmod 0600 "$backup"/*
printf 'stamp=%s\nprevious_release=%s\n' "$stamp" "$old_release" > "$backup/manifest"
chmod 0600 "$backup/manifest"

live_changed=0
rollback_on_error() {
  status=$?
  trap - EXIT
  if [[ "$status" != 0 ]]; then
    if [[ "$live_changed" == 1 ]]; then
      echo "Asterisk SIP rollout failed; restoring previous release" >&2
      bash "$stage/deploy/rollback-asterisk-sip.sh" "$stamp" || {
        echo "ROLLBACK_FAILED stamp=$stamp" >&2
        exit 70
      }
    else
      current_compose=$(docker inspect voice-conference-asterisk-1 \
        --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)
      if [[ "$current_compose" != "$new_release/deploy/compose.conference.yaml" ]]; then
        rm -rf -- "$new_release"
        echo "PRELIVE_CANDIDATE_REMOVED stamp=$stamp" >&2
      else
        echo "PRELIVE_CANDIDATE_PRESERVED_ACTIVE stamp=$stamp" >&2
      fi
    fi
    rm -rf -- "$stage"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

cp -a "$old_release" "$new_release"
cp -a "$stage/conference/asterisk/." "$new_release/conference/asterisk/"
install -o root -g root -m 0644 "$stage/deploy/compose.conference.yaml" "$new_release/deploy/compose.conference.yaml"
install -o root -g root -m 0644 "$stage/deploy/update_pjsip_wss_transport.py" "$new_release/deploy/update_pjsip_wss_transport.py"
install -o root -g root -m 0644 "$stage/deploy/sync_asterisk_pstn_runtime.py" "$new_release/deploy/sync_asterisk_pstn_runtime.py"
install -o root -g root -m 0644 "$stage/deploy/sync_asterisk_extensions_runtime.py" "$new_release/deploy/sync_asterisk_extensions_runtime.py"
install -o root -g root -m 0644 "$stage/deploy/configure_nateks_pstn_runtime.py" "$new_release/deploy/configure_nateks_pstn_runtime.py"
install -o root -g root -m 0644 "$stage/deploy/verify_nateks_outbound_dialplan.py" "$new_release/deploy/verify_nateks_outbound_dialplan.py"
install -o root -g root -m 0755 "$stage/deploy/activate-nateks-pstn.sh" "$new_release/deploy/activate-nateks-pstn.sh"
install -o root -g root -m 0644 "$stage/deploy/firewall.sh" "$new_release/deploy/firewall.sh"
install -o root -g root -m 0755 "$stage/deploy/rollback-asterisk-sip.sh" "$new_release/deploy/rollback-asterisk-sip.sh"

python3 - "$new_release/.env" "$new_release/runtime" "$stamp" <<'PY'
from pathlib import Path
import re
import sys

env_path = Path(sys.argv[1])
runtime = sys.argv[2]
stamp = sys.argv[3]
text = env_path.read_text()
required = {"CONFERENCE_TAG", "CONFERENCE_RUNTIME", "VOICE_ARI_RUNTIME"}
for key, value in {
    "CONFERENCE_TAG": stamp,
    "CONFERENCE_RUNTIME": runtime,
    "VOICE_ARI_RUNTIME": runtime,
}.items():
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
    if len(pattern.findall(text)) != 1:
        raise SystemExit(f"deployment environment is missing unique {key}")
    text = pattern.sub(f"{key}={value}", text)
if not required.issubset(set(re.findall(r"(?m)^([A-Z0-9_]+)=", text))):
    raise SystemExit("deployment environment is incomplete")
env_path.write_text(text)
env_path.chmod(0o600)
PY

python3 "$new_release/deploy/sync_asterisk_pstn_runtime.py" \
  "$new_release/runtime/asterisk" \
  "$new_release/conference/asterisk/nateks-pstn.conf.template"
python3 "$new_release/deploy/update_pjsip_wss_transport.py" \
  "$new_release/runtime/asterisk/pjsip.conf" \
  "$new_release/conference/asterisk/pjsip.conf.template"
python3 "$new_release/deploy/sync_asterisk_extensions_runtime.py" \
  "$new_release/runtime/asterisk/extensions.conf" \
  "$new_release/conference/asterisk/extensions.conf" \
  "$new_release/runtime/asterisk/nateks-pstn.conf"
install -o root -g root -m 0644 \
  "$new_release/conference/asterisk/sorcery.conf" \
  "$new_release/runtime/asterisk/sorcery.conf"
cp "$new_release/conference/asterisk/modules.conf" \
  "$new_release/runtime/asterisk/modules.conf"
chown --reference="$old_release/runtime/asterisk/modules.conf" \
  "$new_release/runtime/asterisk/modules.conf"
chmod --reference="$old_release/runtime/asterisk/modules.conf" \
  "$new_release/runtime/asterisk/modules.conf"
docker compose -p voice-conference -f "$new_release/deploy/compose.conference.yaml" \
  --env-file "$new_release/.env" config --quiet
docker build -f "$new_release/conference/asterisk/Dockerfile" \
  -t "voice-conference-asterisk:$stamp" "$new_release/conference/asterisk"

live_changed=1
install -o root -g root -m 0755 "$new_release/deploy/firewall.sh" "$firewall_source"
/bin/sh "$firewall_source"
iptables -C DOCKER-USER -i eth0 -p udp --dport 5060 -j VOICE_SIP_INGRESS
iptables -C VOICE_SIP_INGRESS -p udp --dport 5060 -s 192.168.20.0/24 -j ACCEPT
iptables -C VOICE_SIP_INGRESS -p udp --dport 5060 -j DROP
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -s 192.168.20.12 -j ACCEPT
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -s 10.19.87.1 -j ACCEPT
iptables -C VOICE_SIP_INGRESS -p tcp --dport 5063 -j DROP
iptables -C DOCKER-USER -i eth0 -p tcp --dport 5063 -j VOICE_SIP_INGRESS
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "A call started before Asterisk restart" >&2; exit 1; }
docker compose -p voice-conference -f "$new_release/deploy/compose.conference.yaml" \
  --env-file "$new_release/.env" up -d --no-build --no-deps --force-recreate asterisk

ready=0
for attempt in $(seq 1 40); do
  if [[ "$(docker inspect voice-conference-asterisk-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)" == healthy ]]; then
    ready=1
    break
  fi
  sleep 2
done
[[ "$ready" == 1 ]] || { echo "Asterisk health check failed" >&2; exit 1; }

docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-tls-internet' \
  | grep -q 'Transport:  transport-tls-internet'
grep -Eq '^\[2015\]\(phone-endpoint\)$' "$new_release/runtime/asterisk/pjsip.conf" \
  || { echo "Selected inbound phone 2015 is missing from runtime PJSIP config" >&2; exit 1; }
endpoints=$(docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show endpoints')
for extension in 1987 1988 2014 2015 2016 1234; do
  if grep -Eq "^\[$extension\]\(phone-endpoint\)$" "$new_release/runtime/asterisk/pjsip.conf"; then
    printf '%s\n' "$endpoints" | grep -Eq "Endpoint:[[:space:]]+$extension(/|[[:space:]]|$)"
  fi
done
docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show endpoint 1234' \
  | grep -Eq 'transport[[:space:]]+:[[:space:]]+transport-tls-1234-internet-legacy([[:space:]]|$)'
docker exec voice-conference-asterisk-1 asterisk -rx \
  'pjsip show transport transport-tls-1234-internet-legacy' \
  | grep -q 'Transport:  transport-tls-1234-internet-legacy'
docker port voice-conference-asterisk-1 5063/tcp \
  | grep -Fxq '192.168.20.70:5063'
openssl s_client -connect 192.168.20.70:5061 -servername phone.awesomeio.ru \
  -verify_hostname phone.awesomeio.ru -verify_return_error </dev/null >/dev/null 2>&1
openssl s_client -connect 192.168.20.70:5063 -servername phone.awesomeio.ru \
  -verify_hostname phone.awesomeio.ru -verify_return_error \
  -tls1 -cipher 'AES128-SHA:@SECLEVEL=0' </dev/null >/dev/null 2>&1
curl --max-time 5 -fsS http://192.168.20.70:8080/healthz >/dev/null

app_ready=0
for attempt in $(seq 1 30); do
  if docker exec voice-conference-asterisk-1 asterisk -rx 'ari show apps' | grep -q voice-control; then
    app_ready=1
    break
  fi
  sleep 1
done
if [[ "$app_ready" != 1 ]]; then
  docker compose -p voice-go -f "$old_release/deploy/compose.goweb.yaml" \
    --env-file "$old_env" restart voice-go
  for attempt in $(seq 1 30); do
    if docker exec voice-conference-asterisk-1 asterisk -rx 'ari show apps' | grep -q voice-control; then
      app_ready=1
      break
    fi
    sleep 1
  done
fi
[[ "$app_ready" == 1 ]] || { echo "Go ARI app did not reconnect to Asterisk" >&2; exit 1; }
docker exec voice-conference-asterisk-1 test -r /etc/asterisk/nateks-pstn.conf
docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show nateks-inbound' \
  | grep -Fq 'PJSIP/2015'
docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show nateks-inbound-predial' \
  | grep -Fq 'CHANNEL(callgroup)=63'
channels=$(docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
printf '%s\n' "$channels" | grep -Eq '0 active channels' || { echo "Unexpected active call after Asterisk restart" >&2; exit 1; }

if grep -Fq 'exten => _7XXXXXXXXXX,1,' "$new_release/runtime/asterisk/extensions.conf"; then
  docker exec voice-conference-asterisk-1 asterisk -rx 'module show like res_pjsip_endpoint_identifier_ip.so' \
    | grep -Eq 'res_pjsip_endpoint_identifier_ip\.so.*Running'
  printf '%s\n' "$endpoints" \
    | grep -Eq 'Endpoint:[[:space:]]+nateks-fxo([/[:space:]]|$)'
  docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show aor 701' \
    | grep -Eq 'Aor:[[:space:]]+701([[:space:]]|$)'
  docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show identify nateks-fxo-identify' \
    | grep -Fq nateks-fxo
  outbound_context=$(docker exec voice-conference-asterisk-1 asterisk -rx 'dialplan show phoneguy-sip')
  printf '%s\n' "$outbound_context" \
    | python3 "$new_release/deploy/verify_nateks_outbound_dialplan.py"
fi

live_changed=0
trap - EXIT
rm -rf -- "$stage"
printf 'ASTERISK_SIP_DEPLOY_COMPLETE stamp=%s previous=%s backup=%s edge=not-enabled\n' \
  "$stamp" "${old_release##*/}" "$backup"
