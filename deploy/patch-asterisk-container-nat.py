#!/usr/bin/env python3
"""Make a containerised Asterisk advertise the VM's reachable SIP/RTP address."""

from pathlib import Path
import re
import sys


NETWORK = "192.168.20.70"
REQUIRED = (
    "local_net=172.19.0.0/16",
    f"external_signaling_address={NETWORK}",
    "external_signaling_port=5060",
    f"external_media_address={NETWORK}",
)


def main(path):
    config = Path(path)
    source = config.read_text()
    match = re.search(
        r"(^\[transport-udp\]$\n)(.*?)(?=^\[)", source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError("transport-udp missing")
    transport = match.group(2)
    retained = [line for line in transport.splitlines()
                if not line.startswith(("local_net=", "external_signaling_", "external_media_address="))]
    replacement = "\n".join(retained + list(REQUIRED)) + "\n"
    config.write_text(source[:match.start(2)] + replacement + source[match.end(2):])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-asterisk-container-nat.py pjsip.conf")
    main(sys.argv[1])
