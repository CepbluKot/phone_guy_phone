import importlib.util
import json
import hashlib
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def test_public_phone_does_not_expose_legacy_teleport_listener():
    caddy = (ROOT / "deploy/Caddyfile.goweb").read_text()

    assert "8181" not in caddy
    assert "phone.awesomeio.ru" not in caddy
    assert "VOICE_PHONE_PUBLIC_ORIGIN" in (ROOT / "deploy/compose.goweb.yaml").read_text()
    compose = (ROOT / "deploy/compose.goweb.yaml").read_text()
    assert 'VOICE_PHONE_TURN_SECRET_FILE: /run/secrets/voice-phone-turn' in compose
    assert '/run/secrets/voice-phone-turn:ro' in compose
    assert 'VOICE_PHONE_TURN_SECRET_HOST_FILE=/etc/voice-phone/turn-shared-secret' in (ROOT / "deploy/update-goweb.sh").read_text()
    assert 'VOICE_PHONE_PUBLIC_AUTH_FILE: /run/secrets/voice-phone-public-auth' in compose
    assert '/run/secrets/voice-phone-public-auth:ro' in compose
    assert 'VOICE_PHONE_PUBLIC_AUTH_HOST_FILE=/etc/voice-phone-auth/public-auth.json' in (ROOT / "deploy/update-goweb.sh").read_text()
    assert 'configure-public-phone-auth.py' in (ROOT / "deploy/deploy-goweb.sh").read_text()
    assert 'passwordIterations = 600_000' in (ROOT / "internal/publicauth/auth.go").read_text()

def test_public_turn_is_authenticated_and_restricted_to_voice_rtp():
    compose = (ROOT / "deploy/compose.conference.yaml").read_text()
    turn_files = [
        path
        for path in (ROOT / "deploy").rglob("*turn*")
        if path.is_file() and path.suffix in {".conf", ".service", ".nft", ".py", ".template"}
    ]
    assert turn_files, "public phone requires a reviewed coturn deployment configuration"
    turn_config = "\n".join(path.read_text() for path in turn_files)

    assert "use-auth-secret" in turn_config
    assert "static-auth-secret" in turn_config
    assert "192.168.20.70" in turn_config
    assert "10000" in turn_config and "10019" in turn_config
    firewall = (ROOT / "deploy/coturn/turn-firewall.nft").read_text()
    assert "udp dport 10000-10019 accept" in firewall
    coturn = (ROOT / "deploy/coturn/turnserver.conf.template").read_text()
    assert "udp dport 10000-10019 accept" not in coturn
    assert "tcp dport 5349 accept" in firewall
    assert "udp dport 49160-49219 accept" in firewall
    assert "udp dport 49160-49219 dnat to 10.19.87.1" in firewall
    assert "udp sport 49160-49219 snat to 94.102.89.13" in firewall
    assert '"192.168.20.70:10000-10019:10000-10019/udp"' in compose
    assert '"0.0.0.0:10000-10019:10000-10019/udp"' not in compose


def test_turn_secret_renderer_keeps_secret_out_of_source_and_protects_output(tmp_path):
    module_path = ROOT / "deploy/coturn/voice-turn-render-config.py"
    spec = importlib.util.spec_from_file_location("voice_turn_render_config", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    secret_path = tmp_path / "secret"
    template_path = tmp_path / "template"
    output_path = tmp_path / "rendered" / "turnserver.conf"
    secret_path.write_text("a" * 40, encoding="ascii")
    secret_path.chmod(0o600)
    template_path.write_text("static-auth-secret=__VOICE_TURN_SHARED_SECRET__\n", encoding="ascii")

    module.render(secret_path, template_path, output_path)

    assert output_path.read_text(encoding="ascii") == "static-auth-secret=" + "a" * 40 + "\n"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o640
    assert "a" * 40 not in module_path.read_text(encoding="utf-8")


def test_public_phone_auth_provisioner_writes_only_salted_verifier(tmp_path):
    module_path = ROOT / "deploy/configure-public-phone-auth.py"
    spec = importlib.util.spec_from_file_location("configure_public_phone_auth", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ITERATIONS = 2

    password = b"private-test-password"
    config_path = tmp_path / "public-auth.json"
    module.create_config(config_path, password, os.getuid(), os.getgid())

    config = json.loads(config_path.read_text(encoding="utf-8"))
    salt = bytes.fromhex(config["passwordSalt"])
    assert config["username"] == "voice-owner"
    assert bytes.fromhex(config["passwordHash"]) == hashlib.pbkdf2_hmac("sha256", password, salt, 2, dklen=32)
    assert len(config["signingKey"]) >= 40
    assert password.decode() not in config_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o400
