"""Scoped Caddy/dialplan deployment must preserve the running call contour."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_live_mirror_route_patch_preserves_call_and_is_idempotent(tmp_path):
    active = tmp_path / "Caddyfile"
    active.write_text("""https://vm-voice-1.lan.awesomeio.ru {
    handle /ws/conference {
        reverse_proxy 127.0.0.1:8091
    }
    handle /ws/call {
        reverse_proxy 127.0.0.1:8091
    }
    handle_path /call/* {
        root * /opt/voice-changer/web/call
        file_server
    }
}
""")
    script = ROOT / "deploy" / "patch-live-mirror-caddy.py"
    for _ in range(2):
        subprocess.run([sys.executable, script, active], check=True)
    result = active.read_text()
    assert result.count("handle /ws/live-mirror {") == 1
    assert "reverse_proxy 127.0.0.1:8097" in result
    assert "handle /ws/call {" in result
    assert "handle_path /call/* {" in result


def test_source_dialplan_keeps_1999_after_conference_deploy():
    source = (ROOT / "conference/asterisk/extensions.conf").read_text()
    assert "exten => 1999,1,Stasis(selfmonitor)" in source
    assert "exten => _X.,1,Hangup(1)" in source


def test_physical_phone_pjsip_patch_adds_keepalive_once_and_preserves_secret(tmp_path):
    active = tmp_path / "pjsip.conf"
    active.write_text("""[1983](phone-endpoint)
auth=1983-auth
aors=1983
rtp_timeout=0
[1983-auth]
type=auth
password=keep-me-secret
[1983]
type=aor
contact=sip:1983@192.168.20.134:5062
""")
    script = ROOT / "deploy" / "patch-physical-phone-pjsip.py"
    for _ in range(2):
        subprocess.run([sys.executable, script, active], check=True)
    result = active.read_text()
    endpoint = result.split("[1983-auth]", 1)[0]
    assert endpoint.count("rtp_keepalive=1") == 1
    assert "password=keep-me-secret" in result


def test_container_nat_patch_advertises_vm_address_without_touching_secrets(tmp_path):
    active = tmp_path / "pjsip.conf"
    active.write_text("""[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
local_net=192.168.20.0/24
local_net=10.19.87.0/24
[1983-auth]
type=auth
password=keep-me-secret
""")
    script = ROOT / "deploy" / "patch-asterisk-container-nat.py"
    for _ in range(2):
        subprocess.run([sys.executable, script, active], check=True)
    result = active.read_text()
    transport = result.split("[1983-auth]", 1)[0]
    assert "local_net=172.19.0.0/16" in transport
    assert "local_net=192.168.20.0/24" not in transport
    assert "local_net=10.19.87.0/24" not in transport
    assert transport.count("external_signaling_address=192.168.20.70") == 1
    assert transport.count("external_signaling_port=5060") == 1
    assert transport.count("external_media_address=192.168.20.70") == 1
    assert "password=keep-me-secret" in result


def test_selfmonitor_deploy_patches_container_nat_before_restarting_asterisk():
    deploy = (ROOT / "deploy" / "deploy-selfmonitor.sh").read_text()
    assert "patch-asterisk-container-nat.py" in deploy
    assert "core show channels concise" in deploy
    assert "docker restart voice-conference-asterisk-1" in deploy
    assert "docker restart voice-conference-controller-1" in deploy
    assert "phoneguy-sip" in deploy
    assert "pjsip show transport transport-udp" in deploy
