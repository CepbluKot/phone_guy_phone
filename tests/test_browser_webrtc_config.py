from pathlib import Path


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
