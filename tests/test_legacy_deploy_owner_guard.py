from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).parents[1]
OWNER_MARKER = "/opt/voice-changer/.go-runtime-owner"
LEGACY_SCRIPTS = (
    "deploy/deploy.sh",
    "deploy/deploy-rvc.sh",
    "deploy/deploy-conference.sh",
    "deploy/deploy-selfmonitor.sh",
)
SELFMONITOR_SCRIPT = ROOT / "deploy" / "deploy-selfmonitor.sh"


def test_every_legacy_deploy_refuses_vm209_after_go_takes_ownership():
    for relative_path in LEGACY_SCRIPTS:
        source = (ROOT / relative_path).read_text()
        assert OWNER_MARKER in source, f"{relative_path} has no Go ownership check"
        assert "legacy deployment refused" in source, f"{relative_path} has no clear refusal"


def test_service_specific_rollback_paths_refuse_to_restore_legacy_runtime():
    for relative_path in ("deploy/deploy-rvc.sh", "deploy/deploy-conference.sh"):
        source = (ROOT / relative_path).read_text()
        rollback = source.index("remote_rollback()")
        rollback_body = source[rollback:]
        assert OWNER_MARKER in rollback_body, f"{relative_path} rollback can overwrite Go"
        assert "legacy deployment refused" in rollback_body


def test_selfmonitor_deploy_rejects_non_vm209_target_before_ssh(tmp_path):
    log = tmp_path / "ssh.log"
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\n")
    ssh.chmod(0o755)

    result = subprocess.run(
        ["bash", SELFMONITOR_SCRIPT],
        env=os.environ | {"TARGET": "ubuntu@192.168.20.71", "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "approved VM209" in result.stderr
    assert not log.exists()
