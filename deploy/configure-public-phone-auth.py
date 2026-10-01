#!/usr/bin/env python3
"""Create Go's owner-login verifier config from a password read on stdin."""

import argparse
import base64
import hashlib
import getpass
import json
import os
import secrets
import tempfile
from pathlib import Path


ITERATIONS = 600_000


def create_config(path: Path, password: bytes, uid: int, gid: int) -> None:
    if not 1 <= len(password) <= 256:
        raise ValueError("password length is invalid")
    salt = secrets.token_bytes(16)
    signing_key = secrets.token_bytes(32)
    password_hash = hashlib.pbkdf2_hmac("sha256", password, salt, ITERATIONS, dklen=32)
    document = {
        "username": "voice-owner",
        "passwordSalt": salt.hex(),
        "passwordHash": password_hash.hex(),
        "signingKey": base64.urlsafe_b64encode(signing_key).rstrip(b"=").decode("ascii"),
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".public-auth-", dir=path.parent)
    try:
        os.fchmod(fd, 0o400)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(document, output, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chown(path, uid, gid)
        os.chmod(path, 0o400)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--uid", type=int, default=10001)
    parser.add_argument("--gid", type=int, default=10001)
    args = parser.parse_args()
    import sys

    if sys.stdin.isatty():
        password = getpass.getpass("Owner password: ").encode("utf-8")
    else:
        password = sys.stdin.buffer.readline(258).rstrip(b"\r\n")
    try:
        create_config(args.path, password, args.uid, args.gid)
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
