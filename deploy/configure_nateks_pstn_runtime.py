#!/usr/bin/env python3
"""Create the protected Asterisk peer for the owner's Nateks FXO1 line."""

from __future__ import annotations

import getpass
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


RUNTIME_NAME = "nateks-pstn.conf"
SECRET_PATTERN = re.compile(r"[A-Za-z0-9]{32,64}\Z")


def render_config(secret: str) -> str:
    if not SECRET_PATTERN.fullmatch(secret):
        raise ValueError("SIP secret must be 32-64 ASCII letters or digits")
    if len(set(secret)) < 10:
        raise ValueError("SIP secret does not have enough character variety")

    return f"""; Private Nateks VoiceCom FXO1 peer. This file is runtime-only.
[nateks-fxo]
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
password={secret}

[701]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=30

[nateks-fxo-identify]
type=identify
endpoint=nateks-fxo
match=192.168.20.236/32
"""


def _check_disabled_include(target: Path) -> None:
    if target.is_symlink():
        raise ValueError(f"refusing to follow PSTN runtime symlink: {target}")
    if not target.exists():
        return
    if not target.is_file():
        raise ValueError(f"PSTN runtime path is not a regular file: {target}")
    for line in target.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith((";", "#")):
            raise ValueError("PSTN runtime include is already configured; refusing to replace it")


def write_runtime(runtime_dir: Path, secret: str, replace_existing: bool = False) -> None:
    """Atomically create the peer using the owner/mode of pjsip.conf."""
    if not runtime_dir.is_dir():
        raise ValueError(f"Asterisk runtime directory is missing: {runtime_dir}")
    reference = runtime_dir / "pjsip.conf"
    if reference.is_symlink() or not reference.is_file():
        raise ValueError(f"Asterisk PJSIP runtime file is missing or unsafe: {reference}")

    config = render_config(secret)
    target = runtime_dir / RUNTIME_NAME
    if replace_existing:
        if target.is_symlink() or not target.is_file():
            raise ValueError("replace requires an existing regular Nateks runtime include")
        existing = target.read_text()
        endpoint = re.search(r"(?ms)^\[nateks-fxo\][^\n]*\n(.*?)(?=^\[|\Z)", existing)
        if endpoint is None or not re.search(r"(?m)^type=endpoint\s*$", endpoint.group(1)):
            raise ValueError("replace is allowed only for an existing Nateks endpoint")
    else:
        _check_disabled_include(target)

    reference_stat = reference.stat()
    reference_mode = stat.S_IMODE(reference_stat.st_mode)
    if not reference_mode & stat.S_IRUSR:
        raise ValueError("PJSIP runtime owner cannot read the protected config")

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{RUNTIME_NAME}.", suffix=".tmp", dir=runtime_dir)
    temporary = Path(temporary_name)
    try:
        os.fchown(descriptor, reference_stat.st_uid, reference_stat.st_gid)
        os.fchmod(descriptor, reference_mode)
        with os.fdopen(descriptor, "w") as output:
            descriptor = -1
            output.write(config)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(runtime_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def read_secret_pair() -> tuple[str, str]:
    if sys.stdin.isatty():
        return (
            getpass.getpass("New Nateks SIP password (input hidden): "),
            getpass.getpass("Repeat Nateks SIP password: "),
        )
    return sys.stdin.readline().rstrip("\r\n"), sys.stdin.readline().rstrip("\r\n")


def main() -> int:
    if len(sys.argv) not in {2, 3} or (len(sys.argv) == 3 and sys.argv[2] != "--replace"):
        print("usage: configure_nateks_pstn_runtime.py RUNTIME_DIR [--replace]", file=sys.stderr)
        return 2
    try:
        secret, confirmation = read_secret_pair()
        if secret != confirmation:
            raise ValueError("SIP secrets do not match")
        write_runtime(Path(sys.argv[1]), secret, replace_existing=len(sys.argv) == 3)
    except (OSError, ValueError) as error:
        print(f"could not configure Nateks PSTN runtime: {error}", file=sys.stderr)
        return 1
    print("Nateks FXO1 PJSIP peer configured in the protected runtime include")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
