from pathlib import Path
import hashlib
import os
import subprocess


ROOT = Path(__file__).parents[1]
ROLLBACK = ROOT / "deploy" / "rollback-goweb-production.sh"
STAMP = "20260925T080000Z"


def fixture(tmp_path):
    root = tmp_path / "remote"
    backup = root / "opt" / "voice-go" / "backups" / STAMP
    backup.mkdir(parents=True)
    go_release = root / "opt" / "voice-go" / "releases" / STAMP
    (go_release / "deploy").mkdir(parents=True)
    (go_release / "deploy" / "compose.goweb.yaml").write_text("services: {}\n")
    (go_release / ".env").write_text("VOICE_GO_IMAGE=voice-go:test\n")
    conference_release = "20260915T211605Z"
    conference = root / "opt" / "voice-conference" / "releases" / conference_release
    (conference / "deploy").mkdir(parents=True)
    (conference / "deploy" / "compose.conference.yaml").write_text("services: {}\n")
    (conference / ".env").write_text("CONFERENCE_TAG=test\n")

    files = {
        "Caddyfile": (root / "etc" / "caddy" / "Caddyfile", "prior live caddy\n"),
        "source-Caddyfile": (root / "opt" / "voice-changer" / "deploy" / "Caddyfile", "prior source caddy\n"),
        "selfmonitor.service": (root / "etc" / "systemd" / "system" / "voice-selfmonitor.service", "prior unit\n"),
        "selfmonitor.env": (root / "etc" / "voice-selfmonitor.env", "prior secret settings\n"),
        "voice-routing.json": (root / "etc" / "voice-changer" / "voice-routing.json", '{"schemaVersion":1,"revision":1,"extensions":{}}\n'),
        "admin-password": (root / "etc" / "voice-changer-admin" / "password", "private-admin-password"),
    }
    for name, (destination, content) in files.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        (backup / name).write_text(content)
    for name, mode in (("Caddyfile", 0o644), ("source-Caddyfile", 0o664), ("selfmonitor.service", 0o664),
                       ("selfmonitor.env", 0o600), ("voice-routing.json", 0o640), ("admin-password", 0o400)):
        (backup / name).chmod(mode)
        files[name][0].chmod(0o600)

    for name, content in (("asterisk-image-id", "sha256:prior-asterisk"), ("controller-image-id", "sha256:prior-controller")):
        (backup / name).write_text(content + "\n")
    (backup / "route-config-sha256").write_text(hashlib.sha256(files["voice-routing.json"][1].encode()).hexdigest() + "\n")
    (backup / "admin-password-sha256").write_text(hashlib.sha256(files["admin-password"][1].encode()).hexdigest() + "\n")
    (backup / "manifest").write_text(
        f"stamp={STAMP}\n"
        f"conference_release={conference_release}\n"
        "selfmonitor_was_active=active\n"
        "selfmonitor_was_enabled=enabled\n"
        "http_container_was_running=stopped\n"
        "phonebook_was_present=false\n"
    )
    (backup / "phonebook-absent").write_text("")
    (backup / "go.env").write_text("VOICE_GO_IMAGE=voice-go:prior\n")
    owner = root / "opt" / "voice-changer" / ".go-runtime-owner"
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text(STAMP + "\n")
    return root, backup, files, owner


