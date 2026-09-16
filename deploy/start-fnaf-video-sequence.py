#!/usr/bin/env python3
"""Start the private, one-shot FNaF callback sequence on the active VM209 release."""

import json
import pathlib
import subprocess
import tempfile


CONTAINER = "voice-conference-asterisk-1"
SOURCE_EXTENSION = "2014"
TARGET_EXTENSION = "1900"
CALL_HOLD_SECONDS = 180


def active_runtime() -> tuple[int, pathlib.Path]:
    info = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER]))[0]
    pjsip = next(
        mount["Source"] for mount in info["Mounts"]
        if mount["Destination"] == "/etc/asterisk/pjsip.conf"
    )
    return info["State"]["Pid"], pathlib.Path(pjsip).parent


def make_scenario(source: str) -> str:
    source = source.replace("sip:600@", f"sip:{TARGET_EXTENSION}@")
    source = source.replace(
        '<recv response="100" optional="true"/>',
        '<recv response="100" optional="true"/>\n  <recv response="180" optional="true"/>',
    )
    begin = source.index("  <nop>")
    end = source.index('  <send retrans="500">', begin)
    hold = "  <nop/>\n  <pause milliseconds=\"9000\"/>\n"
    repeats = CALL_HOLD_SECONDS // 9
    return source[:begin] + hold * repeats + source[end:]


def main() -> None:
    pid, runtime = active_runtime()
    password = (runtime / f"sip-{SOURCE_EXTENSION}-password").read_text().strip()
    if not password:
        raise SystemExit("virtual caller password is unavailable")
    release = pathlib.Path("/opt/voice-conference/releases") / runtime.parent.name
    fixture = release / "tests/fixtures/sipp-auth-conference.xml"
    scenario = make_scenario(fixture.read_text())

    with tempfile.TemporaryDirectory(prefix="fnaf-video-sequence-") as temporary:
        temporary_path = pathlib.Path(temporary)
        credentials = temporary_path / "account.csv"
        credentials.write_text(f"SEQUENTIAL\n{SOURCE_EXTENSION};{password}\n")
        credentials.chmod(0o600)
        xml = temporary_path / "scenario.xml"
        xml.write_text(scenario)
        command = [
            "nsenter", "-t", str(pid), "-n", "sipp", "127.0.0.1:5060",
            "-sf", str(xml), "-inf", str(credentials), "-i", "127.0.0.1", "-p", "5097",
            "-m", "1", "-r", "1", "-nostdin",
        ]
        print("FNaF callback sequence started for 1983.", flush=True)
        completed = subprocess.run(command, check=False)
        # Asterisk sends BYE after the second listener hangs up. SIPp reports
        # that expected teardown as non-zero because this one-shot caller does
        # not own the final BYE. The phone sequence has already completed.
        if completed.returncode not in (0, 1):
            raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
