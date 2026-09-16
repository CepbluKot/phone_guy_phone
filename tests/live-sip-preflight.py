#!/usr/bin/env python3
"""Run two authenticated SIP callers through the deployed Phone Guy path.

Run this only on VM209 as root.  Credentials never leave the active runtime;
the script prints metrics and status only, and removes its temporary files.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen


ASTERISK = "voice-conference-asterisk-1"
CONTROLLER = "voice-conference-controller-1"


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def inspect_container():
    value = json.loads(run("docker", "inspect", ASTERISK).stdout)[0]
    pjsip = next(
        mount["Source"] for mount in value["Mounts"]
        if mount["Destination"] == "/etc/asterisk/pjsip.conf"
    )
    return value["State"]["Pid"], Path(pjsip).parent


def health():
    with urlopen("http://127.0.0.1:8090/healthz", timeout=3) as response:
        return json.load(response)


def asterisk(command):
    return run("docker", "exec", ASTERISK, "asterisk", "-rx", command).stdout


def wait_process(process, timeout):
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return 124


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("live SIP preflight must run as root on VM209")
    if not args.scenario.is_file():
        raise SystemExit("SIPp scenario not found")
    for command in ("docker", "nsenter", "sipp"):
        run("sh", "-c", f"command -v {command}")

    started = datetime.now(timezone.utc).isoformat()
    pid, runtime = inspect_container()
    samples = []
    with tempfile.TemporaryDirectory(prefix="phoneguy-sip-") as temporary:
        temporary = Path(temporary)
        commands = []
        for index, extension in enumerate(("1983", "1987")):
            password = (runtime / f"sip-{extension}-password").read_text().strip()
            if not password:
                raise SystemExit(f"empty credential for {extension}")
            injection = temporary / f"{extension}.csv"
            injection.write_text(f"SEQUENTIAL\n{extension};{password}\n")
            injection.chmod(0o600)
            commands.append([
                "nsenter", "-t", str(pid), "-n", "sipp", "127.0.0.1:5060",
                "-sf", str(args.scenario), "-inf", str(injection),
                "-i", "127.0.0.1", "-p", str(5093 + index * 4),
                "-m", "1", "-r", "1", "-nostdin",
            ])

        callers = [subprocess.Popen(commands[0], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)]
        time.sleep(1)
        callers.append(subprocess.Popen(commands[1], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and any(p.poll() is None for p in callers):
            samples.append(health())
            time.sleep(0.5)
        exits = [wait_process(process, 12) for process in callers]

    cleanup_deadline = time.monotonic() + 10
    while time.monotonic() < cleanup_deadline:
        channels = [line for line in asterisk("core show channels concise").splitlines()
                    if line.strip()]
        bridges = asterisk("bridge show all")
        final_health = health()
        if not channels and "phoneguy-source-" not in bridges and not final_health["active"]:
            break
        time.sleep(0.5)
    else:
        raise SystemExit("SIP resources did not return to idle")

    logs = run("docker", "logs", "--since", started, CONTROLLER).stdout
    error_markers = ("Traceback", "Task exception was never retrieved", "Rejected SIP channel")
    result = {
        "sip1983Exit": exits[0],
        "sip1987Exit": exits[1],
        "rvcActiveObserved": any(item.get("active") is True for item in samples),
        "rvcInferenceObserved": any(item.get("running") is True for item in samples),
        "channelsAfter": len(channels),
        "privateBridgeAfter": "phoneguy-source-" in bridges,
        "rvcAfter": final_health,
        "controllerErrors": [marker for marker in error_markers if marker in logs],
    }
    print(json.dumps(result, sort_keys=True))
    if exits != [0, 0] or not result["rvcInferenceObserved"] or result["controllerErrors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