def fake_commands(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.log"
    commands = {
        "docker": (
            "#!/bin/sh\n"
            f"printf 'docker %s\\n' \"$*\" >> '{log}'\n"
            "case \"$1:$2\" in\n"
            "  ps:*) [ \"${MOCK_GO_REMAINS:-0}\" = 1 ] && echo candidate-id ;;\n"
            "  inspect:voice-conference-asterisk-1) case \"$*\" in *Health.Status*) echo healthy;; *Image*) echo sha256:prior-asterisk;; esac ;;\n"
            "  inspect:voice-conference-controller-1) case \"$*\" in *Health.Status*) echo healthy;; *Image*) echo sha256:prior-controller;; esac ;;\n"
            "  compose:*) [ \"${MOCK_COMPOSE_FAIL:-0}\" != 1 ] ;;\n"
            "esac\n"
        ),
        "caddy": f"#!/bin/sh\nprintf 'caddy %s\\n' \"$*\" >> '{log}'\n",
        "systemctl": (
            f"#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >> '{log}'\n"
            "case \"$1\" in is-active|is-enabled) exit 0;; esac\n"
        ),
        "curl": (
            "#!/bin/sh\n"
            f"printf 'curl %s\\n' \"$*\" >> '{log}'\n"
            "case \"$*\" in *8096*) echo '{\"status\":\"ready\"}';; *8090*) echo '{\"status\":\"ready\"}';; esac\n"
        ),
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    for name, contents in commands.items():
        path = binary / name
        path.write_text(contents)
        path.chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DEPLOY_GOWEB_ROOT_PREFIX": str(tmp_path / "remote"),
        "DEPLOY_GOWEB_ROLLBACK_HEALTH_ATTEMPTS": "1",
        "DEPLOY_GOWEB_ROLLBACK_HEALTH_DELAY": "0",
    }
    return env, log


def run_rollback(tmp_path, *, env_overrides=None):
    env, log = fake_commands(tmp_path)
    env.update(env_overrides or {})
    result = subprocess.run(["bash", str(ROLLBACK), STAMP], env=env, text=True, capture_output=True)
    return result, log


def test_production_rollback_restores_snapshot_and_never_starts_legacy_http(tmp_path):
    root, backup, files, owner = fixture(tmp_path)
    for destination, _ in files.values():
        destination.write_text("candidate state\n")
    env, log = fake_commands(tmp_path)

    result = subprocess.run(["bash", str(ROLLBACK), STAMP], env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert "ROLLBACK_COMPLETE" in result.stdout
    assert not owner.exists()
    for name, (destination, original) in files.items():
        assert destination.read_text() == original
        assert destination.stat().st_mode & 0o777 == (backup / name).stat().st_mode & 0o777
    commands = log.read_text()
    assert "compose -p voice-go " in commands and " down --remove-orphans" in commands
    assert "compose -p voice-conference " in commands and " up -d --no-build --force-recreate" in commands
    assert "compose -p voice-changer " not in commands
    assert backup.exists()


def test_production_rollback_rejects_missing_or_invalid_snapshot_before_docker(tmp_path):
    root = tmp_path / "remote"
    result, log = run_rollback(tmp_path)

    assert result.returncode != 0
    assert "ROLLBACK_FAILED" in result.stderr
    assert not log.exists()


def test_production_rollback_keeps_owner_marker_when_compose_down_fails(tmp_path):
    _, _, _, owner = fixture(tmp_path)

    result, log = run_rollback(tmp_path, env_overrides={"MOCK_COMPOSE_FAIL": "1"})

    assert result.returncode != 0
    assert "ROLLBACK_FAILED" in result.stderr
    assert owner.exists()
    assert log.exists()


def test_production_rollback_keeps_owner_marker_if_go_container_remains(tmp_path):
    _, _, _, owner = fixture(tmp_path)

    result, _ = run_rollback(tmp_path, env_overrides={"MOCK_GO_REMAINS": "1"})

    assert result.returncode != 0
    assert owner.exists()
    assert "Go container remains" in result.stderr


def test_production_rollback_blocks_pre_cookie_release_while_public_cookie_route_is_enabled(tmp_path):
    root, backup, _, owner = fixture(tmp_path)
    # The candidate is cookie-aware; the rollback snapshot represents a Go
    # release predating cookie auth. Public edge auth has no Basic fallback.
    go_release = root / "opt" / "voice-go" / "releases" / STAMP
    (go_release / ".env").write_text(
        "VOICE_GO_IMAGE=voice-go:test\n"
        "VOICE_PHONE_PUBLIC_AUTH_HOST_FILE=/etc/voice-phone-auth/public-auth.json\n"
    )
    (root / "etc" / "voice-phone-auth").mkdir(parents=True)
    (root / "etc" / "voice-phone-auth" / "public-cookie-route.enabled").write_text("enabled\n")

    result, log = run_rollback(tmp_path)

    assert result.returncode != 0
    assert "ROLLBACK_BLOCKED" in result.stderr
    assert not log.exists()
    assert owner.exists()
