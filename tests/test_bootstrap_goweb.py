from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "bootstrap-goweb.sh"
PASSWORD = "a-secure-test-password-value"


def fake_ssh(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "ssh.log"
    received = tmp_path / "stdin"
    ssh = binary / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"cat > '{received}'\n"
    )
    ssh.chmod(0o755)
    return binary, log, received


def run_bootstrap(tmp_path, password_file, *, target=None):
    binary, log, received = fake_ssh(tmp_path)
    env = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}
    if target:
        env["DEPLOY_GOWEB_TARGET"] = target
    result = subprocess.run(
        ["bash", str(SCRIPT), str(password_file)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    return result, log, received


def make_password(tmp_path, value=PASSWORD):
    path = tmp_path / "admin-password"
    path.write_text(value)
    path.chmod(0o600)
    return path


def test_bootstrap_transfers_route_template_and_secret_only_via_stdin(tmp_path):
    password_file = make_password(tmp_path)

    result, log, received = run_bootstrap(tmp_path, password_file)

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 4
    assert "192.168.20.70" in calls[0]
    assert "/etc/voice-changer/voice-routing.json" in calls[2]
    assert "/etc/voice-changer-admin/password" in calls[3]
    assert "-o 10001 -g 10001 -m 0400" in calls[3]
    assert PASSWORD not in "\n".join(calls)
    assert received.read_text() == PASSWORD
    assert "GOWEB_BOOTSTRAP_FILES_READY" in result.stdout


def test_bootstrap_refuses_insecure_password_file_before_ssh(tmp_path):
    password_file = make_password(tmp_path)
    password_file.chmod(0o644)
    binary, log, _ = fake_ssh(tmp_path)
    env = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}

    result = subprocess.run(
        ["bash", str(SCRIPT), str(password_file)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "group or others" in result.stderr
    assert not log.exists()


def test_bootstrap_rejects_nonproduction_target_before_ssh(tmp_path):
    password_file = make_password(tmp_path)
    binary, log, _ = fake_ssh(tmp_path)
    env = os.environ | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DEPLOY_GOWEB_TARGET": "ubuntu@192.168.20.71",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), str(password_file)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "approved VM209" in result.stderr
    assert not log.exists()


def test_bootstrap_rejects_trailing_newline_and_symlinked_secret(tmp_path):
    password_file = make_password(tmp_path, PASSWORD + "\n")
    result, log, _ = run_bootstrap(tmp_path, password_file)
    assert result.returncode == 2
    assert "line breaks" in result.stderr
    assert not log.exists()

    link_dir = tmp_path / "symlink-case"
    link_dir.mkdir()
    link = link_dir / "password-link"
    link.symlink_to(password_file)
    result, log, _ = run_bootstrap(link_dir, link)
    assert result.returncode == 2
    assert "non-symlink" in result.stderr
    assert not log.exists()
