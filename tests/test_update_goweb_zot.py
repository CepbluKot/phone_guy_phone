import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "update-goweb.sh"


def test_zot_images_replace_the_on_vm_build_but_keep_the_compose_tag_names():
    source = SCRIPT.read_text()
    pull = source.index('docker pull "$zot_go_image"')
    build = source.index('docker build -f "$release/Dockerfile.goweb"')

    assert pull < build  # the Zot branch comes first, the on-VM build stays as the default
    assert 'docker tag "$zot_go_image" "voice-go:update-$stamp"' in source
    assert 'docker tag "$zot_asterisk_image" "voice-conference-asterisk:update-$stamp"' in source
    assert "images.lan.awesomeio.ru:5002/phone-guy/voice-web:$zot_tag" in source
    assert "images.lan.awesomeio.ru:5002/phone-guy/asterisk-pbx:$zot_tag" in source


def test_zot_mode_is_opt_in_and_requires_a_safe_tag():
    source = SCRIPT.read_text()

    assert '"${DEPLOY_GOWEB_ZOT:-0}" = 1' in source
    assert "Set DEPLOY_GOWEB_ZOT_TAG to the CI image tag" in source
    # The tag is validated before it reaches the remote shell.
    assert source.index("DEPLOY_GOWEB_ZOT_TAG") < source.index("sudo bash -s --")


def test_zot_mode_rejects_a_missing_or_unsafe_tag_before_touching_the_vm():
    for tag in ("", "bad tag", "x;rm -rf /", "../x"):
        result = subprocess.run(
            [str(SCRIPT)],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "DEPLOY_GOWEB_ZOT": "1", "DEPLOY_GOWEB_ZOT_TAG": tag, "DEPLOY_GOWEB_SKIP_CHECKS": "1"},
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 2, (tag, result.stdout, result.stderr)
        assert "DEPLOY_GOWEB_ZOT_TAG" in result.stderr
