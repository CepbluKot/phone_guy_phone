from importlib.util import module_from_spec, spec_from_file_location
import io
from pathlib import Path
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "deploy/validate_sip_cert_bundle.py"
ALLOWED_MEMBERS = (
    "wildcard-lan-fullchain.pem",
    "wildcard-lan-key.pem",
    "phone-fullchain.pem",
    "phone-key.pem",
)


def _validator():
    assert VALIDATOR.is_file(), "SIP certificate validator is missing"
    spec = spec_from_file_location("sip_cert_validator", VALIDATOR)
    assert spec is not None and spec.loader is not None, "SIP certificate validator is missing"
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_pair(directory: Path, *, host: str = "phone.awesomeio.ru", days: int = 30):
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", str(days),
            "-subj", f"/CN={host}", "-addext", f"subjectAltName=DNS:{host}",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def _bundle(path: Path, cert: Path, key: Path, *, extra: bool = False):
    with tarfile.open(path, "w") as archive:
        for name, data in (
            (ALLOWED_MEMBERS[0], b"unused LAN cert"),
            (ALLOWED_MEMBERS[1], b"unused LAN key"),
            (ALLOWED_MEMBERS[2], cert.read_bytes()),
            (ALLOWED_MEMBERS[3], key.read_bytes()),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, fileobj=io.BytesIO(data))
        if extra:
            data = b"unexpected"
            info = tarfile.TarInfo("extra.txt")
            info.size = len(data)
            archive.addfile(info, fileobj=io.BytesIO(data))


def test_valid_bundle_extracts_only_phone_pair_with_restricted_modes(tmp_path):
    cert, key = _make_pair(tmp_path / "valid")
    bundle = tmp_path / "bundle.tar"
    _bundle(bundle, cert, key)
    output = tmp_path / "output"

    cert_path, key_path = _validator().validate_and_extract(bundle, output)

    assert cert_path.read_bytes() == cert.read_bytes()
    assert key_path.read_bytes() == key.read_bytes()
    assert sorted(path.name for path in output.iterdir()) == ["phone-fullchain.pem", "phone-key.pem"]
    assert cert_path.stat().st_mode & 0o777 == 0o600
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_bundle_rejects_extra_members(tmp_path):
    cert, key = _make_pair(tmp_path / "valid")
    bundle = tmp_path / "bundle.tar"
    _bundle(bundle, cert, key, extra=True)

    with pytest.raises(ValueError, match="members"):
        _validator().validate_and_extract(bundle, tmp_path / "output")


def test_bundle_rejects_wrong_server_name(tmp_path):
    cert, key = _make_pair(tmp_path / "wrong-host", host="wrong.example")
    bundle = tmp_path / "bundle.tar"
    _bundle(bundle, cert, key)

    with pytest.raises(ValueError, match="certificate"):
        _validator().validate_and_extract(bundle, tmp_path / "output")


def test_bundle_rejects_certificate_with_less_than_one_day_remaining(tmp_path):
    cert, key = _make_pair(tmp_path / "near-expiry", days=1)
    bundle = tmp_path / "bundle.tar"
    _bundle(bundle, cert, key)

    with pytest.raises(ValueError, match="certificate"):
        _validator().validate_and_extract(bundle, tmp_path / "output")


def test_bundle_rejects_mismatched_private_key(tmp_path):
    cert, _ = _make_pair(tmp_path / "cert")
    _, wrong_key = _make_pair(tmp_path / "other")
    bundle = tmp_path / "bundle.tar"
    _bundle(bundle, cert, wrong_key)

    with pytest.raises(ValueError, match="key"):
        _validator().validate_and_extract(bundle, tmp_path / "output")
