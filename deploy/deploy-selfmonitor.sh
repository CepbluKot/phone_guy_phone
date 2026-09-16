#!/usr/bin/env bash
# Deploy the self-monitor echo line (dial 1999 -> hear yourself as Phone Guy).
# Scoped: touches only /opt/voice-selfmonitor, /etc/voice-selfmonitor.env,
# the voice-selfmonitor.service unit, and adds one extension to the ACTIVE
# conference runtime extensions.conf (with backup + dialplan reload).
# Never restarts asterisk/voice-rvc/conference containers.
set -euo pipefail

TARGET=${TARGET:-ubuntu@192.168.20.70}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "==> 1/6 sync code"
ssh "$TARGET" 'mkdir -p /opt/voice-selfmonitor/app/selfmonitor /opt/voice-selfmonitor/app/conference'
scp -q "$ROOT/selfmonitor/"*.py "$TARGET:/opt/voice-selfmonitor/app/selfmonitor/"
scp -q "$ROOT/conference/__init__.py" "$ROOT/conference/media.py" "$ROOT/conference/rvc.py" \
  "$TARGET:/opt/voice-selfmonitor/app/conference/"

echo "==> 2/6 python environment"
ssh "$TARGET" '
if [ ! -x /opt/voice-selfmonitor/venv/bin/python ]; then
  python3 -m venv /opt/voice-selfmonitor/venv
  /opt/voice-selfmonitor/venv/bin/pip -q install httpx websockets
fi
/opt/voice-selfmonitor/venv/bin/python -c "import httpx, websockets; print(\"deps ok\")"'

echo "==> 3/6 credentials from the active conference runtime"
ssh "$TARGET" '
ARI_CONF=$(sudo docker inspect voice-conference-asterisk-1 --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep "ari.conf" | cut -d" " -f1)
sudo test -f "$ARI_CONF" || { echo "ari.conf not found"; exit 1; }
PASSWORD=$(sudo grep -oP "^password=\K.*" "$ARI_CONF" | head -1)
[ -n "$PASSWORD" ] || { echo "password missing in $ARI_CONF"; exit 1; }
printf "SELFMONITOR_ARI_URL=http://127.0.0.1:8092/ari\nSELFMONITOR_ARI_USERNAME=phoneguy\nSELFMONITOR_ARI_PASSWORD=%s\n" "$PASSWORD" \
  | sudo tee /etc/voice-selfmonitor.env > /dev/null
sudo chmod 600 /etc/voice-selfmonitor.env
echo "env written (password length ${#PASSWORD})"'

echo "==> 4/6 systemd unit"
scp -q "$ROOT/deploy/voice-selfmonitor.service" "$TARGET:/tmp/voice-selfmonitor.service"
ssh "$TARGET" '
sudo mv /tmp/voice-selfmonitor.service /etc/systemd/system/voice-selfmonitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now voice-selfmonitor.service
for i in $(seq 1 20); do
  sleep 1
  STATUS=$(curl -fsS -m 2 http://127.0.0.1:8096/healthz 2>/dev/null || true)
  [ "$STATUS" = "{\"status\":\"ready\"}" ] && { echo "service ready: $STATUS"; break; }
  [ "$i" = 20 ] && { echo "health timeout"; sudo journalctl -u voice-selfmonitor -n 20 --no-pager; exit 1; }
done'

echo "==> 5/6 dialplan extension 1999 (idempotent, with backup)"
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
route = """; Live self-monitor: hear your own voice as Phone Guy (voice-selfmonitor.service).
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

echo "==> 6/6 verify ARI application registered"
ssh "$TARGET" '
PASSWORD=$(sudo grep -oP "^SELFMONITOR_ARI_PASSWORD=\K.*" /etc/voice-selfmonitor.env)
sudo docker exec voice-conference-asterisk-1 asterisk -rx "ari show apps" | grep -q selfmonitor && echo "ARI app registered"
curl -fsS -m 3 http://127.0.0.1:8096/healthz; echo
echo "DONE: dial 1999 from any registered phone"'

echo "rollback:"
echo "  ssh $TARGET \"sudo systemctl disable --now voice-selfmonitor && sudo rm /etc/voice-selfmonitor.env\""
echo "  restore extensions.conf from its .backup-selfmonitor-* copy + asterisk -rx 'dialplan reload'"
