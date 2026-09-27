#!/usr/bin/env python3
"""Keep the deployed WebRTC PJSIP transport aligned with its source template."""

from __future__ import annotations

import re
import sys
from pathlib import Path


SECTION = re.compile(r"(?ms)^\[transport-wss\][ \t]*\r?\n.*?(?=^\[|\Z)")


def transport_section(text: str) -> str:
    match = SECTION.search(text)
    if match is None:
        raise ValueError("transport-wss section is missing from template")
    block = match.group(0).rstrip()
    for setting in (
        "type=transport",
        "protocol=wss",
        "external_media_address=192.168.20.70",
        "local_net=172.19.0.0/16",
    ):
        if setting not in block.splitlines():
            raise ValueError(f"transport-wss template is missing {setting}")
    return block


def update_runtime(runtime_path: Path, template_path: Path) -> None:
    current = runtime_path.read_text()
    canonical = transport_section(template_path.read_text())
    match = SECTION.search(current)
    if match is None:
        updated = current.rstrip() + "\n\n" + canonical + "\n"
    else:
        updated = current[: match.start()] + canonical + "\n" + current[match.end() :]
    if updated != current:
        runtime_path.write_text(updated)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: update_pjsip_wss_transport.py RUNTIME_PJSIP TEMPLATE_PJSIP", file=sys.stderr)
        return 2
    try:
        update_runtime(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as error:
        print(f"could not update PJSIP WebRTC transport: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
