from pathlib import Path
import re
import os
import stat
import subprocess
import sys
import pytest


from deploy.sync_asterisk_pstn_runtime import sync_runtime


ROOT = Path(__file__).resolve().parents[1]


def make_runtime(tmp_path):
    runtime = tmp_path / "asterisk"
    runtime.mkdir()
    pjsip = runtime / "pjsip.conf"
    pjsip.write_text("[global]\n")
    pjsip.chmod(0o600)
    return runtime, pjsip


def test_sync_creates_an_explicitly_disabled_pstn_runtime_include(tmp_path):
    runtime, pjsip = make_runtime(tmp_path)

    sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")

    gateway = runtime / "nateks-pstn.conf"
    rendered = gateway.read_text()
    assert "PSTN remains disabled" in rendered
    assert not re.search(r"^type=endpoint$", rendered, re.MULTILINE)
    assert (gateway.stat().st_uid, gateway.stat().st_gid, stat.S_IMODE(gateway.stat().st_mode)) == (
        pjsip.stat().st_uid,
        pjsip.stat().st_gid,
        stat.S_IMODE(pjsip.stat().st_mode),
    )


def test_sync_preserves_existing_gateway_configuration_byte_for_byte(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    gateway = runtime / "nateks-pstn.conf"
    configured = "[nateks-fxo]\ntype=endpoint\nauth=nateks-fxo-auth\npassword=runtime-only\n"
    gateway.write_text(configured)
    gateway.chmod(0o600)

    sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")

    assert gateway.read_text() == configured


def test_sync_refuses_a_runtime_symlink(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    target = tmp_path / "outside.conf"
    target.write_text("do-not-overwrite\n")
    (runtime / "nateks-pstn.conf").symlink_to(target)

    try:
        sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")
    except ValueError as error:
        assert "symlink" in str(error)
    else:
        raise AssertionError("runtime PSTN symlink was accepted")

    assert target.read_text() == "do-not-overwrite\n"


def test_sync_rejects_a_non_file_runtime_path(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    (runtime / "nateks-pstn.conf").mkdir()

    with pytest.raises(ValueError, match="not a regular file"):
        sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")


def test_sync_rejects_runtime_permissions_that_differ_from_pjsip(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    gateway = runtime / "nateks-pstn.conf"
    gateway.write_text("[nateks-fxo]\ntype=endpoint\n")
    gateway.chmod(0o644)

    with pytest.raises(ValueError, match="owner/mode differs"):
        sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")


def test_sync_requires_a_safe_pjsip_reference(tmp_path):
    runtime = tmp_path / "asterisk"
    runtime.mkdir()
    os.symlink(tmp_path / "missing-pjsip.conf", runtime / "pjsip.conf")

    with pytest.raises(ValueError, match="missing or unsafe"):
        sync_runtime(runtime, ROOT / "conference" / "asterisk" / "nateks-pstn.conf.template")


def test_secure_configurator_writes_a_valid_peer_without_echoing_the_secret(tmp_path):
    runtime, pjsip = make_runtime(tmp_path)
    secret = "Abcdefghijklmnopqrstuvwxyz123456"

    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy/configure_nateks_pstn_runtime.py"), str(runtime)],
        input=f"{secret}\n{secret}\n",
        text=True,
        capture_output=True,
        check=False,
    )

    gateway = runtime / "nateks-pstn.conf"
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout + result.stderr
    rendered = gateway.read_text()
    assert "[nateks-fxo]\ntype=endpoint" in rendered
    assert "context=nateks-inbound" in rendered
    assert "aors=701" in rendered
    assert "username=nateks-fxo" in rendered
    assert f"password={secret}" in rendered
    assert "match=192.168.20.236/32" in rendered
    assert (gateway.stat().st_uid, gateway.stat().st_gid, stat.S_IMODE(gateway.stat().st_mode)) == (
        pjsip.stat().st_uid,
        pjsip.stat().st_gid,
        stat.S_IMODE(pjsip.stat().st_mode),
    )


def test_secure_configurator_rejects_short_secret_without_changing_disabled_include(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    gateway = runtime / "nateks-pstn.conf"
    gateway.write_text("; disabled\n")
    original = gateway.read_text()

    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy/configure_nateks_pstn_runtime.py"), str(runtime)],
        input="short\nshort\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "32-64" in result.stderr
    assert "short" not in result.stdout + result.stderr
    assert gateway.read_text() == original


def test_secure_configurator_requires_explicit_replace_for_existing_peer(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    gateway = runtime / "nateks-pstn.conf"
    old_secret = "OldSecretNotForAnyLiveSystem123456"
    gateway.write_text(f"[nateks-fxo]\ntype=endpoint\npassword={old_secret}\n")
    new_secret = "NewSecretNotForAnyLiveSystem654321"

    refused = subprocess.run(
        [sys.executable, str(ROOT / "deploy/configure_nateks_pstn_runtime.py"), str(runtime)],
        input=f"{new_secret}\n{new_secret}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode != 0
    assert gateway.read_text().find(old_secret) >= 0

    replaced = subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/configure_nateks_pstn_runtime.py"),
            str(runtime),
            "--replace",
        ],
        input=f"{new_secret}\n{new_secret}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr
    assert old_secret not in gateway.read_text()
    assert new_secret in gateway.read_text()
    assert old_secret not in replaced.stdout + replaced.stderr
    assert new_secret not in replaced.stdout + replaced.stderr
    assert not list(runtime.glob("*.tmp"))


def test_sync_dialplan_replace_failure_preserves_live_file(tmp_path):
    import unittest.mock

    runtime = tmp_path / "extensions.conf"
    original = "[phoneguy-sip]\nexten => _X.,1,NoOp(live route)\n"
    runtime.write_text(original)
    pstn = tmp_path / "nateks-pstn.conf"
    pstn.write_text(
        "[nateks-fxo]\ntype=endpoint\ntransport=transport-udp\ncontext=nateks-inbound\n"
        "auth=nateks-fxo-auth\naors=701\nidentify_by=ip,username\ndisallow=all\nallow=alaw\n"
        "direct_media=no\nmedia_encryption=no\nrtp_symmetric=yes\nforce_rport=yes\n"
        "rewrite_contact=yes\ndtmf_mode=rfc4733\n[nateks-fxo-auth]\ntype=auth\n"
        "auth_type=userpass\nusername=nateks-fxo\npassword=Abcdefghijklmnopqrstuvwxyz123456\n"
        "[701]\ntype=aor\nmax_contacts=1\nremove_existing=yes\n"
        "[nateks-fxo-identify]\ntype=identify\nendpoint=nateks-fxo\nmatch=192.168.20.236/32\n"
    )

    with unittest.mock.patch("os.replace", side_effect=OSError("simulated atomic replace failure")):
        with pytest.raises(OSError, match="simulated atomic replace failure"):
            from deploy.sync_asterisk_extensions_runtime import sync_runtime as sync_dialplan

            sync_dialplan(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)

    assert runtime.read_text() == original


def test_inbound_context_has_one_fixed_phone_target_and_no_go_or_rvc_handoff():
    extensions = (ROOT / "conference" / "asterisk" / "extensions.conf").read_text()
    inbound = extensions.split("[nateks-inbound]", 1)[1]

    assert "Dial(PJSIP/2015,30,b(nateks-inbound-predial^s^1))" in inbound
    assert "Stasis(" not in inbound
    assert "PJSIP/2016" not in inbound
    assert "PJSIP/1234" not in inbound
    assert "b(nateks-inbound-predial^s^1)" in inbound
    predial = extensions.split("[nateks-inbound-predial]", 1)[1]
    assert "Set(CHANNEL(callgroup)=63)" in predial
    assert "Return()" in predial


def test_every_sip_runtime_release_creates_and_mounts_the_preserved_include():
    scripts = tuple(
        (ROOT / "deploy" / name).read_text()
        for name in (
            "deploy-asterisk-sip.sh",
            "remote-deploy-asterisk-sip.sh",
            "deploy-goweb.sh",
            "update-goweb.sh",
        )
    )
    for helper in ("sync_asterisk_pstn_runtime.py", "sync_asterisk_extensions_runtime.py"):
        assert all(helper in script for script in scripts)
    assert all("configure_nateks_pstn_runtime.py" in script for script in scripts)

    sip_remote = scripts[1]
    sip_remote_flat = " ".join(sip_remote.replace("\\\n", " ").split())
    assert '"$new_release/conference/asterisk/sorcery.conf"' in sip_remote
    assert '"$new_release/runtime/asterisk/sorcery.conf"' in sip_remote
    assert 'cp "$new_release/conference/asterisk/modules.conf" "$new_release/runtime/asterisk/modules.conf"' in sip_remote_flat
    assert 'chown --reference="$old_release/runtime/asterisk/modules.conf" "$new_release/runtime/asterisk/modules.conf"' in sip_remote_flat
    assert '"$new_release/runtime/asterisk/nateks-pstn.conf"' in sip_remote
    assert '"$new_runtime/asterisk/nateks-pstn.conf"' in scripts[2]
    assert '"$new_runtime/asterisk/nateks-pstn.conf"' in scripts[3]
    update_goweb = scripts[3]
    assert update_goweb.index("sync_asterisk_pstn_runtime.py") < update_goweb.index(
        "sync_asterisk_extensions_runtime.py"
    )
    assert 'cp "$release/conference/asterisk/modules.conf" "$new_runtime/asterisk/modules.conf"' in update_goweb
    assert "pjsip show endpoint nateks-fxo" in sip_remote
    assert "pjsip show aor 701" in sip_remote
    assert "pjsip show identify nateks-fxo-identify" in sip_remote
    assert "test -r /etc/asterisk/nateks-pstn.conf" in sip_remote
    assert "dialplan show nateks-inbound" in sip_remote
    assert "PRELIVE_CANDIDATE_REMOVED" in sip_remote
    assert "iptables -C DOCKER-USER -i eth0 -p udp --dport 5060 -j VOICE_SIP_INGRESS" in sip_remote
    assert "for pattern in _7XXXXXXXXXX _8XXXXXXXXXX" in sip_remote
    assert 'dialplan show ${pattern}@phoneguy-sip' in sip_remote

    compose = (ROOT / "deploy" / "compose.conference.yaml").read_text()
    assert "nateks-pstn.conf:/etc/asterisk/nateks-pstn.conf:ro" in compose
