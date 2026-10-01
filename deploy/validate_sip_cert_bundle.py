#!/usr/bin/env python3
"""Safely extract and validate the fixed phone-domain certificate pair."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile


ALLOWED_MEMBERS = {
    "wildcard-lan-fullchain.pem",
    "wildcard-lan-key.pem",
    "phone-fullchain.pem",
    "phone-key.pem",
}
PHONE_MEMBERS = ("phone-fullchain.pem", "phone-key.pem")
MAX_MEMBER_BYTES = 1_048_576
PHONE_DOMAIN = "phone.awesomeio.ru"


def _run_openssl(arguments: list[str], *, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["openssl", *arguments],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("certificate validation failed")
    return result.stdout


def _write_private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def validate_and_extract(bundle_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Extract the phone cert/key pair after validating names, SAN, age, and key."""
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert_path = output_dir / PHONE_MEMBERS[0]
    key_path = output_dir / PHONE_MEMBERS[1]

    try:
        with tarfile.open(bundle_path, mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(ALLOWED_MEMBERS) or set(names) != ALLOWED_MEMBERS:
                raise ValueError("certificate bundle members are invalid")
            if any(
                not member.isfile() or member.size < 1 or member.size > MAX_MEMBER_BYTES
                for member in members
            ):
                raise ValueError("certificate bundle members are invalid")

            by_name = {member.name: member for member in members}
            for name, destination in zip(PHONE_MEMBERS, (cert_path, key_path), strict=True):
                stream = archive.extractfile(by_name[name])
                if stream is None:
                    raise ValueError("certificate bundle members are invalid")
                payload = stream.read(MAX_MEMBER_BYTES + 1)
                if len(payload) != by_name[name].size or len(payload) > MAX_MEMBER_BYTES:
                    raise ValueError("certificate bundle members are invalid")
                _write_private_file(destination, payload)

        san_output = _run_openssl(["x509", "-in", str(cert_path), "-noout", "-ext", "subjectAltName"])
        dns_names = re.findall(rb"DNS:([^,\s]+)", san_output)
        if PHONE_DOMAIN.encode("ascii") not in dns_names:
            raise ValueError("certificate name does not match phone.awesomeio.ru")
        _run_openssl(["x509", "-in", str(cert_path), "-noout", "-checkend", "86400"])
        cert_public = _run_openssl(["x509", "-in", str(cert_path), "-pubkey", "-noout"])
        cert_der = _run_openssl(["pkey", "-pubin", "-outform", "DER"], input_data=cert_public)
        key_der = _run_openssl(["pkey", "-in", str(key_path), "-pubout", "-outform", "DER"])
        if cert_der != key_der:
            raise ValueError("certificate key does not match")
        return cert_path, key_path
    except (OSError, tarfile.TarError, subprocess.SubprocessError) as error:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
        raise ValueError("certificate bundle validation failed") from error
    except ValueError:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
        raise


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate_sip_cert_bundle.py BUNDLE OUTPUT_DIR", file=sys.stderr)
        return 2
    try:
        validate_and_extract(Path(sys.argv[1]), Path(sys.argv[2]))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
