from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_websocket_transport_module_is_loaded():
    modules = (ROOT / "conference/asterisk/modules.conf").read_text()
    dockerfile = (ROOT / "conference/asterisk/Dockerfile").read_text()
    healthcheck = (ROOT / "conference/asterisk/healthcheck.sh").read_text()

    assert "load => res_pjsip_transport_websocket.so" in modules
    assert "load => res_crypto.so" in modules
    assert "--enable res_pjsip_transport_websocket" in dockerfile
    assert "--enable res_crypto" in dockerfile
    assert "    res_pjsip_transport_websocket \\" in healthcheck
    assert "    res_crypto \\" in healthcheck


def test_wss_transport_uses_http_websocket_server():
    pjsip = (ROOT / "conference/asterisk/pjsip.conf.template").read_text()

    assert "[transport-wss]" in pjsip
    assert "protocol=wss" in pjsip
    assert "bind=0.0.0.0" in pjsip
    assert "external_media_address=192.168.20.70" in pjsip
    assert "local_net=172.19.0.0/16" in pjsip


def test_goweb_update_replaces_existing_runtime_wss_transport(tmp_path):
    runtime = tmp_path / "pjsip.conf"
    runtime.write_text(
        "[transport-udp]\ntype=transport\nprotocol=udp\n\n"
        "[transport-wss]\ntype=transport\nprotocol=wss\nbind=0.0.0.0\n\n"
        "[phone-endpoint]\ntype=endpoint\n\n"
        "[1983]\ntype=endpoint\n\n"
        "[1987]\ntype=endpoint\n\n"
        "[1988]\ntype=endpoint\n\n"
        "[2014]\ntype=endpoint\n"
    )
    template = ROOT / "conference/asterisk/pjsip.conf.template"
    helper = ROOT / "deploy/update_pjsip_wss_transport.py"

    subprocess.run([sys.executable, str(helper), str(runtime), str(template)], check=True)
    updated = runtime.read_text()
    assert updated.count("[transport-wss]") == 1
    assert "[transport-wss]\ntype=transport\nprotocol=wss\nbind=0.0.0.0\nexternal_media_address=192.168.20.70\nlocal_net=172.19.0.0/16" in updated
    assert "symmetric_transport=yes" in updated
    assert "[phone-endpoint]\ntype=endpoint\nmedia_encryption=sdes" in updated
    for extension in ("1983", "1987", "1988", "2014"):
        assert f"[{extension}]\ntype=endpoint\n" in updated
        assert f"[{extension}]\ntype=endpoint\nmedia_encryption=sdes" not in updated

    subprocess.run([sys.executable, str(helper), str(runtime), str(template)], check=True)
    assert runtime.read_text() == updated


def test_all_goweb_deployment_paths_patch_runtime_wss_transport():
    for name in ("update-goweb.sh", "deploy-goweb.sh"):
        script = (ROOT / "deploy" / name).read_text()
        assert "update_pjsip_wss_transport.py" in script


def test_dynamic_pjsip_objects_use_memory_with_static_fallback():
    sorcery = (ROOT / "conference/asterisk/sorcery.conf").read_text()
    dockerfile = (ROOT / "conference/asterisk/Dockerfile").read_text()

    assert "endpoint=memory" in sorcery
    assert "endpoint=config,pjsip.conf,criteria=type=endpoint" in sorcery
    assert "auth=memory" in sorcery
    assert "auth=config,pjsip.conf,criteria=type=auth" in sorcery
    assert "aor=memory" in sorcery
    assert "aor=config,pjsip.conf,criteria=type=aor" in sorcery
    assert "global=config,pjsip.conf,criteria=type=global" in sorcery
    assert "transport=config,pjsip.conf,criteria=type=transport" in sorcery
    assert sorcery.index("endpoint=memory") < sorcery.index("endpoint=config,pjsip.conf")
    assert sorcery.index("auth=memory") < sorcery.index("auth=config,pjsip.conf")
    assert sorcery.index("aor=memory") < sorcery.index("aor=config,pjsip.conf")
    assert " sorcery.conf " in dockerfile and "/etc/asterisk/" in dockerfile


def test_ari_host_publication_stays_loopback():
    compose = (ROOT / "deploy/compose.conference.yaml").read_text()

    assert '"127.0.0.1:8092:8092"' in compose
    assert '"0.0.0.0:8092:8092"' not in compose
    assert '"8092:8092"' not in compose


def test_rtp_range_remains_private():
    compose = (ROOT / "deploy/compose.conference.yaml").read_text()

    assert '"192.168.20.70:10000-10019:10000-10019/udp"' in compose
    assert '"0.0.0.0:10000-10019:10000-10019/udp"' not in compose


def test_docker_ice_host_candidate_uses_private_vm_address():
    rtp = (ROOT / "conference/asterisk/rtp.conf").read_text()

    assert "[ice_host_candidates]" in rtp
    assert "172.19.0.2 => 192.168.20.70" in rtp
