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
