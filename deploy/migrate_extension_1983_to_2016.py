#!/usr/bin/env python3
"""Move the existing Yealink identity from 1983 to 2016 at Go cutover.

The SIP password is copied in memory from the existing auth block and is never
printed or written to a temporary log. The three files are backed up by the
calling deployment script before this migration runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from add_physical_extension_2015 import _atomic_replace


OLD = "1983"
NEW = "2016"
PHONE_MAC = "00:15:65:89:b3:85"
OTHER_EXTENSIONS = {"1987", "1988", "2014", "2015"}
OPTIONAL_EXTENSIONS = {"1234"}


def section(text: str, header: str) -> re.Match[str]:
    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\][^\n]*\n.*?(?=^\[|\Z)")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"expected one {header} PJSIP section")
    return matches[0]


def replace_line(block: str, old: str, new: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(old)}$")
    updated, count = pattern.subn(new, block)
    if count != 1:
        raise ValueError("legacy SIP section does not match the expected handset")
    return updated


def migrate_pjsip(text: str) -> str:
    has_old = bool(re.search(r"(?m)^\[1983(?:-auth)?\]", text))
    has_new = bool(re.search(r"(?m)^\[2016(?:-auth)?\]", text))
    if has_new and not has_old:
        section(text, "2016-auth")
        if len(list(re.finditer(r"(?m)^\[2016\]", text))) != 2:
            raise ValueError("2016 PJSIP runtime is incomplete")
        return text
    if has_new or not has_old:
        raise ValueError("SIP runtime has mixed or missing extension identities")
    auth = section(text, f"{OLD}-auth")
    # endpoint and AOR share the same header; select them by their section body.
    candidates = list(re.finditer(r"(?ms)^\[1983\][^\n]*\n.*?(?=^\[|\Z)", text))
    if len(candidates) != 2:
        raise ValueError("expected endpoint and AOR for 1983")
    endpoint, aor = candidates
    if "(phone-endpoint)" not in endpoint.group(0).splitlines()[0] or "type=aor" not in aor.group(0).splitlines():
        raise ValueError("1983 endpoint or AOR has unexpected structure")
    endpoint_text = replace_line(endpoint.group(0), "[1983](phone-endpoint)", "[2016](phone-endpoint)")
    endpoint_text = replace_line(endpoint_text, "auth=1983-auth", "auth=2016-auth")
    endpoint_text = replace_line(endpoint_text, "aors=1983", "aors=2016")
    auth_text = replace_line(auth.group(0), "[1983-auth]", "[2016-auth]")
    auth_text = replace_line(auth_text, "username=1983", "username=2016")
    if not re.search(r"(?m)^password=(?!__SIP_)[^\r\n]+$", auth_text):
        raise ValueError("1983 SIP password is missing")
    aor_text = replace_line(aor.group(0), "[1983]", "[2016]")
    aor_text = replace_line(aor_text, "contact=sip:1983@192.168.20.134:5062", "contact=sip:2016@192.168.20.134:5062")
    updated = text
    for match, replacement in sorted(((endpoint, endpoint_text), (auth, auth_text), (aor, aor_text)), key=lambda pair: pair[0].start(), reverse=True):
        updated = updated[:match.start()] + replacement + updated[match.end():]
    return updated


def migrate_routes(data: dict) -> dict:
    routes = data.get("extensions")
    if data.get("schemaVersion") != 1 or not isinstance(data.get("revision"), int) or not isinstance(routes, dict):
        raise ValueError("voice route file is invalid")
    if set(routes) in (OTHER_EXTENSIONS | {NEW}, OTHER_EXTENSIONS | {NEW} | OPTIONAL_EXTENSIONS):
        return data
    if set(routes) not in (OTHER_EXTENSIONS | {OLD}, OTHER_EXTENSIONS | {OLD} | OPTIONAL_EXTENSIONS) or NEW in routes or data["revision"] >= 2**64 - 1:
        raise ValueError("voice route inventory is not the expected baseline")
    migrated = dict(data)
    migrated["extensions"] = dict(routes)
    migrated["extensions"][NEW] = migrated["extensions"].pop(OLD)
    migrated["revision"] += 1
    return migrated


def migrate_phonebook(data: dict) -> dict:
    devices = data.get("devices")
    if data.get("schemaVersion") != 1 or not isinstance(data.get("revision"), int) or not isinstance(devices, list):
        raise ValueError("phonebook file is invalid")
    matches = [item for item in devices if item.get("mac", "").lower() == PHONE_MAC]
    if len(matches) == 1 and matches[0].get("extension") == NEW and all(item.get("extension") != OLD for item in devices):
        return data
    if len(matches) != 1 or matches[0].get("extension") != OLD or any(item.get("extension") == NEW for item in devices):
        raise ValueError("Yealink phonebook assignment is not the expected baseline")
    if data["revision"] >= 2**64 - 1:
        raise ValueError("phonebook revision is exhausted")
    migrated = dict(data)
    migrated["devices"] = [dict(item) for item in devices]
    for item in migrated["devices"]:
        if item.get("mac", "").lower() == PHONE_MAC:
            item["extension"] = NEW
            item["label"] = "Yealink T21"
    migrated["revision"] += 1
    return migrated


def migrate_browser_directory(data: dict) -> dict:
    people = data.get("people")
    if data.get("schemaVersion") != 1 or not isinstance(data.get("revision"), int) or not isinstance(people, list):
        raise ValueError("browser directory is invalid")
    old = [person for person in people if person.get("extension") == OLD]
    new = [person for person in people if person.get("extension") == NEW]
    if not old and not new:
        return data
    if len(new) == 1 and not old:
        return data
    if len(old) != 1 or new or data["revision"] >= 2**64 - 1:
        raise ValueError("browser directory assignment is not the expected baseline")
    migrated = dict(data)
    migrated["people"] = [dict(person) for person in people]
    for person in migrated["people"]:
        if person.get("extension") == OLD:
            person["extension"] = NEW
    migrated["revision"] += 1
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_pjsip", type=Path)
    parser.add_argument("voice_routes", type=Path)
    parser.add_argument("phonebook", type=Path)
    parser.add_argument("browser_directory", type=Path)
    args = parser.parse_args()
    try:
        pjsip = migrate_pjsip(args.runtime_pjsip.read_text())
        routes = migrate_routes(json.loads(args.voice_routes.read_text()))
        phonebook = migrate_phonebook(json.loads(args.phonebook.read_text()))
        directory = migrate_browser_directory(json.loads(args.browser_directory.read_text()))
        if pjsip != args.runtime_pjsip.read_text():
            _atomic_replace(args.runtime_pjsip, pjsip.encode())
        if routes != json.loads(args.voice_routes.read_text()):
            _atomic_replace(args.voice_routes, (json.dumps(routes, indent=2) + "\n").encode())
        if phonebook != json.loads(args.phonebook.read_text()):
            _atomic_replace(args.phonebook, (json.dumps(phonebook, indent=2) + "\n").encode())
        if directory != json.loads(args.browser_directory.read_text()):
            _atomic_replace(args.browser_directory, (json.dumps(directory, indent=2) + "\n").encode())
    except (OSError, ValueError, json.JSONDecodeError):
        print("1983 to 2016 migration failed", file=sys.stderr)
        return 1
    print("SIP_EXTENSION_MIGRATED old=1983 new=2016")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
