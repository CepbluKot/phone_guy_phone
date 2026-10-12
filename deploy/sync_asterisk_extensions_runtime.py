#!/usr/bin/env python3
"""Merge the source-owned Nateks context into a live Asterisk dialplan."""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path


CONTEXT = "nateks-inbound"
PHONE_CONTEXT = "phoneguy-sip"
OUTBOUND_BEGIN = "; BEGIN NATEKS PSTN OUTBOUND"
OUTBOUND_END = "; END NATEKS PSTN OUTBOUND"
GATEWAY_ENDPOINT = "nateks-fxo"
GATEWAY_AUTH = "nateks-fxo-auth"
GATEWAY_ADDRESS = "192.168.20.236/32"


def context_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?ms)^\[{re.escape(name)}\][^\n]*\n.*?(?=^\[|\Z)")


def context_from_template(template: str) -> str:
    match = context_pattern(CONTEXT).search(template)
    if match is None:
        raise ValueError(f"{CONTEXT} context is missing from template")
    return match.group(0).rstrip() + "\n"


def outbound_block_from_template(template: str) -> str:
    context = context_pattern(PHONE_CONTEXT).search(template)
    if context is None:
        raise ValueError(f"{PHONE_CONTEXT} context is missing from template")
    text = context.group(0)
    pattern = re.compile(
        rf"(?ms)^{re.escape(OUTBOUND_BEGIN)}[^\n]*\n.*?^{re.escape(OUTBOUND_END)}[^\n]*(?:\n|\Z)"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError("template must contain exactly one managed Nateks outbound block")
    return matches[0].group(0).rstrip() + "\n"


def merge_outbound_context(text: str, managed: str) -> str:
    pattern = context_pattern(PHONE_CONTEXT)
    context = pattern.search(text)
    if context is None:
        raise ValueError(f"{PHONE_CONTEXT} runtime context is missing")
    block_pattern = re.compile(
        rf"(?ms)^{re.escape(OUTBOUND_BEGIN)}[^\n]*\n.*?^{re.escape(OUTBOUND_END)}[^\n]*(?:\n|\Z)"
    )
    current = context.group(0)
    matches = list(block_pattern.finditer(current))
    if len(matches) > 1:
        raise ValueError("runtime has duplicate managed Nateks outbound blocks")
    if matches:
        merged = current[: matches[0].start()] + managed + current[matches[0].end() :]
    else:
        if re.search(r"(?m)^exten\s*=>\s*_(?:(?:495|499)X{7}|[78]X{10})", current):
            raise ValueError("runtime has unmanaged PSTN dial patterns; refusing to duplicate")
        catchall = re.search(r"(?m)^exten\s*=>\s*_X\.,", current)
        if catchall:
            merged = current[: catchall.start()] + managed + current[catchall.start() :]
        else:
            merged = current.rstrip() + "\n" + managed
    if merged == current:
        return text
    return text[: context.start()] + merged + text[context.end() :]


def parse_sections(text: str) -> list[tuple[str, dict[str, str]]]:
    sections: list[tuple[str, dict[str, str]]] = []
    current_name: str | None = None
    current_options: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", stripped)
        if header:
            if current_name is not None:
                sections.append((current_name, current_options))
            current_name = header.group(1)
            current_options = {}
        elif current_name is not None and "=" in stripped:
            key, value = stripped.split("=", 1)
            current_options[key.strip()] = value.strip()
    if current_name is not None:
        sections.append((current_name, current_options))
    return sections


def configured_gateway(path: Path | None) -> bool:
    """Activate outbound routes only for a complete, LAN-bound authenticated peer."""
    if path is None:
        return False
    if path.is_symlink() or not path.is_file():
        raise ValueError("Nateks runtime include is missing or unsafe")
    sections = parse_sections(path.read_text())

    def objects(name: str, kind: str) -> list[dict[str, str]]:
        return [options for section, options in sections if section == name and options.get("type") == kind]

    endpoints = objects(GATEWAY_ENDPOINT, "endpoint")
    if not endpoints:
        return False
    if len(endpoints) != 1:
        raise ValueError("Nateks runtime endpoint is incomplete or duplicated")

    endpoint = endpoints[0]
    expected_endpoint = {
        "transport": "transport-udp",
        "context": "nateks-inbound",
        "auth": GATEWAY_AUTH,
        "identify_by": "ip,username",
        "disallow": "all",
        "allow": "alaw",
        "direct_media": "no",
        "media_encryption": "no",
        "rtp_symmetric": "yes",
        "force_rport": "yes",
        "rewrite_contact": "yes",
        "dtmf_mode": "rfc4733",
    }
    if any(endpoint.get(key) != value for key, value in expected_endpoint.items()):
        raise ValueError("Nateks runtime endpoint is incomplete or has unsafe media/routing options")

    auth = objects(GATEWAY_AUTH, "auth")
    if len(auth) != 1 or auth[0].get("auth_type") != "userpass" or auth[0].get("username") != GATEWAY_ENDPOINT:
        raise ValueError("Nateks runtime digest authentication is incomplete")
    secret = auth[0].get("password", "")
    if len(secret) < 24 or "__" in secret or secret.lower() in {"changeme", "password"}:
        raise ValueError("Nateks runtime digest secret is missing or too weak")

    aor_ids = {"701"}
    aors = [
        (name, options)
        for name, options in sections
        if name in aor_ids and options.get("type") == "aor"
    ]
    if len(aors) != 1 or endpoint.get("aors") != aors[0][0]:
        raise ValueError("Nateks runtime must use the configured FXO line AOR")
    if aors[0][1].get("max_contacts") != "1" or aors[0][1].get("remove_existing") != "yes":
        raise ValueError("Nateks runtime AOR must allow exactly one registered contact")

    identifies = [options for _, options in sections if options.get("type") == "identify" and options.get("endpoint") == GATEWAY_ENDPOINT]
    if len(identifies) != 1:
        raise ValueError("Nateks runtime must have exactly one IP identify object")
    try:
        network = ipaddress.ip_network(identifies[0].get("match", ""), strict=False)
    except ValueError as error:
        raise ValueError("Nateks runtime identify match must be one private LAN address") from error
    home_network = ipaddress.ip_network("192.168.20.0/24")
    if network.version != 4 or network.prefixlen != 32 or not network.subnet_of(home_network):
        raise ValueError("Nateks runtime identify match must be one private LAN address")
    if network != ipaddress.ip_network(GATEWAY_ADDRESS):
        raise ValueError("Nateks runtime identify match does not match the reserved gateway address")
    return True


def sync_runtime(runtime_path: Path, template_path: Path, pstn_runtime_path: Path | None = None) -> None:
    """Replace only the managed context; leave every live route untouched."""
    if runtime_path.is_symlink():
        raise ValueError(f"refusing to follow dialplan runtime symlink: {runtime_path}")
    if not runtime_path.is_file():
        raise ValueError(f"Asterisk runtime dialplan is missing: {runtime_path}")
    if not template_path.is_file():
        raise ValueError(f"Asterisk dialplan template is missing: {template_path}")

    current = runtime_path.read_text()
    template = template_path.read_text()
    updated = current
    if configured_gateway(pstn_runtime_path):
        updated = merge_outbound_context(updated, outbound_block_from_template(template))
    managed = context_from_template(template)
    pattern = context_pattern(CONTEXT)
    match = pattern.search(updated)
    if match is None:
        updated = updated.rstrip() + "\n\n" + managed
    else:
        updated = updated[: match.start()] + managed + updated[match.end() :]
    if updated != current:
        runtime_path.write_text(updated)


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: sync_asterisk_extensions_runtime.py RUNTIME_EXTENSIONS TEMPLATE [NATEKS_RUNTIME_INCLUDE]",
            file=sys.stderr,
        )
        return 2
    try:
        pstn_runtime = Path(sys.argv[3]) if len(sys.argv) == 4 else None
        sync_runtime(Path(sys.argv[1]), Path(sys.argv[2]), pstn_runtime)
    except (OSError, ValueError) as error:
        print(f"could not merge Nateks dialplan context: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
