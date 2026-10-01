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
"""
    )
    template = ROOT / "conference/asterisk/pjsip.conf.template"

    update_runtime(runtime, template)

    rendered = runtime.read_text()
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
