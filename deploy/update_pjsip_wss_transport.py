#!/usr/bin/env python3
"""Align deployed SIP transports and physical-phone media policy with source."""

from __future__ import annotations

import re
import os
import stat
import sys
import tempfile
from pathlib import Path


PSTN_INCLUDE = "#include nateks-pstn.conf"
PSTN_PICKUP_GROUP = 63


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
    pieces = (
        text[: match.start()].rstrip("\n"),
        block.rstrip(),
        text[match.end() :].lstrip("\n"),
    )
    return "\n\n".join(piece for piece in pieces if piece).rstrip() + "\n"


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


def ensure_pstn_include(text: str) -> str:
    """Include the runtime-owned PSTN file exactly once without replacing it."""
    count = sum(line.strip() == PSTN_INCLUDE for line in text.splitlines())
    if count > 1:
        raise ValueError("runtime PJSIP config has duplicate Nateks PSTN includes")
    if count == 1:
        return text
    first_section = re.search(r"(?m)^[ \t]*\[", text)
    if first_section is None:
        return PSTN_INCLUDE + "\n" + text
    return text[: first_section.start()] + PSTN_INCLUDE + "\n\n" + text[first_section.start() :]


def _contains_pickup_group(value: str, reserved: int) -> bool:
    for item in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", item)
        if match is None:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        if start <= reserved <= end:
            return True
    return False


def validate_reserved_pickup_group(text: str) -> None:
    for line in text.splitlines():
        active = re.split(r"[;#]", line, maxsplit=1)[0].strip()
        if "=" not in active:
            continue
        option, value = (part.strip().lower() for part in active.split("=", 1))
        if option in {"call_group", "pickup_group"} and _contains_pickup_group(
            value, PSTN_PICKUP_GROUP
        ):
            raise ValueError("group 63 is reserved for Nateks calls to extension 2015")


def _atomic_write(path: Path, contents: str, reference_stat: os.stat_result) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(reference_stat.st_mode))
        temporary_stat = os.fstat(descriptor)
        if (temporary_stat.st_uid, temporary_stat.st_gid) != (reference_stat.st_uid, reference_stat.st_gid):
            os.fchown(descriptor, reference_stat.st_uid, reference_stat.st_gid)
        with os.fdopen(descriptor, "w") as output:
            descriptor = -1
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def update_runtime(runtime_path: Path, template_path: Path) -> None:
    if runtime_path.is_symlink() or not runtime_path.is_file():
        raise ValueError(f"PJSIP runtime file is missing or unsafe: {runtime_path}")
    if not template_path.is_file():
        raise ValueError(f"PJSIP template is missing: {template_path}")
    current = runtime_path.read_text()
    validate_reserved_pickup_group(current)
    template = template_path.read_text()
    updated = current
    for name in ("transport-wss", "transport-tls-internet"):
        updated = replace_section(updated, name, transport_section(template, name))
    if section_pattern("global").search(updated) is None:
        updated = updated.rstrip() + "\n\n[global]\ntype=global\n"
    updated = set_section_option(updated, "global", "endpoint_identifier_order", "ip,username")
    updated = set_section_option(updated, "phone-endpoint", "symmetric_transport", None)
    updated = set_section_option(updated, "phone-endpoint", "media_encryption", "sdes")
    updated = set_section_option(updated, "phone-endpoint", "media_encryption_optimistic", None)
    for extension in ("1983", "1987", "1988", "2014"):
        updated = set_section_option(updated, extension, "media_encryption", None)
        updated = set_section_option(updated, extension, "media_encryption_optimistic", None)
    updated = ensure_pstn_include(updated)
    if updated != current:
        _atomic_write(runtime_path, updated, runtime_path.stat())


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
