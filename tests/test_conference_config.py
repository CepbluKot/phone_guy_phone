import re
from pathlib import Path


ROOT = Path("conference/asterisk")


def read(name: str) -> str:
    return (ROOT / name).read_text()


def test_conference_configuration_is_private_and_wideband():
    assert "internal_sample_rate=48000" in read("confbridge.conf")
    assert "autoload=no" in read("modules.conf")
    assert "chan_pjsip" not in read("modules.conf")
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
        "app_stasis",
        "bridge_softmix",
        "chan_websocket",
        "pbx_config",
        "res_ari",
        "res_ari_asterisk",
        "res_ari_channels",
        "res_ari_events",
        "res_ari_model",
        "res_http_websocket",
        "res_sorcery_config",
        "res_stasis",
        "res_stasis_answer",
        "res_stasis_playback",
        "res_stasis_recording",
        "res_stasis_snoop",
        "res_timing_timerfd",
        "res_websocket_client",
    }
    assert "pjsip" not in modules.lower()
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


def test_phone_routes_enter_go_with_trusted_endpoint_identity():
    extensions = read("extensions.conf")
    assert "[phoneguy-sip]" in extensions
    assert "exten => 1999,1,Stasis(selfmonitor)" in extensions
    assert "Set(PHONEGUY_SOURCE=${CHANNEL(endpoint)})" in extensions
    assert "Stasis(voice-control,source=${PHONEGUY_SOURCE},peer=${EXTEN})" in extensions
    assert "Stasis(voice-control,source=${PHONEGUY_SOURCE},peer=conference)" in extensions
    assert "Stasis(voice-control,source=${PHONEGUY_SOURCE},peer=1983)" in extensions
    # Once the route is handed to Go, no endpoint may be dialed natively before policy runs.
    assert not re.search(r"(?:^|\n)\s*(?:same\s*=>\s*n|exten\s*=>).*\bDial\(", extensions)


def test_caddy_routes_go_owned_websockets_to_go_runtime():
    caddy = Path("deploy/Caddyfile.goweb").read_text()
    assert re.search(r"handle /ws/conference\s*\{\s*reverse_proxy 127\.0\.0\.1:8080", caddy)
    assert re.search(r"handle /ws/live-mirror\s*\{\s*reverse_proxy 127\.0\.0\.1:8080", caddy)
    assert re.search(r"handle /ws/rvc-v2\s*\{\s*reverse_proxy 127\.0\.0\.1:8090", caddy)


def test_go_image_uses_the_runtime_uid_that_owns_the_ari_secret():
    dockerfile = Path("Dockerfile.goweb").read_text()
    compose = Path("deploy/compose.goweb.yaml").read_text()
    assert "USER 10001:10001" in dockerfile
    assert "/run/secrets/ari-password:ro" in compose


def test_staging_compose_uses_distinct_loopback_ports_and_ari_app():
    stage = Path("deploy/compose.goweb.stage.yaml").read_text()
    assert "127.0.0.1:8081" in stage
    assert "selfmonitor-go-candidate" in stage
    assert "127.0.0.1:8196" in stage
    assert "127.0.0.1:8197" in stage


def test_go_caddy_upstream_matches_the_private_loopback_listener():
    caddy = Path("deploy/Caddyfile.goweb").read_text().split("# Research demo stack", 1)[0]
    assert "reverse_proxy 127.0.0.1:8080" in caddy
    assert "reverse_proxy 192.168.20.70:8080" not in caddy


def test_legacy_caddy_template_stays_unchanged_until_go_cutover():
    caddy = Path("deploy/Caddyfile").read_text()
    assert "handle /ws/conference {\n        reverse_proxy 127.0.0.1:8091" in caddy
    assert "reverse_proxy 192.168.20.70:8080" in caddy
    assert "/ws/live-mirror" not in caddy


def test_source_build_is_reproducible_non_root_and_health_checked():
    dockerfile = read("Dockerfile")

    assert "asterisk-22.11.0.tar.gz" in dockerfile
    assert "3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94" in dockerfile
    assert re.search(r"^FROM debian:bookworm-slim@sha256:[0-9a-f]{64}", dockerfile, re.MULTILINE)
    assert "sha256sum -c" in dockerfile
    assert "make -j1" in dockerfile
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
