#!/usr/bin/env python3
"""Enable RTP comfort-noise keepalives only for the physical Yealink."""

from pathlib import Path
import re
import sys


def main(path):
    config = Path(path)
    source = config.read_text()
    match = re.search(
        r"(^\[1983\]\(phone-endpoint\)$\n)(.*?)(?=^\[)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError("1983 endpoint missing")
    endpoint = match.group(2)
    if "rtp_keepalive=" in endpoint:
        if endpoint.count("rtp_keepalive=1") != 1:
            raise ValueError("unexpected 1983 rtp_keepalive")
        return
    if endpoint.count("rtp_timeout=0\n") != 1:
        raise ValueError("1983 rtp_timeout anchor missing")
    endpoint = endpoint.replace("rtp_timeout=0\n", "rtp_timeout=0\nrtp_keepalive=1\n")
    config.write_text(source[:match.start(2)] + endpoint + source[match.end(2):])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-physical-phone-pjsip.py pjsip.conf")
    main(sys.argv[1])
