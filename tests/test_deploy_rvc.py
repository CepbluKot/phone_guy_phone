import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "deploy-rvc.sh"
STAMP = "20260906T120000Z"


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
        "DEPLOY_RVC_STAMP": STAMP,
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
    assert "--remote-rollback" in commands
    assert STAMP in commands


def test_rejects_an_unsafe_release_stamp_before_remote_commands(tmp_path):
    env, log = fake_commands(tmp_path, curl_succeeds=True)
    env["DEPLOY_RVC_STAMP"] = "../../wrong"

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "release stamp" in result.stderr
    assert not log.exists() or not log.read_text()


def test_rejects_a_syntactically_valid_unapproved_target_before_remote_commands(tmp_path):
    env, log = fake_commands(tmp_path, curl_succeeds=True)
    env["DEPLOY_RVC_TARGET"] = "ubuntu@192.168.20.71"

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "approved VM209" in result.stderr
    assert not log.exists() or not log.read_text()


def test_existing_read_only_venv_is_verified_without_writes(tmp_path):
    lock = tmp_path / "requirements.lock"
    installed = subprocess.check_output(
        [sys.executable, "-c", "from importlib.metadata import version; print(version('numpy'))"],
        text=True,
    ).strip()
    lock.write_text(f"numpy=={installed}\n")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    tmp_path.chmod(0o555)
    try:
        result = subprocess.run(
            [SCRIPT, "--verify-venv", str(lock), sys.executable],
            cwd=ROOT, text=True, capture_output=True,
        )
    finally:
        tmp_path.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def make_rollback_fixture(tmp_path, fault=""):
    root = tmp_path / "root"
    backup = root / "opt/voice-rvc/backups" / STAMP
    web = root / "opt/voice-changer/web"
    deploy = root / "opt/voice-changer/deploy"
    caddy = root / "etc/caddy"
    unit_dir = root / "etc/systemd/system"
    state = tmp_path / "state"
    binary = tmp_path / "bin"
    for path in (backup, web, deploy, caddy, unit_dir, state, binary):
        path.mkdir(parents=True, exist_ok=True)

    (web / "index.html").write_text("new UI")
    (web / "new-only.js").write_text("must disappear")
    old_web = tmp_path / "old-web/web"
    old_web.mkdir(parents=True)
    (old_web / "index.html").write_text("old UI")
    with tarfile.open(backup / "web.tar.gz", "w:gz") as archive:
        archive.add(old_web, arcname="web")
    (root / "opt/voice-changer/.dockerignore").write_text("new ignore")
    (deploy / "Caddyfile").write_text("new source caddy")
    (caddy / "Caddyfile").write_text("new live caddy")
    (unit_dir / "voice-rvc.service").write_text("new unit")
    current = root / "opt/voice-rvc/current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to("/opt/voice-rvc/releases/new")

    (backup / ".dockerignore").write_text("old ignore")
    (backup / "source-Caddyfile").write_text("old source caddy")
    (backup / "Caddyfile").write_text("old live caddy")
    (backup / "voice-rvc.service").write_text("old unit")
    (backup / "rvc-current-target").write_text("/opt/voice-rvc/releases/old\n")
    (backup / "rvc-enabled").write_text("disabled\n")
    (backup / "rvc-active").write_text("active\n")
    (backup / "prior-image-id").write_text("sha256:old\n")
    (state / "image").write_text("sha256:new\n")
    (state / "active").write_text("active\n")
    (state / "enabled").write_text("enabled\n")

    programs = {
        "docker": """#!/bin/sh
if [ \"$FAULT\" = docker ]; then exit 1; fi
if [ \"$1 $2 $3\" = \"image inspect voice-changer:rollback-$STAMP\" ]; then exit 0; fi
if [ \"$1 $2 $3\" = \"image inspect voice-changer:current\" ]; then cat \"$STATE/image\"; exit 0; fi
if [ \"$1\" = tag ]; then printf '%s\\n' sha256:old > \"$STATE/image\"; exit 0; fi
if [ \"$1\" = compose ]; then [ \"$FAULT\" != compose ]; exit; fi
if [ \"$1\" = logs ]; then echo bounded-container-log; exit 0; fi
exit 0
""",
        "systemctl": """#!/bin/sh
case \"$1\" in
  is-active) cat \"$STATE/active\" ;;
  is-enabled) cat \"$STATE/enabled\" ;;
  restart) [ \"$FAULT\" != restart ] || exit 1; printf '%s\\n' active > \"$STATE/active\" ;;
  stop) printf '%s\\n' inactive > \"$STATE/active\" ;;
  enable) printf '%s\\n' enabled > \"$STATE/enabled\" ;;
  disable) printf '%s\\n' disabled > \"$STATE/enabled\" ;;
esac
""",
        "caddy": "#!/bin/sh\n[ \"$FAULT\" != caddy ]\n",
        "curl": "#!/bin/sh\n[ \"$FAULT\" != health ] && printf '%s\\n' '{\"status\":\"ok\"}'\n",
        "journalctl": "#!/bin/sh\necho bounded-service-log\n",
    }
    for name, body in programs.items():
        path = binary / name
        path.write_text(body)
        path.chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DEPLOY_RVC_ROOT_PREFIX": str(root),
        "STATE": str(state),
        "FAULT": fault,
        "STAMP": STAMP,
    }
    return root, state, env


def test_remote_rollback_body_restores_exact_trees_and_prior_states(tmp_path):
    root, state, env = make_rollback_fixture(tmp_path)

    result = subprocess.run(
        [SCRIPT, "--remote-rollback", STAMP], cwd=ROOT, env=env,
        text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (root / "opt/voice-changer/web/index.html").read_text() == "old UI"
    assert not (root / "opt/voice-changer/web/new-only.js").exists()
    assert (root / "opt/voice-changer/.dockerignore").read_text() == "old ignore"
    assert (root / "opt/voice-changer/deploy/Caddyfile").read_text() == "old source caddy"
    assert (root / "etc/caddy/Caddyfile").read_text() == "old live caddy"
    assert (root / "etc/systemd/system/voice-rvc.service").read_text() == "old unit"
    assert os.readlink(root / "opt/voice-rvc/current") == "/opt/voice-rvc/releases/old"
    assert (state / "image").read_text().strip() == "sha256:old"
    assert (state / "enabled").read_text().strip() == "disabled"
    assert (state / "active").read_text().strip() == "active"
    assert (root / f"opt/voice-rvc/backups/{STAMP}/voice-rvc-failure.log").exists()
    assert "ROLLBACK_COMPLETE" in result.stdout


@pytest.mark.parametrize("fault", ["compose", "restart", "caddy"])
def test_remote_rollback_body_reports_any_restore_or_validation_fault(tmp_path, fault):
    _, _, env = make_rollback_fixture(tmp_path, fault=fault)

    result = subprocess.run(
        [SCRIPT, "--remote-rollback", STAMP], cwd=ROOT, env=env,
        text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert "ROLLBACK_FAILED" in result.stderr


def test_manual_stamp_rollback_reports_ssh_failure_distinctly(tmp_path):
    env, _ = fake_commands(tmp_path, curl_succeeds=True)
    ssh = Path(env["PATH"].split(":", 1)[0]) / "ssh"
    ssh.write_text("#!/bin/sh\nexit 1\n")
    ssh.chmod(0o755)

    result = subprocess.run(
        [SCRIPT, "rollback", STAMP], cwd=ROOT, env=env,
        text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert "ROLLBACK FAILED" in result.stderr
