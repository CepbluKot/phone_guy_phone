#!/usr/bin/env python3
"""Fail unless both approved Moscow-number patterns dial only through Nateks."""

from __future__ import annotations

import re
import sys


EXTENSION_HEADER = re.compile(r"^\s*'([^']+)'\s*=>")
EXPECTED_DIAL = "PJSIP/701${NATEKS_NUMBER}@nateks-fxo,60"
REQUIRED_PATTERNS = ("_7XXXXXXXXXX", "_8XXXXXXXXXX")


def verify_outbound_dialplan(output: str) -> None:
    routes: dict[str, list[str]] = {}
    current_extension: str | None = None

    for line in output.splitlines():
        header = EXTENSION_HEADER.match(line)
        if header:
            current_extension = header.group(1)
            routes.setdefault(current_extension, []).append(line)
        elif current_extension is not None:
            routes[current_extension].append(line)

    for pattern in REQUIRED_PATTERNS:
        route = routes.get(pattern)
        if route is None:
            raise ValueError(f"required outbound pattern {pattern} is missing")
        dials = re.findall(r"\bDial\(([^)]*)\)", "\n".join(route))
        if dials != [EXPECTED_DIAL]:
            raise ValueError(
                f"outbound pattern {pattern} must contain exactly one approved Nateks Dial"
            )


def main() -> int:
    try:
        verify_outbound_dialplan(sys.stdin.read())
    except ValueError as error:
        print(f"NATEKS_OUTBOUND_DIALPLAN_INVALID: {error}", file=sys.stderr)
        return 1
    print("NATEKS_OUTBOUND_DIALPLAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
