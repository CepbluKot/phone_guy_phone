#!/usr/bin/env python3
"""Provision the fixed SIP identity 1234 without printing its generated secret."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from add_physical_extension_2015 import _atomic_replace, _headers, _section


EXTENSION = "1234"
BASE_EXTENSIONS = {"1987", "1988", "2014", "2015", "2016"}
PASSWORD_MARKER = "__SIP_1234_PASSWORD__"
PROFILE_VALUES = {"original", "phone-guy"}


def add_pjsip_endpoint(runtime_path: Path, template_path: Path) -> bool:
    runtime = runtime_path.read_text()
    template = template_path.read_text()
    headers = _headers(runtime)
    expected = {EXTENSION, f"{EXTENSION}-auth"}
    present = headers & expected
    if present:
        if present != expected:
            raise ValueError("SIP extension runtime is incomplete")
        endpoint = _section(runtime, EXTENSION, "(phone-endpoint)")
        auth = _section(runtime, f"{EXTENSION}-auth")
        aor = _section(runtime, EXTENSION)
        required = (
            ("auth=1234-auth", endpoint),
            ("aors=1234", endpoint),
            ("transport=transport-tls-internet", endpoint),
            ("type=auth", auth),
            ("auth_type=userpass", auth),
            ("username=1234", auth),
            ("type=aor", aor),
            ("max_contacts=1", aor),
        )
        if PASSWORD_MARKER in auth or not all(setting in block.splitlines() for setting, block in required):
            raise ValueError("SIP extension runtime is inconsistent")
        return False

    if not {f"{number}-auth" for number in BASE_EXTENSIONS}.issubset(headers):
        raise ValueError("SIP endpoint inventory is not the expected baseline")

    endpoint = _section(template, EXTENSION, "(phone-endpoint)")
    auth = _section(template, f"{EXTENSION}-auth")
    aor = _section(template, EXTENSION)
    if auth.count(PASSWORD_MARKER) != 1:
        raise ValueError("SIP extension template has no unique password placeholder")
    password = secrets.token_hex(16)
    addition = "\n".join((endpoint.rstrip(), auth.replace(PASSWORD_MARKER, password, 1).rstrip(), aor.rstrip()))
    _atomic_replace(runtime_path, (runtime.rstrip() + "\n\n" + addition + "\n").encode())
    return True


def add_route_profile(route_path: Path) -> bool:
    data = json.loads(route_path.read_text())
    routes = data.get("extensions")
    expected = BASE_EXTENSIONS
    if (
        data.get("schemaVersion") != 1
        or not isinstance(data.get("revision"), int)
        or data["revision"] < 1
        or not isinstance(routes, dict)
        or not set(routes).issubset(expected | {EXTENSION})
        or not BASE_EXTENSIONS.issubset(routes)
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
