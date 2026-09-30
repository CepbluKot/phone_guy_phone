#!/usr/bin/env python3
"""Render coturn's root-only shared secret into a protected config file."""

import grp
import os
from pathlib import Path
import re
import stat
import sys


SECRET = Path("/etc/voice-phone/turn-shared-secret")
TEMPLATE = Path("/etc/voice-phone/turnserver.conf.template")
OUTPUT = Path("/run/voice-turn/turnserver.conf")
PLACEHOLDER = "__VOICE_TURN_SHARED_SECRET__"


def render(secret_path=SECRET, template_path=TEMPLATE, output_path=OUTPUT, group_id=None):
    info = secret_path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("TURN secret must be a private regular file")
    secret = secret_path.read_text(encoding="ascii").strip()
    if len(secret) < 32 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", secret):
        raise ValueError("TURN secret is invalid")

    template = template_path.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("TURN config template placeholder is invalid")
    rendered = template.replace(PLACEHOLDER, secret)
    if PLACEHOLDER in rendered:
        raise ValueError("TURN config contains an unresolved secret placeholder")

    output_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_path, flags, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())
        if group_id is not None:
            os.fchown(stream.fileno(), 0, group_id)
        os.fchmod(stream.fileno(), 0o640)
    os.replace(temp_path, output_path)


if __name__ == "__main__":
    try:
        render(group_id=grp.getgrnam("turnserver").gr_gid)
    except (OSError, UnicodeError, ValueError):
        print("TURN config rendering failed", file=sys.stderr)
        raise SystemExit(1)
