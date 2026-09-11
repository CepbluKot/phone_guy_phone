from pathlib import Path


SOURCE = Path(__file__).with_name("live-sip-preflight.py").read_text()


def test_live_sip_preflight_keeps_credentials_remote_and_proves_audio_cleanup():
    assert "sip-{extension}-password" in SOURCE
    assert "TemporaryDirectory" in SOURCE
    assert '"rvcInferenceObserved"' in SOURCE
    assert '"channelsAfter"' in SOURCE
    assert '"privateBridgeAfter"' in SOURCE
    assert "PhoneGuy" not in SOURCE
    assert "scp" not in SOURCE
