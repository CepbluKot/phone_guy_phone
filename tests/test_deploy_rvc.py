import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "deploy-rvc.sh"


def fake_commands(tmp_path, curl_succeeds):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.log"
    for name in ("ssh", "rsync", "curl"):
        program = binary / name
        exit_code = 0 if name != "curl" or curl_succeeds else 1
        program.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{name} '$* >> \"$COMMAND_LOG\"\n"
            "cat >> \"$COMMAND_LOG\"\n"
            f"exit {exit_code}\n"
        )
        program.chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DEPLOY_RVC_SKIP_CHECKS": "1",
        "DEPLOY_RVC_STAMP": "20260906T120000Z",
        "DEPLOY_RVC_HEALTH_ATTEMPTS": "1",
        "DEPLOY_RVC_HEALTH_DELAY": "0",
        "DEPLOY_RVC_LIVE_CLIENT": "/bin/true",
    }
    return env, log


def test_rollout_stages_only_scoped_inputs_and_avoids_network_mutation(tmp_path):
    env, log = fake_commands(tmp_path, curl_succeeds=True)

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "rvc_service" in commands
    assert "web" in commands
    assert "experiments" not in next(line for line in commands.splitlines() if line.startswith("rsync "))
    assert "netplan" not in commands
    assert "iptables" not in commands
    assert "ufw" not in commands
    assert "192.168.20.70:8080" not in commands
    assert "voice-changer:rollback-$stamp" in commands
    assert "20260906T120000Z" in commands


def test_failed_health_gate_executes_scoped_rollback(tmp_path):
    env, log = fake_commands(tmp_path, curl_succeeds=False)

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode != 0
    commands = log.read_text()
    rollback = [line for line in commands.splitlines() if "ROLLBACK_BEGIN" in line]
    assert len(rollback) == 1
    assert "voice-changer:rollback-$stamp" in rollback[0]
    assert "/etc/caddy/Caddyfile" in rollback[0]
    assert "/opt/voice-changer" in rollback[0]
    assert "/opt/voice-rvc/current" in rollback[0]


def test_rejects_an_unsafe_release_stamp_before_remote_commands(tmp_path):
    env, log = fake_commands(tmp_path, curl_succeeds=True)
    env["DEPLOY_RVC_STAMP"] = "../../wrong"

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "release stamp" in result.stderr
    assert not log.exists() or not log.read_text()
