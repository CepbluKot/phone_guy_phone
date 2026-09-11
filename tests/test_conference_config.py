import re
from pathlib import Path


ROOT = Path("conference/asterisk")


def read(name: str) -> str:
    return (ROOT / name).read_text()


def test_conference_configuration_is_private_and_wideband():
    assert "internal_sample_rate=48000" in read("confbridge.conf")
    assert "autoload=no" in read("modules.conf")
    assert "chan_pjsip" in read("modules.conf")
    assert "ConfBridge(phoneguy-demo" in read("extensions.conf")


def test_http_and_ari_are_local_only_and_use_a_deployment_secret():
    http = read("http.conf")
    ari_template = read("ari.conf.template")

    assert "bindaddr=127.0.0.1" in http
    assert "bindport=8092" in http
    assert "tlsenable=no" in http
    assert "enabled=yes" in ari_template
    assert "password=__ARI_PASSWORD__" in ari_template
    assert not (ROOT / "ari.conf").exists()
    assert "ari.conf" in read(".gitignore").splitlines()


def test_module_allowlist_is_exact_and_contains_release_dependencies():
    modules = read("modules.conf")
    loaded = set(re.findall(r"^load\s*=>\s*([a-z0-9_]+)\.so$", modules, re.MULTILINE))

    assert loaded == {
        "app_confbridge",
        "app_dial",
        "app_stasis",
        "bridge_softmix",
        "chan_pjsip",
        "chan_websocket",
        "pbx_config",
        "res_ari",
        "res_ari_asterisk",
        "res_ari_bridges",
        "res_ari_channels",
        "res_ari_events",
        "res_ari_model",
        "res_http_websocket",
        "res_pjproject",
        "res_pjsip",
        "res_pjsip_authenticator_digest",
        "res_pjsip_endpoint_identifier_user",
        "res_pjsip_nat",
        "res_pjsip_pubsub",
        "res_pjsip_registrar",
        "res_pjsip_sdp_rtp",
        "res_pjsip_session",
        "res_rtp_asterisk",
        "res_sorcery_astdb",
        "res_sorcery_config",
        "res_sorcery_memory",
        "res_stasis",
        "res_stasis_answer",
        "res_stasis_playback",
        "res_stasis_recording",
        "res_stasis_snoop",
        "res_timing_timerfd",
        "res_websocket_client",
    }
    assert {
        "chan_pjsip", "res_pjproject", "res_pjsip", "res_pjsip_session",
        "res_pjsip_authenticator_digest", "res_pjsip_registrar",
        "res_pjsip_endpoint_identifier_user", "res_pjsip_nat",
        "res_pjsip_pubsub", "res_pjsip_sdp_rtp", "res_rtp_asterisk", "res_sorcery_astdb",
        "res_sorcery_memory",
    } <= loaded
    assert "cdr_" not in modules.lower()
    assert "cel_" not in modules.lower()
    assert "res_ari_recordings" not in modules


def test_ari_events_is_built_allowlisted_and_health_checked():
    """ARI event WebSocket support must survive the source-build deployment path."""
    dockerfile = read("Dockerfile")
    modules = read("modules.conf")
    healthcheck = read("healthcheck.sh")

    assert "--enable res_ari_events" in dockerfile
    assert "load => res_ari_events.so" in modules
    assert re.search(r"^    res_ari_events \\$", healthcheck, re.MULTILINE)


def test_ari_channel_creation_builds_allowlists_and_health_checks_app_stasis():
    """ARI /channels/create?app= needs the registered Stasis dialplan app."""
    dockerfile = read("Dockerfile")
    modules = read("modules.conf")
    healthcheck = read("healthcheck.sh")

    assert "--enable app_stasis" in dockerfile
    assert "load => app_stasis.so" in modules
    assert re.search(r"^    app_stasis \\$", healthcheck, re.MULTILINE)


def test_ari_bridge_api_is_built_allowlisted_and_health_checked():
    dockerfile = read("Dockerfile")
    modules = read("modules.conf")
    healthcheck = read("healthcheck.sh")

    assert "--enable res_ari_bridges" in dockerfile
    assert "load => res_ari_bridges.so" in modules
    assert re.search(r"^    res_ari_bridges \\$", healthcheck, re.MULTILINE)


