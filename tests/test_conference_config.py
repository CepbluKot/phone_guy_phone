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
        "codec_alaw",
        "format_wav",
        "app_playback",
        "codec_resample",
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


def test_sip_audio_translators_are_built_allowlisted_and_health_checked():
    dockerfile = read("Dockerfile")
    modules = read("modules.conf")
    healthcheck = read("healthcheck.sh")

    for module in ("codec_alaw", "codec_resample"):
        assert f"--enable {module}" in dockerfile
        assert f"load => {module}.so" in modules
        assert re.search(rf"^    {module} \\$", healthcheck, re.MULTILINE)


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

    transport = re.search(r"^\[transport-udp\]$(.*?)(?=^\[)", pjsip,
                          re.MULTILINE | re.DOTALL).group(1)
    assert "local_net=172.19.0.0/16" in transport
    assert "local_net=192.168.20.0/24" not in transport
    assert "external_signaling_address=192.168.20.70" in transport
    assert "external_signaling_port=5060" in transport
    assert "external_media_address=192.168.20.70" in transport

    for extension in ("1983", "1987", "2014"):
        assert f"[{extension}]" in pjsip
        assert f"__SIP_{extension}_PASSWORD__" in pjsip
    assert "direct_media=no" in pjsip
    assert "allow=alaw" in pjsip
    assert "rtp_timeout=30" in pjsip
    physical_phone = re.search(r"^\[1983\]\(phone-endpoint\)$(.*?)(?=^\[)", pjsip,
                               re.MULTILINE | re.DOTALL).group(1)
    assert "rtp_timeout=0" in physical_phone
    assert "rtp_keepalive=1" in physical_phone
    physical_aor = re.search(r"^\[1983\]$\n(type=aor.*?)(?=^\[)", pjsip,
                            re.MULTILINE | re.DOTALL).group(1)
    assert "contact=sip:1983@192.168.20.134:5062" in physical_aor
    assert "qualify_frequency=30" in physical_aor
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


def test_browser_phone_microphone_route_is_private_native_websocket_only():
    caddy = Path("deploy/Caddyfile").read_text()
    compose = Path("deploy/compose.conference.yaml").read_text()

    assert "handle /ws/call" in caddy
    assert "reverse_proxy 127.0.0.1:8091" in caddy
    assert "handle_path /call/*" in caddy
    assert "/opt/voice-changer/web/call" in caddy
    assert "--ws-max-size 4096" in compose


def test_spooky_service_number_plays_the_night5_then_scary_montage():
    """1983 must play the requested composite, not the old music-only track."""
    extensions = read("extensions.conf")
    deploy = Path("deploy/deploy-conference.sh").read_text()

    assert "Playback(phoneguy-bot/night5-then-scary)" in extensions
    assert "Playback(phoneguy-bot/scary-music)" not in extensions
    assert 'sounds/night5-then-scary.wav' in deploy


def test_fnaf_callback_sequence_uses_the_new_montage_after_the_first_hangup():
    """The durable 1900 scenario must re-ring 1983 after the Night 1 bite."""
    extensions = read("extensions.conf")
    deploy = Path("deploy/deploy-conference.sh").read_text()
    launcher = Path("deploy/start-fnaf-video-sequence.py")

    assert "exten => 1900,1,NoOp(FNaF callback sequence)" in extensions
    assert "Dial(PJSIP/1983,30,gA(phoneguy-bot/fnaf1-night1-original))" in extensions
    assert "same => n,Wait(1)" in extensions
    assert "Dial(PJSIP/1983,40,A(phoneguy-bot/night5-then-scary))" in extensions
    assert "start-fnaf-video-sequence.py" in deploy
    assert launcher.exists()


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
