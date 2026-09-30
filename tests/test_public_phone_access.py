import importlib.util
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def test_public_phone_origin_is_loopback_and_path_allowlisted():
    caddy = (ROOT / "deploy/Caddyfile.goweb").read_text()

    assert "http://127.0.0.1:8181" in caddy
    assert "path /phone /phone/* /admin/assets/* /ws/phone-signaling" in caddy
    assert "reverse_proxy 192.168.20.70:8080" in caddy
    assert "respond 404" in caddy
    assert "VOICE_PHONE_PUBLIC_ORIGIN" in (ROOT / "deploy/compose.goweb.yaml").read_text()

    block = caddy.split("http://127.0.0.1:8181", maxsplit=1)[1].split("\n}", maxsplit=1)[0]
    assert "/admin/api/" not in block
    assert "/healthz" not in block
    assert "bind 0.0.0.0" not in block


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