def test_dialplan_profile_and_accounting_are_bounded_and_non_recording():
    extensions = read("extensions.conf")
    confbridge = read("confbridge.conf")

    assert "[phoneguy]" in extensions
    assert "exten => demo,1,Answer()" in extensions
    assert "ConfBridge(phoneguy-demo,phoneguy_bridge,phoneguy_user)" in extensions
    assert "same => n,Hangup()" in extensions
    assert "mixing_interval=20" in confbridge
    assert "max_members=4" in confbridge
    assert "quiet=yes" in confbridge
    assert "record_conference=no" in confbridge
    assert "enable=no" in read("cdr.conf")
    assert "enable=no" in read("cel.conf")


def test_pjsip_template_has_only_runtime_password_placeholders_and_private_media():
    pjsip = read("pjsip.conf.template")

    for extension in ("1983", "1987", "2014"):
        assert f"[{extension}]" in pjsip
        assert f"__SIP_{extension}_PASSWORD__" in pjsip
    assert "direct_media=no" in pjsip
    assert "allow=alaw" in pjsip
    assert "rtp_timeout=30" in pjsip
    assert "password=" not in pjsip.replace("password=__SIP_1983_PASSWORD__", "").replace(
        "password=__SIP_1987_PASSWORD__", "").replace("password=__SIP_2014_PASSWORD__", "")
    rtp = read("rtp.conf")
    assert "rtpstart=10000" in rtp
    assert "rtpend=10019" in rtp


def test_sip_dialplan_enters_the_dedicated_stasis_app_and_rejects_unknown_numbers():
    extensions = read("extensions.conf")

    assert "[phoneguy-sip]" in extensions
    assert "exten => 600,1,Stasis(phoneguy-sip)" in extensions
    assert "same => n,Hangup()" in extensions
    assert "exten => _X.,1,Hangup(1)" in extensions


def test_source_build_is_reproducible_non_root_and_health_checked():
    dockerfile = read("Dockerfile")

    assert "--with-pjproject-bundled" in dockerfile
    assert "libpjproject-dev" not in dockerfile
    assert "asterisk-22.11.0.tar.gz" in dockerfile
    assert "3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94" in dockerfile
    assert re.search(r"^FROM debian:bookworm-slim@sha256:[0-9a-f]{64}", dockerfile, re.MULTILINE)
    assert "sha256sum -c" in dockerfile
    assert "make -j1" in dockerfile
    assert "rm -rf /opt/asterisk-root/var/run" in dockerfile
    assert "make samples" not in dockerfile
    assert "CORE-SOUNDS" not in dockerfile
    assert "USER asterisk:asterisk" in dockerfile
    assert 'HEALTHCHECK' in dockerfile
    assert "exec asterisk -f" in dockerfile


def test_stasis_has_an_explicit_supported_taskpool_config():
    """Asterisk 22 needs explicit supported bounds for the Stasis task pool."""
    stasis = read("stasis.conf")
    dockerfile = read("Dockerfile")

    assert "[taskpool]" in stasis
    assert "minimum_size" not in stasis
    assert "initial_size=1" in stasis
    assert "max_size=4" in stasis
    assert "[declined_message_types]" not in stasis
    assert "stasis.conf" in dockerfile


def test_astdb_directory_is_created_by_the_non_root_asterisk_process():
    dockerfile = read("Dockerfile")
    asterisk = read("asterisk.conf")

    assert "astdbdir => /var/run/asterisk/astdb" in asterisk
    assert "USER asterisk:asterisk" in dockerfile
    assert "mkdir -p /var/run/asterisk/astdb" in dockerfile


def test_healthcheck_requires_every_allowlisted_module():
    modules = set(
        re.findall(r"^load\s*=>\s*([a-z0-9_]+)\.so$", read("modules.conf"), re.MULTILINE)
    )
    healthcheck = read("healthcheck.sh")

    checked = set(re.findall(r"^    ([a-z0-9_]+) \\$", healthcheck, re.MULTILINE))
    checked.add(re.search(r"^    ([a-z0-9_]+)$", healthcheck, re.MULTILINE).group(1))
    assert checked == modules
