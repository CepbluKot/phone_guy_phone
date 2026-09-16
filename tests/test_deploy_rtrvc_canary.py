from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_canary_deploy_is_transactional_and_never_restarts_production() -> None:
    script = (ROOT / "deploy/deploy-rtrvc-canary.sh").read_text()

    assert "set -Eeuo pipefail" in script
    assert "trap rollback ERR" in script
    assert "root=/opt/voice-rtrvc" in script
    assert 'release="$root/releases/$stamp"' in script
    assert 'backup="$root/backups/$stamp"' in script
    assert "cp -al /opt/voice-rvc/venv" in script
    assert "--target \"$fcpe_pkgs\"" in script
    assert "NUMBA_CACHE_DIR=/var/cache/voice-rtrvc/numba" in script
    assert "voice-rtrvc-canary.service" in script
    assert "restart voice-rvc.service" not in script
    assert "systemctl is-active --quiet voice-rvc.service" in script
    assert "ws://127.0.0.1:8093/ws/rvc" in script
    assert "wss://voice-claude.lan.awesomeio.ru/ws/rvc" in script


def test_canary_deploy_has_a_local_syntax_only_preflight() -> None:
    script = (ROOT / "deploy/deploy-rtrvc-canary.sh").read_text()

    assert 'if [[ "${1:-}" == "--check" ]]' in script
    assert "bash -n \"$0\"" in script
    assert 'VOICE_RTRVC_TEST_PYTHON:-$repo_root/.venv/bin/python' in script
