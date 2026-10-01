#!/usr/bin/env python3
"""Align deployed SIP transports and physical-phone media policy with source."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^\[{re.escape(name)}\](?:[ \t]*\([^)]*\))?[^\n]*\r?\n.*?(?=^\[|\Z)"
    )


def transport_section(text: str, name: str = "transport-wss") -> str:
    match = section_pattern(name).search(text)
    if match is None:
        raise ValueError(f"{name} section is missing from template")
    block = match.group(0).rstrip()
    if name == "transport-wss":
        required = (
            "type=transport",
            "protocol=wss",
            "external_media_address=192.168.20.70",
            "local_net=172.19.0.0/16",
        )
    elif name == "transport-tls-internet":
        required = (
            "type=transport",
            "protocol=tls",
            "bind=0.0.0.0:5061",
            "method=tlsv1_2",
            "cert_file=/run/voice-tls/current/fullchain.pem",
            "priv_key_file=/run/voice-tls/current/key.pem",
            "allow_reload=yes",
            "external_signaling_address=94.102.89.13",
            "external_signaling_port=5061",
            "external_media_address=94.102.89.13",
            "symmetric_transport=yes",
            "local_net=192.168.20.0/24",
            "local_net=172.19.0.0/16",
        )
        if "local_net=10.19.87.0/24" in block.splitlines():
            raise ValueError("Internet TLS transport must not treat WireGuard as local")
    else:
        raise ValueError(f"unsupported PJSIP transport section: {name}")
    for setting in required:
        if setting not in block.splitlines():
            raise ValueError(f"{name} template is missing {setting}")
    return block


def replace_section(text: str, name: str, block: str) -> str:
    pattern = section_pattern(name)
    match = pattern.search(text)
    if match is None:
        return text.rstrip() + "\n\n" + block.rstrip() + "\n"
    return text[: match.start()] + block.rstrip() + "\n" + text[match.end() :]


def set_section_option(text: str, name: str, option: str, value: str | None) -> str:
    pattern = section_pattern(name)
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"{name} section is missing from runtime PJSIP config")
    block = match.group(0).rstrip("\n")
    lines = [
        line
        for line in block.splitlines()
        if not re.match(rf"^\s*{re.escape(option)}\s*=", line)
    ]
    if value is not None:
        lines.append(f"{option}={value}")
    updated = "\n".join(lines) + "\n"
    return text[: match.start()] + updated + text[match.end() :]


def update_runtime(runtime_path: Path, template_path: Path) -> None:
    current = runtime_path.read_text()
    template = template_path.read_text()
    updated = current
    for name in ("transport-wss", "transport-tls-internet"):
        updated = replace_section(updated, name, transport_section(template, name))
    updated = set_section_option(updated, "phone-endpoint", "symmetric_transport", None)
    updated = set_section_option(updated, "phone-endpoint", "media_encryption", "sdes")
    updated = set_section_option(updated, "phone-endpoint", "media_encryption_optimistic", None)
    for extension in ("1983", "1987", "1988", "2014"):
        updated = set_section_option(updated, extension, "media_encryption", None)
        updated = set_section_option(updated, extension, "media_encryption_optimistic", None)
    if updated != current:
        runtime_path.write_text(updated)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: update_pjsip_wss_transport.py RUNTIME_PJSIP TEMPLATE_PJSIP", file=sys.stderr)
        return 2
    try:
        update_runtime(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as error:
        print(f"could not update PJSIP transports: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
