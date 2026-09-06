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
        "bridge_softmix",
        "chan_websocket",
        "pbx_config",
        "res_ari",
        "res_ari_asterisk",
        "res_ari_channels",
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
    assert 'CMD ["asterisk", "-f"' in dockerfile


def test_healthcheck_requires_every_allowlisted_module():
    modules = set(
        re.findall(r"^load\s*=>\s*([a-z0-9_]+)\.so$", read("modules.conf"), re.MULTILINE)
    )
    healthcheck = read("healthcheck.sh")

    checked = set(re.findall(r"^    ([a-z0-9_]+) \\$", healthcheck, re.MULTILINE))
    checked.add(re.search(r"^    ([a-z0-9_]+)$", healthcheck, re.MULTILINE).group(1))
    assert checked == modules
