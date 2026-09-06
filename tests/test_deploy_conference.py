"""Deployment boundary tests for the isolated Asterisk listening demo."""

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "deploy-conference.sh"
ASTERISK_HEALTHCHECK = ROOT / "conference" / "asterisk" / "healthcheck.sh"
STAMP = "20260906T180000Z"


def fake_commands(tmp_path, *, healthy=True):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.log"
    for name in ("ssh", "rsync", "curl"):
        code = 0 if name != "curl" or healthy else 1
        path = binary / name
        path.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{name} '$* >> \"$COMMAND_LOG\"\n"
            "cat >> \"$COMMAND_LOG\"\n"
            f"exit {code}\n"
        )
        path.chmod(0o755)
    return os.environ | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DEPLOY_CONFERENCE_SKIP_CHECKS": "1",
        "DEPLOY_CONFERENCE_STAMP": STAMP,
        "DEPLOY_CONFERENCE_HEALTH_ATTEMPTS": "1",
        "DEPLOY_CONFERENCE_HEALTH_DELAY": "0",
        "DEPLOY_CONFERENCE_LIVE_CLIENT": "/bin/true",
    }, log


def test_rejects_any_target_except_vm209_before_remote_commands(tmp_path):
    env, log = fake_commands(tmp_path)
    env["DEPLOY_CONFERENCE_TARGET"] = "ubuntu@192.168.20.71"

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "approved VM209" in result.stderr
    assert not log.exists() or not log.read_text()


def test_rollout_uploads_only_conference_and_http_sources_without_mutating_venv_or_network(tmp_path):
    env, log = fake_commands(tmp_path)

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "rsync " in commands
    assert "conference" in commands
    assert "app" in commands and "web" in commands
    assert "rvc_service" not in commands
    assert "experiments" not in commands
    for prohibited in ("ufw", "iptables", "nft", "netplan", "voice-rvc/venv", "VM208", "frigate"):
        assert prohibited not in commands


def test_failed_health_gate_runs_scoped_rollback_and_never_reports_success(tmp_path):
    env, log = fake_commands(tmp_path, healthy=False)

    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode != 0
    commands = log.read_text()
    assert "--remote-rollback" in commands
    assert STAMP in commands
    assert "DEPLOY_COMPLETE" not in result.stdout


def test_rollback_is_a_noop_when_remote_preflight_never_created_a_snapshot():
    source = SCRIPT.read_text()

    probe = source.index('ssh "$target" "test -d \'$backup\'"')
    no_snapshot = source.index('No conference snapshot was created')
    rollback = source.index('--remote-rollback "$stamp"')
    prepare = source.index("REMOTE_PREPARE'")
    protection = source.rindex("rollback_required=1", 0, prepare)
    assert probe < no_snapshot < rollback
    assert protection < prepare


def test_missing_rollback_snapshot_is_a_nonzero_failure_not_a_success_message(tmp_path):
    env, _ = fake_commands(tmp_path)
    env["DEPLOY_CONFERENCE_ROOT_PREFIX"] = str(tmp_path / "remote-root")

    result = subprocess.run(
        [SCRIPT, "--remote-rollback", STAMP], cwd=ROOT, env=env,
        text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert "ROLLBACK_FAILED" in result.stderr
    assert "ROLLBACK_COMPLETE" not in result.stdout


def test_rollback_checks_legacy_http_on_the_vm_published_address():
    """VM209 publishes the existing HTTP container on its LAN address, not loopback."""
    source = SCRIPT.read_text()

    assert "http://192.168.20.70:8080/healthz" in source
    assert "http://127.0.0.1:8080/healthz" not in source


def test_generated_root_owned_fixtures_are_readable_by_the_unprivileged_controller():
    """The controller image is UID 10001 while VM fixture generation is root-owned."""
    source = SCRIPT.read_text()

    generation = source.index('python3 -m conference.scenario "$fixtures"')
    fixture_mount = source.index('CONFERENCE_FIXTURES=$fixtures')
    assert generation < source.index('chmod 0755 "$fixtures"') < fixture_mount
    assert generation < source.index('chmod 0644 "$fixtures"/*.pcm') < fixture_mount


def test_asterisk_read_only_root_keeps_bundled_docs_and_moves_only_astdb_to_tmpfs():
    compose = (ROOT / "deploy" / "compose.conference.yaml").read_text()
    asterisk = (ROOT / "conference" / "asterisk" / "asterisk.conf").read_text()

    assert "read_only: true" in compose
    assert "/var/run/asterisk" in compose
    assert "/var/lib/asterisk" not in compose
    assert "/var/log/asterisk" in compose
    assert "astdbdir => /var/run/asterisk/astdb" in asterisk


def test_ari_events_preflight_must_pass_before_public_caddy_switch():
    """A missing ARI events module must roll back before /ws/conference is public."""
    source = SCRIPT.read_text()

    preflight = source.index('module show like res_ari_events')
    websocket = source.index('ARI_EVENTS_WEBSOCKET_OK')
    public_switch = source.index('cp "$candidate" /etc/caddy/Caddyfile')

    assert preflight < public_switch
    assert websocket < public_switch
    assert 'docker exec -i "voice-conference-controller-1" python -' in source
    assert 'additional_headers={"Authorization": "Basic " + token}' in source


def test_ari_events_module_preflight_accepts_its_running_row_regardless_of_total_modules():
    module_output = """\
Module                         Description                              Use Count  Status      Support Level
res_ari.so                     Asterisk RESTful API                    0          Running              core
res_ari_asterisk.so            Asterisk RESTful API module             0          Running              core
res_ari_channels.so            Asterisk RESTful API module             0          Running              core
res_ari_events.so              Asterisk RESTful API module             0          Running              core
res_ari_model.so               Asterisk RESTful API module             0          Running              core
5 modules loaded
"""

    result = subprocess.run(
        [SCRIPT, "--verify-ari-events-module"], cwd=ROOT, input=module_output,
        text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_ari_events_module_preflight_rejects_a_nonrunning_module_row():
    module_output = """\
Module                         Description                              Use Count  Status      Support Level
res_ari_events.so              Asterisk RESTful API module             0          Not Running          core
1 modules loaded
"""

    result = subprocess.run(
        [SCRIPT, "--verify-ari-events-module"], cwd=ROOT, input=module_output,
        text=True, capture_output=True,
    )

    assert result.returncode != 0


def test_asterisk_healthcheck_requires_the_exact_running_module_row_not_a_total_count():
    source = ASTERISK_HEALTHCHECK.read_text()

    assert 'grep -Fq "1 modules loaded"' not in source
    assert 'grep -Eq "^${required_module}\\.so[[:space:]].*[[:space:]][[:digit:]]+[[:space:]]+Running[[:space:]]"' in source


def test_rollback_waits_for_recreated_legacy_http_before_declaring_failure():
    source = SCRIPT.read_text()

    assert 'DEPLOY_CONFERENCE_ROLLBACK_HEALTH_ATTEMPTS:-20' in source
    assert 'DEPLOY_CONFERENCE_ROLLBACK_HEALTH_DELAY:-1' in source


def test_preflight_allows_only_the_idle_owned_conference_to_hold_its_ports():
    source = SCRIPT.read_text()

    assert 'conference_contour_is_idle()' in source
    assert 'voice-conference-asterisk-1' in source
    assert 'voice-conference-controller-1' in source
    assert '"status":"idle"' in source
