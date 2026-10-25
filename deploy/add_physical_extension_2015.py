#!/usr/bin/env python3
"""Idempotently add the dedicated physical SIP extension 2015 at Go cutover."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path


EXTENSION = "2015"
LEGACY_EXTENSIONS = {"1983", "1987", "1988", "2014"}
MIGRATED_EXTENSIONS = {"1987", "1988", "2014", "2016"}
OPTIONAL_EXTENSION = "1234"
PROFILE_VALUES = {"original", "phone-guy"}
PASSWORD_MARKER = "__SIP_2015_PASSWORD__"


def _section(text: str, name: str, suffix: str = "") -> str:
    pattern = re.compile(
        rf"(?ms)^\[{re.escape(name)}\]{re.escape(suffix)}[ \t]*\r?\n.*?(?=^\[|\Z)"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError("SIP extension template or runtime is inconsistent")
    return matches[0].group(0)


def _headers(text: str) -> set[str]:
    return set(re.findall(r"(?m)^\[([^]]+)\]", text))


def _atomic_replace(path: Path, data: bytes) -> None:
    info = path.stat()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchown(fd, info.st_uid, info.st_gid)
        os.fchmod(fd, stat.S_IMODE(info.st_mode))
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def add_pjsip_endpoint(runtime_path: Path, template_path: Path) -> bool:
    runtime = runtime_path.read_text()
    template = template_path.read_text()
    required_headers = {"2015", "2015-auth"}
    runtime_headers = _headers(runtime)
    if "2015" in runtime_headers or "2015-auth" in runtime_headers:
        if not required_headers.issubset(runtime_headers):
            raise ValueError("SIP extension runtime is incomplete")
        endpoint = _section(runtime, "2015", "(phone-endpoint)")
        auth = _section(runtime, "2015-auth")
        aor = _section(runtime, "2015")
        if PASSWORD_MARKER in auth or not all(
            setting in block.splitlines()
            for setting, block in (
                ("auth=2015-auth", endpoint),
                ("aors=2015", endpoint),
                ("type=auth", auth),
                ("auth_type=userpass", auth),
                ("username=2015", auth),
                ("type=aor", aor),
                ("max_contacts=1", aor),
            )
        ):
            raise ValueError("SIP extension runtime is inconsistent")
        return False

    known_optional_headers = {OPTIONAL_EXTENSION, f"{OPTIONAL_EXTENSION}-auth"}
    if runtime_headers & known_optional_headers and not known_optional_headers.issubset(runtime_headers):
        raise ValueError("SIP extension runtime is inconsistent")
    baseline_headers = runtime_headers - known_optional_headers
    if LEGACY_EXTENSIONS.issubset(baseline_headers):
        baseline = LEGACY_EXTENSIONS
    elif MIGRATED_EXTENSIONS.issubset(baseline_headers):
        baseline = MIGRATED_EXTENSIONS
    else:
        raise ValueError("SIP endpoint inventory is not the expected baseline")
    for extension in baseline:
        if extension not in runtime_headers or f"{extension}-auth" not in runtime_headers:
            raise ValueError("SIP endpoint inventory is not the expected baseline")

    endpoint = _section(template, "2015", "(phone-endpoint)")
    auth = _section(template, "2015-auth")
    aor = _section(template, "2015")
    if auth.count(PASSWORD_MARKER) != 1:
        raise ValueError("SIP extension template has no unique password placeholder")
    secret = secrets.token_hex(16)
    addition = "\n".join((endpoint.rstrip(), auth.replace(PASSWORD_MARKER, secret, 1).rstrip(), aor.rstrip()))
    _atomic_replace(runtime_path, (runtime.rstrip() + "\n\n" + addition + "\n").encode())
    return True


def add_route_profile(route_path: Path) -> bool:
    data = json.loads(route_path.read_text())
    routes = data.get("extensions")
    if (
        data.get("schemaVersion") != 1
        or not isinstance(data.get("revision"), int)
        or data["revision"] < 1
        or not isinstance(routes, dict)
        or set(routes) not in (
            LEGACY_EXTENSIONS,
            LEGACY_EXTENSIONS | {EXTENSION},
            MIGRATED_EXTENSIONS,
            MIGRATED_EXTENSIONS | {EXTENSION},
            LEGACY_EXTENSIONS | {OPTIONAL_EXTENSION},
            LEGACY_EXTENSIONS | {EXTENSION, OPTIONAL_EXTENSION},
            MIGRATED_EXTENSIONS | {OPTIONAL_EXTENSION},
            MIGRATED_EXTENSIONS | {EXTENSION, OPTIONAL_EXTENSION},
        )
        or not set(routes.values()).issubset(PROFILE_VALUES)
    ):
        raise ValueError("voice route inventory is inconsistent")
    if EXTENSION in routes:
        return False
    if data["revision"] >= 2**64 - 1:
        raise ValueError("voice route revision is exhausted")
    routes[EXTENSION] = "original"
    data["revision"] += 1
    _atomic_replace(route_path, (json.dumps(data, indent=2) + "\n").encode())
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_pjsip", type=Path)
    parser.add_argument("template_pjsip", type=Path)
    parser.add_argument("voice_routes", type=Path)
    args = parser.parse_args()
    try:
        pjsip_added = add_pjsip_endpoint(args.runtime_pjsip, args.template_pjsip)
        route_added = add_route_profile(args.voice_routes)
    except (OSError, ValueError, json.JSONDecodeError):
        print("SIP extension preparation failed", file=__import__("sys").stderr)
        return 1
    print(f"SIP_EXTENSION_READY extension={EXTENSION} pjsip_added={str(pjsip_added).lower()} route_added={str(route_added).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
