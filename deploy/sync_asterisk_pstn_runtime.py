#!/usr/bin/env python3
"""Create the disabled Nateks runtime include without replacing live config."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


RUNTIME_NAME = "nateks-pstn.conf"


def sync_runtime(runtime_dir: Path, template_path: Path) -> None:
    """Create the disabled include once; preserve any configured peer verbatim."""
    if not runtime_dir.is_dir():
        raise ValueError(f"Asterisk runtime directory is missing: {runtime_dir}")
    if not template_path.is_file():
        raise ValueError(f"PSTN runtime template is missing: {template_path}")

    reference = runtime_dir / "pjsip.conf"
    if reference.is_symlink() or not reference.is_file():
        raise ValueError(f"Asterisk PJSIP runtime file is missing or unsafe: {reference}")

    target = runtime_dir / RUNTIME_NAME
    if target.is_symlink():
        raise ValueError(f"refusing to follow PSTN runtime symlink: {target}")
    if target.exists():
        if not target.is_file():
            raise ValueError(f"PSTN runtime path is not a regular file: {target}")
        reference_mode = stat.S_IMODE(reference.stat().st_mode)
        target_stat = target.stat()
        if (target_stat.st_uid, target_stat.st_gid, stat.S_IMODE(target_stat.st_mode)) != (
            reference.stat().st_uid,
            reference.stat().st_gid,
            reference_mode,
        ):
            raise ValueError(f"PSTN runtime owner/mode differs from pjsip.conf: {target}")
        return

    shutil.copyfile(template_path, target)
    reference_stat = reference.stat()
    target.chmod(stat.S_IMODE(reference_stat.st_mode))
    os.chown(target, reference_stat.st_uid, reference_stat.st_gid)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: sync_asterisk_pstn_runtime.py RUNTIME_DIR TEMPLATE", file=sys.stderr)
        return 2
    try:
        sync_runtime(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as error:
        print(f"could not prepare Nateks PSTN runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
