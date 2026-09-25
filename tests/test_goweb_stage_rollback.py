from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).parents[1]
ROLLBACK = ROOT / "deploy" / "rollback-goweb-stage.sh"
STAMP = "20260925T070000Z"


def make_stage(tmp_path):
    root = tmp_path / "remote"
    stage = root / "opt" / "voice-go" / "staging" / STAMP
    stage.mkdir(parents=True)
    (stage / ".voice-go-stage-stamp").write_text(STAMP)
    (stage / "compose.yaml").write_text("services: {}\n")
    (stage / ".env").write_text("VOICE_GO_IMAGE=voice-go:candidate\n")
    return root, stage


def make_docker_stub(tmp_path):
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "if [ \"$1\" = compose ] && [ \"${MOCK_DOCKER_DOWN_FAIL:-0}\" = 1 ]; then exit 9; fi\n"
        "if [ \"$1\" = ps ] && [ \"${MOCK_DOCKER_REMAINS:-0}\" = 1 ]; then echo candidate-id; fi\n"
    )
    docker.chmod(0o755)
    return log, docker


def run_rollback(tmp_path, root, docker, **overrides):
    env = os.environ | {
        "DEPLOY_GOWEB_ROOT_PREFIX": str(root),
        "PATH": f"{docker.parent}:{os.environ['PATH']}",
    } | overrides
    return subprocess.run(
        ["bash", str(ROLLBACK), STAMP],
        env=env,
        text=True,
        capture_output=True,
    )


def test_stage_rollback_removes_only_marked_candidate_after_compose_stops_it(tmp_path):
    root, stage = make_stage(tmp_path)
    log, docker = make_docker_stub(tmp_path)

    result = run_rollback(tmp_path, root, docker)

    assert result.returncode == 0, result.stderr
    assert "STAGE_ROLLBACK_COMPLETE" in result.stdout
    assert not stage.exists()
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert calls[0].startswith(f"compose -p voice-go-stage-{STAMP.lower()}")
    assert calls[0].endswith("down --remove-orphans")
    assert calls[1] == f"ps -aq --filter label=com.docker.compose.project=voice-go-stage-{STAMP.lower()}"


def test_stage_rollback_preserves_release_when_compose_down_fails(tmp_path):
    root, stage = make_stage(tmp_path)
    _, docker = make_docker_stub(tmp_path)

    result = run_rollback(tmp_path, root, docker, MOCK_DOCKER_DOWN_FAIL="1")

    assert result.returncode != 0
    assert stage.exists()


def test_stage_rollback_preserves_release_when_candidate_container_remains(tmp_path):
    root, stage = make_stage(tmp_path)
    _, docker = make_docker_stub(tmp_path)

    result = run_rollback(tmp_path, root, docker, MOCK_DOCKER_REMAINS="1")

    assert result.returncode != 0
    assert "containers remain" in result.stderr
    assert stage.exists()


def test_stage_rollback_rejects_unmarked_release_without_running_docker(tmp_path):
    root, stage = make_stage(tmp_path)
    (stage / ".voice-go-stage-stamp").unlink()
    log, docker = make_docker_stub(tmp_path)

    result = run_rollback(tmp_path, root, docker)

    assert result.returncode != 0
    assert not log.exists()
    assert stage.exists()


def test_stage_compose_has_only_loopback_listeners_and_separate_state():
    compose = (ROOT / "deploy" / "compose.goweb.stage.yaml").read_text()

    assert "network_mode: host" in compose
    assert "127.0.0.1:8081" in compose
    assert "127.0.0.1:8196" in compose
    assert "127.0.0.1:8197" in compose
    assert "/var/lib/voice-go-stage/voice-routing.json" in compose
    assert "selfmonitor-go-candidate" in compose
    assert 'restart: "no"' in compose
    assert "VOICE_ADMIN_ORIGIN: https://voice.lan.awesomeio.ru,https://vm-voice-1.lan.awesomeio.ru" in compose


def test_production_compose_uses_the_existing_ui_origin_for_admin_login():
    compose = (ROOT / "deploy" / "compose.goweb.yaml").read_text()

    assert "VOICE_ADMIN_ORIGIN: https://voice.lan.awesomeio.ru,https://vm-voice-1.lan.awesomeio.ru" in compose
    assert "VOICE_WEB_ADDR: 192.168.20.70:8080" in compose


def test_go_caddy_template_preserves_the_live_call_page_route():
    caddy = (ROOT / "deploy" / "Caddyfile.goweb").read_text()

    assert "redir /call /call/ 308" in caddy
    assert "handle_path /call/*" in caddy
    assert "root * /opt/voice-changer/web/call" in caddy
