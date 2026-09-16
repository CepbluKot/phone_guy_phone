#!/usr/bin/env python3
"""Insert the mirror route into the active Caddyfile without replacing other routes."""

from pathlib import Path
import sys


ROUTE = """    handle /ws/live-mirror {
        reverse_proxy 127.0.0.1:8097
    }
"""
ANCHOR = "    handle /ws/conference {\n"


def main(path):
    config = Path(path)
    source = config.read_text()
    if "handle /ws/live-mirror {" in source:
        if source.count(ROUTE) != 1:
            raise ValueError("unexpected existing live-mirror route")
        return
    if source.count(ANCHOR) != 1 or "https://vm-voice-1.lan.awesomeio.ru {" not in source:
        raise ValueError("private voice vhost anchor missing")
    config.write_text(source.replace(ANCHOR, ROUTE + ANCHOR, 1))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-live-mirror-caddy.py Caddyfile")
    main(sys.argv[1])
