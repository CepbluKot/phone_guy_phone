from pathlib import Path


from deploy.sync_asterisk_extensions_runtime import sync_runtime


ROOT = Path(__file__).resolve().parents[1]


def make_active_pstn_config(path):
    path.write_text(
        """[nateks-fxo]
type=endpoint
transport=transport-udp
context=nateks-inbound
auth=nateks-fxo-auth
aors=701
identify_by=ip,username
disallow=all
allow=alaw
direct_media=no
media_encryption=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733

[nateks-fxo-auth]
type=auth
auth_type=userpass
username=nateks-fxo
password=0123456789abcdef0123

[701]
type=aor
max_contacts=1
remove_existing=yes

[nateks-fxo-identify]
type=identify
endpoint=nateks-fxo
match=192.168.20.236/32
"""
    )


def test_sync_adds_only_nateks_context_and_preserves_live_phoneguy_routes(tmp_path):
    runtime = tmp_path / "extensions.conf"
    live_phoneguy = """[phoneguy-sip]
exten => 1900,1,Set(PHONEGUY_SOURCE=${CHANNEL(endpoint)})
 same => n,Stasis(voice-control,source=${PHONEGUY_SOURCE},peer=2016,mode=callback-1900)
 same => n,Hangup()
exten => _X.,1,Stasis(voice-control,peer=${EXTEN})
"""
    runtime.write_text(live_phoneguy)
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)

    sync_runtime(runtime, ROOT / "conference" / "asterisk" / "extensions.conf", pstn)

    rendered = runtime.read_text()
    assert "peer=2016,mode=callback-1900" in rendered
    assert "exten => _X.,1,Stasis(voice-control,peer=${EXTEN})" in rendered
    assert "_7XXXXXXXXXX" in rendered and "_8XXXXXXXXXX" in rendered
    assert rendered.count("[nateks-inbound]") == 1
    assert "Dial(PJSIP/2015,30,b(nateks-inbound-predial^s^1))" in rendered
    assert "Set(CHANNEL(callgroup)=63)" in rendered
    assert "peer=2016,mode=callback-1900" in rendered


def test_outbound_route_accepts_full_russian_numbers_from_authenticated_phone_context():
    extensions = (ROOT / "conference/asterisk/extensions.conf").read_text()
    phoneguy = extensions.split("[phoneguy-sip]", 1)[1].split("[nateks-inbound]", 1)[0]
    managed = phoneguy.split("; BEGIN NATEKS PSTN OUTBOUND", 1)[1].split(
        "; END NATEKS PSTN OUTBOUND", 1
    )[0]

    assert "_7XXXXXXXXXX" in managed
    assert "_8XXXXXXXXXX" in managed
    assert managed.count("${CHANNEL(endpoint)}") == 2
    assert managed.count("Set(NATEKS_NUMBER=8${EXTEN:1})") == 2
    assert managed.count("Dial(PJSIP/701${NATEKS_NUMBER}@nateks-fxo,60)") == 2
    assert "CALLERID" not in managed
    assert "Stasis(" not in managed
    assert phoneguy.index("; BEGIN NATEKS PSTN OUTBOUND") < phoneguy.index("exten => _X.")


def test_sync_merges_managed_outbound_patterns_before_live_catchall(tmp_path):
    runtime = tmp_path / "extensions.conf"
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    live_phoneguy = """[phoneguy-sip]
exten => 1900,1,NoOp(live callback route)
 same => n,Stasis(voice-control,peer=2016)
exten => _X.,1,Stasis(voice-control,peer=${EXTEN})
"""
    runtime.write_text(live_phoneguy)

    sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)

    rendered = runtime.read_text()
    phoneguy = rendered.split("[phoneguy-sip]", 1)[1].split("[nateks-inbound]", 1)[0]
    assert "peer=2016" in phoneguy
    assert "_7XXXXXXXXXX" in phoneguy and "_8XXXXXXXXXX" in phoneguy
    assert phoneguy.index("; END NATEKS PSTN OUTBOUND") < phoneguy.index("exten => _X.")
    assert rendered.count("; BEGIN NATEKS PSTN OUTBOUND") == 1
    assert rendered.count("[nateks-inbound]") == 1

    sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)

    assert runtime.read_text() == rendered


def test_sync_keeps_outbound_disabled_without_an_authenticated_peer(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text(
        "[phoneguy-sip]\nexten => _X.,1,Stasis(voice-control,peer=${EXTEN})\n"
    )
    disabled = tmp_path / "nateks-pstn.conf"
    disabled.write_text("; PSTN remains disabled\n")

    sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", disabled)

    rendered = runtime.read_text()
    assert "[nateks-inbound]" in rendered
    assert "_7XXXXXXXXXX" not in rendered
    assert "_8XXXXXXXXXX" not in rendered


def test_sync_rejects_a_partially_configured_nateks_peer(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    pstn.write_text(
        "[nateks-fxo]\ntype=endpoint\ncontext=nateks-inbound\n"
    )

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "incomplete" in str(error)
    else:
        raise AssertionError("incomplete Nateks peer activated outbound routing")


def test_sync_rejects_a_weak_nateks_digest_secret(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    pstn.write_text(pstn.read_text().replace("0123456789abcdef0123", "short"))

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "secret is missing or too weak" in str(error)
    else:
        raise AssertionError("weak Nateks digest secret activated outbound routing")


def test_sync_rejects_a_public_or_non_lan_identify_match(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    pstn.write_text(pstn.read_text().replace("192.168.20.236/32", "203.0.113.10/32"))

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "private LAN" in str(error)
    else:
        raise AssertionError("public Nateks identify address was accepted")


def test_sync_rejects_a_different_private_identify_address(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    pstn.write_text(pstn.read_text().replace("192.168.20.236/32", "192.168.20.237/32"))

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "gateway address" in str(error)
    else:
        raise AssertionError("different private address was accepted as the Nateks gateway")


def test_sync_rejects_gateway_peer_without_ip_identification(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    pstn.write_text(pstn.read_text().replace("identify_by=ip,username", "identify_by=username"))

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "unsafe media/routing options" in str(error)
    else:
        raise AssertionError("Nateks peer without IP identification was accepted")


def test_sync_rejects_fxo2_when_the_line_is_on_fxo1(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text("[phoneguy-sip]\nexten => _X.,1,Hangup()\n")
    pstn = tmp_path / "nateks-pstn.conf"
    make_active_pstn_config(pstn)
    pstn.write_text(pstn.read_text().replace("aors=701", "aors=702").replace("[701]", "[702]"))

    try:
        sync_runtime(runtime, ROOT / "conference/asterisk/extensions.conf", pstn)
    except ValueError as error:
        assert "configured FXO line AOR" in str(error)
    else:
        raise AssertionError("FXO2 was accepted although the analog line is on FXO1")


def test_sync_replaces_only_the_managed_nateks_context(tmp_path):
    runtime = tmp_path / "extensions.conf"
    runtime.write_text(
        "[phoneguy-sip]\nexten => 1900,1,NoOp(live route)\n\n"
        "[nateks-inbound]\nexten => s,1,Hangup()\n"
    )

    sync_runtime(runtime, ROOT / "conference" / "asterisk" / "extensions.conf")

    rendered = runtime.read_text()
    assert "exten => 1900,1,NoOp(live route)" in rendered
    assert rendered.count("[nateks-inbound]") == 1
    assert "Dial(PJSIP/2015,30,b(nateks-inbound-predial^s^1))" in rendered
    assert "Set(CHANNEL(callgroup)=63)" in rendered
