#!/usr/bin/env bash
# Deploy the private browser-to-1999 mirror without replacing the active
# conference runtime, /call route, Yealink contact, or voice-rvc.service.
# Never restarts asterisk/voice-rvc/conference containers.
set -euo pipefail

TARGET=${TARGET:-ubuntu@192.168.20.70}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STAGE="/tmp/voice-live-mirror-$STAMP"

echo "==> 0/7 scoped backup"
ssh "$TARGET" "sudo mkdir -p /opt/voice-selfmonitor/backups/$STAMP && sudo cp -a /etc/caddy/Caddyfile /opt/voice-selfmonitor/backups/$STAMP/Caddyfile && sudo cp -a /opt/voice-selfmonitor/app /opt/voice-selfmonitor/backups/$STAMP/app && sudo cp -a /opt/voice-changer/web/live /opt/voice-selfmonitor/backups/$STAMP/live && sudo cp -a /etc/systemd/system/voice-selfmonitor.service /opt/voice-selfmonitor/backups/$STAMP/voice-selfmonitor.service"

echo "==> 1/7 sync code"
ssh "$TARGET" 'mkdir -p /opt/voice-selfmonitor/app/selfmonitor /opt/voice-selfmonitor/app/conference'
scp -q "$ROOT/selfmonitor/"*.py "$TARGET:/opt/voice-selfmonitor/app/selfmonitor/"
scp -q "$ROOT/conference/__init__.py" "$ROOT/conference/media.py" \
  "$ROOT/conference/asterisk.py" \
  "$TARGET:/opt/voice-selfmonitor/app/conference/"

echo "==> 2/7 python environment"
ssh "$TARGET" '
if [ ! -x /opt/voice-selfmonitor/venv/bin/python ]; then
  python3 -m venv /opt/voice-selfmonitor/venv
  /opt/voice-selfmonitor/venv/bin/pip -q install httpx websockets
fi
/opt/voice-selfmonitor/venv/bin/python -c "import httpx, websockets; print(\"deps ok\")"'

echo "==> 3/7 credentials from the active conference runtime"
ssh "$TARGET" '
ARI_CONF=$(sudo docker inspect voice-conference-asterisk-1 --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep "ari.conf" | cut -d" " -f1)
sudo test -f "$ARI_CONF" || { echo "ari.conf not found"; exit 1; }
PASSWORD=$(sudo grep -oP "^password=\K.*" "$ARI_CONF" | head -1)
[ -n "$PASSWORD" ] || { echo "password missing in $ARI_CONF"; exit 1; }
printf "SELFMONITOR_ARI_URL=http://127.0.0.1:8092/ari\nSELFMONITOR_ARI_USERNAME=phoneguy\nSELFMONITOR_ARI_PASSWORD=%s\n" "$PASSWORD" \
  | sudo tee /etc/voice-selfmonitor.env > /dev/null
sudo chmod 600 /etc/voice-selfmonitor.env
echo "env written (password length ${#PASSWORD})"'

echo "==> 4/7 systemd unit"
scp -q "$ROOT/deploy/voice-selfmonitor.service" "$TARGET:/tmp/voice-selfmonitor.service"
ssh "$TARGET" '
sudo mv /tmp/voice-selfmonitor.service /etc/systemd/system/voice-selfmonitor.service
sudo systemctl daemon-reload
sudo systemctl enable voice-selfmonitor.service
sudo systemctl restart voice-selfmonitor.service
for i in $(seq 1 20); do
  sleep 1
  STATUS=$(curl -fsS -m 2 http://127.0.0.1:8096/healthz 2>/dev/null || true)
  [ "$STATUS" = "{\"status\":\"ready\"}" ] && { echo "service ready: $STATUS"; break; }
  [ "$i" = 20 ] && { echo "health timeout"; sudo journalctl -u voice-selfmonitor -n 20 --no-pager; exit 1; }
done'

echo "==> 5/7 dialplan extension 1999 (idempotent, with backup)"
ssh "$TARGET" '
EXT=$(sudo docker inspect voice-conference-asterisk-1 --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep "extensions.conf" | cut -d" " -f1)
sudo test -f "$EXT" || { echo "extensions.conf not found"; exit 1; }
if sudo grep -q "exten => 1999" "$EXT"; then
  echo "1999 already present"
else
  sudo cp "$EXT" "$EXT.backup-selfmonitor-$(date +%Y%m%dT%H%M%SZ)"
  sudo python3 - "$EXT" << "PYEOF"
import sys
path = sys.argv[1]
text = open(path).read()
anchor = "exten => _X.,1,Hangup(1)"
route = """; Live browser audio mirror (voice-selfmonitor.service).
exten => 1999,1,Stasis(selfmonitor)
 same => n,Hangup()

"""
assert anchor in text, "dialplan anchor missing"
assert "[phoneguy-sip]" in text, "phoneguy-sip context missing"
text = text.replace(anchor, route + anchor, 1)
open(path, "w").write(text)
print("1999 inserted")
PYEOF
  sudo docker exec voice-conference-asterisk-1 asterisk -rx "dialplan reload" | head -1
fi
sudo docker exec voice-conference-asterisk-1 asterisk -rx "dialplan show 1999@phoneguy-sip" | head -3'

echo "==> 6/7 private Caddy route and live page"
ssh "$TARGET" "mkdir -p '$STAGE'"
scp -q "$ROOT/deploy/patch-live-mirror-caddy.py" "$TARGET:$STAGE/patch-live-mirror-caddy.py"
scp -q "$ROOT/web/live/index.html" "$ROOT/web/live/app.js" \
  "$ROOT/web/live/audio-worklet.js" "$TARGET:$STAGE/"
ssh "$TARGET" "sudo cp -a /etc/caddy/Caddyfile '$STAGE/Caddyfile.candidate' && sudo python3 '$STAGE/patch-live-mirror-caddy.py' '$STAGE/Caddyfile.candidate' && sudo caddy validate --config '$STAGE/Caddyfile.candidate' && sudo install -m 0644 '$STAGE/Caddyfile.candidate' /etc/caddy/Caddyfile && { sudo systemctl reload caddy || { sudo cp -a '/opt/voice-selfmonitor/backups/$STAMP/Caddyfile' /etc/caddy/Caddyfile; sudo systemctl reload caddy; exit 1; }; } && sudo install -m 0644 '$STAGE/index.html' /opt/voice-changer/web/live/index.html && sudo install -m 0644 '$STAGE/app.js' /opt/voice-changer/web/live/app.js && sudo install -m 0644 '$STAGE/audio-worklet.js' /opt/voice-changer/web/live/audio-worklet.js"

echo "==> 7/7 verify relay and ARI application registered"
ssh "$TARGET" '
sudo docker exec voice-conference-asterisk-1 asterisk -rx "ari show apps" | grep -q selfmonitor && echo "ARI app registered"
curl -fsS -m 3 http://127.0.0.1:8096/healthz; echo
curl -fsS -m 3 https://vm-voice-1.lan.awesomeio.ru/live/ | grep -q "app.js?v=2"
echo "DONE: dial 1999, then start /live/"'

echo "backup: /opt/voice-selfmonitor/backups/$STAMP"
