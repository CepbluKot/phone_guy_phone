from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "deploy-goweb.sh"
ROLLBACK = ROOT / "deploy" / "rollback-goweb-production.sh"
STAMP = "20260925T081000Z"


def fake_ssh(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "ssh.log"
    stdin_file = tmp_path / "ssh.stdin"
    ssh = binary / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"cat > '{stdin_file}'\n"
    )
    ssh.chmod(0o755)
    env = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}
    return env, log, stdin_file


def test_production_target_guard_runs_before_any_remote_command(tmp_path):
    env, log, _ = fake_ssh(tmp_path)
    env["DEPLOY_GOWEB_TARGET"] = "ubuntu@192.168.20.71"

    result = subprocess.run([str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "must be ubuntu@192.168.20.70" in result.stderr
    assert not log.exists()


def test_manual_rollback_sends_exact_stamp_and_reviewed_helper_to_vm209(tmp_path):
    env, log, stdin_file = fake_ssh(tmp_path)

    result = subprocess.run([str(SCRIPT), "rollback", STAMP], cwd=ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert "ubuntu@192.168.20.70 sudo bash -s -- " + STAMP in log.read_text()
    assert stdin_file.read_text() == ROLLBACK.read_text()


def test_production_cutover_has_snapshot_rollback_and_health_gates_in_order():
    source = SCRIPT.read_text()

    snapshot = source.index('cp -a "$caddy_live" "$backup/Caddyfile"')
    trap = source.index('trap rollback_on_error EXIT', snapshot)
    stop_legacy = source.index('stop controller')
    start_asterisk = source.index('up -d --no-build --no-deps --force-recreate asterisk')
    start_go = source.index('docker compose -p voice-go ', start_asterisk)
    admin_smoke = source.index('admin smoke passed for both private UI origins')
    caddy_switch = source.index('cp "$release/deploy/Caddyfile.goweb" "$caddy_live"')
    owner_marker = source.index('printf \'%s\\n\' "$stamp" > "$voice_root/.go-runtime-owner"')

    assert snapshot < trap < stop_legacy < start_asterisk < start_go < admin_smoke < caddy_switch < owner_marker
    assert "0 active channels" in source
    assert "RVC is not ready and idle" in source
    assert "endpoint inventory changed" in source
    assert "private web address is not assigned" in source
    assert "VOICE_WEB_ADDR: 192.168.20.70:8080" in (ROOT / "deploy" / "compose.goweb.yaml").read_text()
    assert "voice-changer-voice-1" in source
    assert 'docker compose -p voice-changer' not in source


def test_production_caddy_keeps_call_page_and_moves_go_websocket_paths_to_go():
    caddy = (ROOT / "deploy" / "Caddyfile.goweb").read_text()

    assert "reverse_proxy 192.168.20.70:8080" in caddy
    assert "redir /call /call/ 308" in caddy
    assert "root * /opt/voice-changer/web/call" in caddy
    assert "reverse_proxy 127.0.0.1:8091" not in caddy
    assert "reverse_proxy 127.0.0.1:8097" not in caddy


def test_production_cutover_clones_protected_asterisk_runtime_into_release():
    source = SCRIPT.read_text()

    assert 'new_runtime="$release/runtime"' in source
    assert 'cp -a "$old_runtime/asterisk/$item" "$new_runtime/asterisk/"' in source
    assert 'cp -a "$old_runtime/asterisk/sounds" "$new_runtime/asterisk/"' in source
    assert 'cp "$release/conference/asterisk/extensions.conf" "$new_runtime/asterisk/extensions.conf"' in source
    assert '"$stamp" "$new_runtime" "$fixtures" "$stamp" "$new_runtime" "$fixtures"' in source


def test_admin_secret_readability_preflight_checks_the_container_numeric_uid():
    source = SCRIPT.read_text()

    assert "setpriv --reuid 10001 --regid 10001 --clear-groups test -r" in source
    assert "sudo -u '#10001'" not in source


def test_endpoint_preflight_accepts_asterisk_column_spacing():
    source = SCRIPT.read_text()

    assert "Endpoint:[[:space:]]+[0-9]+" in source


def test_release_staging_preserves_conference_asterisk_build_context_path():
    source = SCRIPT.read_text()

    assert "mkdir -m 0755 '$remote_stage/conference'" in source
    assert 'rsync -a --delete conference/asterisk "$target:$remote_stage/conference/"' in source
    assert 'rsync -a --delete deploy/compose.goweb.yaml' in source
    assert '"$target:$remote_stage/deploy/"' in source


def test_health_check_uses_the_private_address_bound_by_the_go_service():
    source = SCRIPT.read_text()

    assert "http://192.168.20.70:8080/healthz" in source
    assert "http://127.0.0.1:8080/healthz" not in source


def test_production_rollback_image_identity_condition_checks_both_images():
    source = ROLLBACK.read_text()

    assert 'asterisk-image-id")" ]' in source
    assert '|| [ "$(docker inspect voice-conference-controller-1' in source
