from pathlib import Path

from deploy.update_pjsip_wss_transport import update_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_adds_tls_and_strict_phone_media_without_replacing_auth_or_contacts(tmp_path):
    runtime = tmp_path / "pjsip.conf"
    runtime.write_text(
        """[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

[global]
type=global
endpoint_identifier_order=username,ip
user_agent=preserve-this

[transport-wss]
type=transport
protocol=wss
bind=0.0.0.0

[phone-endpoint](!)
type=endpoint
context=phoneguy-sip
direct_media=no
symmetric_transport=yes
media_encryption_optimistic=yes

[1983](phone-endpoint)
auth=1983-auth
aors=1983
media_encryption_optimistic=yes

[1983-auth]
type=auth
auth_type=userpass
username=1983
password=do-not-change-1983-secret

[1983]
type=aor
contact=sip:1983@192.168.20.134:5062
max_contacts=1

[1987](phone-endpoint)
auth=1987-auth
aors=1987

[1987-auth]
type=auth
auth_type=userpass
username=1987
password=do-not-change-1987-secret

[1987]
type=aor
max_contacts=1

[1988](phone-endpoint)
auth=1988-auth
aors=1988

[1988-auth]
type=auth
auth_type=userpass
username=1988
password=do-not-change-1988-secret

[1988]
type=aor
max_contacts=1

[2014](phone-endpoint)
auth=2014-auth
aors=2014

[2014-auth]
type=auth
auth_type=userpass
username=2014
password=do-not-change-2014-secret

[2014]
type=aor
max_contacts=1

[2015](phone-endpoint)
auth=2015-auth
aors=2015
call_group=4
pickup_group=4

[2015-auth]
type=auth
auth_type=userpass
username=2015
password=do-not-change-2015-secret

[2015]
type=aor
max_contacts=1
"""
    )
    template = ROOT / "conference/asterisk/pjsip.conf.template"

    update_runtime(runtime, template)

    rendered = runtime.read_text()
    assert "endpoint_identifier_order=ip,username" in rendered
    assert "user_agent=preserve-this" in rendered
    assert rendered.count("[transport-tls-internet]") == 1
    assert "protocol=tls" in rendered
    assert "bind=0.0.0.0:5061" in rendered
    tls = rendered.split("[transport-tls-internet]", 1)[1].split("\n[", 1)[0]
    phone_defaults = rendered.split("[phone-endpoint](!)", 1)[1].split("\n[", 1)[0]
    assert "symmetric_transport=yes" in tls
    assert "symmetric_transport=yes" not in phone_defaults
    assert "media_encryption=sdes" in phone_defaults
    assert "media_encryption_optimistic=yes" not in phone_defaults
    for extension in ("1983", "1987", "1988", "2014"):
        section = rendered.split(f"[{extension}](phone-endpoint)", 1)[1].split("\n[", 1)[0]
        assert "media_encryption_optimistic=yes" not in section
        assert "media_encryption=sdes" not in section
    assert "password=do-not-change-1983-secret" in rendered
    assert "password=do-not-change-1988-secret" in rendered
    assert "contact=sip:1983@192.168.20.134:5062" in rendered
    selected = rendered.split("[2015](phone-endpoint)", 1)[1].split("\n[", 1)[0]
    assert "call_group=4" in selected
    assert "pickup_group=4" in selected
    assert rendered.count("#include nateks-pstn.conf") == 1
    assert rendered.startswith("#include nateks-pstn.conf\n\n[transport-udp]")


def test_runtime_rejects_reusing_reserved_pstn_pickup_group(tmp_path):
    runtime = tmp_path / "pjsip.conf"
    runtime.write_text(
        "[global]\ntype=global\n"
        "[phone-endpoint](!)\ntype=endpoint\n"
        "[1983](phone-endpoint)\n"
        "[1987](phone-endpoint)\n"
        "[1988](phone-endpoint)\n"
        "[2014](phone-endpoint)\n"
        "[2015](phone-endpoint)\ncall_group=63 ; statically reserving 63 is unsafe\n"
    )

    try:
        update_runtime(runtime, ROOT / "conference/asterisk/pjsip.conf.template")
    except ValueError as error:
        assert "group 63 is reserved for Nateks calls" in str(error)
    else:
        raise AssertionError("shared Nateks PSTN pickup group was accepted")
