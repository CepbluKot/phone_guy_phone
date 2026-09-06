#!/usr/bin/env python3
"""Private WSS acceptance tool for the Phone Guy RVC service."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import wave

import numpy as np


SAMPLE_RATE = 48_000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
HOP_SAMPLES = 96_000
LOOKAHEAD_FRAMES = 5
START = {"type": "start", "version": 1, "sampleRate": SAMPLE_RATE,
         "channels": 1, "sampleFormat": "s16le"}
EXPECTED_READY = {"version": 1, "sampleRate": SAMPLE_RATE, "channels": 1,
                  "sampleFormat": "s16le", "frameBytes": FRAME_BYTES,
                  "outputSamples": HOP_SAMPLES}


def _read_approved_sample(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24_000):
            raise ValueError(f"{path} must be mono PCM16 at 24000 Hz")
        pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    if not pcm.size:
        raise ValueError(f"{path} is empty")
    # The approved fixtures are exactly 24 kHz. Linear 2x interpolation keeps
    # the tool dependency-free on the laptop and avoids zero-order-hold edges.
    positions = np.arange(pcm.size * 2, dtype=np.float64) / 2.0
    resampled = np.interp(positions, np.arange(pcm.size), pcm.astype(np.float64))
    return np.rint(resampled).clip(-32768, 32767).astype("<i2")


def load_timeline(samples_dir: str | Path) -> tuple[np.ndarray, list[str]]:
    """Load the approved RU and EN fixtures, separated by 250 ms silence."""
    root = Path(samples_dir)
    names = ["ru.wav", "en.wav"]
    silence = np.zeros(SAMPLE_RATE // 4, dtype="<i2")
    parts = []
    for name in names:
        parts.extend((_read_approved_sample(root / name), silence))
    return np.concatenate(parts), names


async def _sleep_until(deadline: float) -> None:
    await asyncio.sleep(max(0.0, deadline - time.monotonic()))


async def _ready(socket) -> dict:
    await socket.send(json.dumps(START, separators=(",", ":")))
    message = json.loads(await asyncio.wait_for(socket.recv(), 95))
    if message.get("type") == "warming":
        message = json.loads(await asyncio.wait_for(socket.recv(), 95))
    assert message.get("type") == "ready", message
    for key, value in EXPECTED_READY.items():
        assert message.get(key) == value, f"ready.{key}={message.get(key)!r}, expected {value!r}"
    return message


def _frame(source: np.ndarray, start: int) -> bytes:
    positions = (np.arange(FRAME_SAMPLES) + start) % source.size
    return source[positions].astype("<i2", copy=False).tobytes()


def _percentile(values: list[float], percentile: float) -> float:
    return float(np.percentile(values, percentile)) if values else 0.0


async def stream_session(
    socket,
    source: np.ndarray,
    audio_seconds: int,
    *,
    now=time.monotonic,
    sleep_until=_sleep_until,
) -> dict:
    """Send capture-paced frames while receiving ordered two-second PCM hops."""
    if audio_seconds <= 0 or audio_seconds % 2:
        raise ValueError("audio_seconds must be a positive multiple of 2")
    if source.dtype != np.dtype("<i2") or not source.size:
        raise ValueError("source must be non-empty little-endian PCM16")

    await _ready(socket)
    expected_hops = audio_seconds // 2
    total_frames = audio_seconds * 50 + LOOKAHEAD_FRAMES
    begun = now()
    send_drifts = []
    processing = []
    arrival_lags = []
    outputs = []
    state = {"received": 0, "maxOutstanding": 0, "maxWriteBufferBytes": 0}

    async def sender():
        for number in range(total_frames):
            deadline = begun + number * 0.020
            await sleep_until(deadline)
            send_drifts.append((now() - deadline) * 1000)
            await socket.send(_frame(source, number * FRAME_SAMPLES))
            available = max(0, 1 + (number + 1 - 105) // 100) if number + 1 >= 105 else 0
            state["maxOutstanding"] = max(
                state["maxOutstanding"], available - state["received"]
            )
            transport = getattr(socket, "transport", None)
            if transport is not None:
                state["maxWriteBufferBytes"] = max(
                    state["maxWriteBufferBytes"], transport.get_write_buffer_size()
                )

    async def receiver():
        for hop in range(expected_hops):
            raw = await asyncio.wait_for(socket.recv(), 10)
            assert isinstance(raw, str), f"expected metrics for hop {hop}"
            metadata = json.loads(raw)
            assert metadata.get("type") == "metrics", metadata
            expected = {
                "outputStart": hop * HOP_SAMPLES,
                "outputSamples": HOP_SAMPLES,
                "consumedSamples": (hop + 1) * HOP_SAMPLES,
            }
            for key, value in expected.items():
                assert metadata.get(key) == value, (
                    f"{key}={metadata.get(key)!r}, expected {value} at hop {hop}"
                )
            processing_ms = metadata.get("processingMs")
            assert isinstance(processing_ms, (int, float)) and 0 <= processing_ms < 10_000
            raw_pcm = await asyncio.wait_for(socket.recv(), 10)
            assert isinstance(raw_pcm, bytes) and len(raw_pcm) == HOP_SAMPLES * 2
            pcm = np.frombuffer(raw_pcm, dtype="<i2").copy()
            assert np.isfinite(pcm.astype(np.float32)).all()
            processing.append(float(processing_ms))
            arrival_lags.append(now() - begun - metadata["consumedSamples"] / SAMPLE_RATE)
            outputs.append(pcm)
            state["received"] += 1

    await asyncio.gather(sender(), receiver())
    await socket.send(json.dumps({"type": "stop"}))
    stopped = json.loads(await asyncio.wait_for(socket.recv(), 10))
    assert stopped.get("type") == "stopped", stopped

    output = np.concatenate(outputs)
    hop_rms = [
        float(np.sqrt(np.mean(np.square(pcm.astype(np.float64) / 32768.0))))
        for pcm in outputs
    ]
    steps = np.abs(np.diff(output.astype(np.float32) / 32768.0))
    boundaries = np.arange(HOP_SAMPLES, output.size, HOP_SAMPLES)
    seam_steps = [float(abs(int(output[pos]) - int(output[pos - 1])) / 32768.0)
                  for pos in boundaries]
    window = min(15, max(1, len(arrival_lags) // 3))
    lag_growth = float(np.mean(arrival_lags[-window:]) - np.mean(arrival_lags[:window]))
    result = {
        "requestedAudioSeconds": audio_seconds,
        "wallSeconds": round(now() - begun, 6),
        "framesSent": total_frames,
        "hopsReceived": len(outputs),
        "outputSamples": int(output.size),
        "outputSeconds": output.size / SAMPLE_RATE,
        "finitePcm": True,
        "nonSilentHops": sum(value > 0.001 for value in hop_rms),
        "outputRms": float(np.sqrt(np.mean(np.square(output.astype(np.float64) / 32768.0)))),
        "processingMsP50": _percentile(processing, 50),
        "processingMsP95": _percentile(processing, 95),
        "processingRtfP95": _percentile(processing, 95) / 2000.0,
        "sendDriftMsP95": _percentile(send_drifts, 95),
        "arrivalLagSecondsMin": min(arrival_lags),
        "arrivalLagSecondsMax": max(arrival_lags),
        "arrivalLagGrowthSeconds": lag_growth,
        "queue": {
            "maxOutstandingHops": state["maxOutstanding"],
            "finalOutstandingHops": expected_hops - state["received"],
            "maxTransportWriteBufferBytes": state["maxWriteBufferBytes"],
        },
        "seams": {
            "count": len(seam_steps),
            "maximumStep": max(seam_steps, default=0.0),
            "stepP99": _percentile(steps.tolist(), 99),
        },
        "output": output,
    }
    assert result["outputRms"] > 0.001, "RVC output is silent"
    assert result["nonSilentHops"] == expected_hops, result
    assert result["processingRtfP95"] < 1.0, result
    assert result["arrivalLagSecondsMax"] < 3.0, result
    assert abs(result["arrivalLagGrowthSeconds"]) < 0.5, result
    assert result["sendDriftMsP95"] < 100.0, result
    assert result["queue"]["finalOutstandingHops"] == 0, result
    return result


def parse_runtime_sample(output: str) -> dict:
    service_text, remainder = output.split("---health---\n", 1)
    health_text, gpu_text = remainder.split("---gpu---\n", 1)
    properties = dict(
        line.split("=", 1) for line in service_text.splitlines() if "=" in line
    )
    health = json.loads(health_text.strip())
    gpu_values = [int(value) for value in re.findall(r"^\s*(\d+)\s*$", gpu_text, re.MULTILINE)]
    return {
        "memoryCurrentBytes": int(properties["MemoryCurrent"]),
        "memoryPeakBytes": int(properties["MemoryPeak"]),
        "serviceRestarts": int(properties["NRestarts"]),
        "queuedWindows": int(health["queuedWindows"]),
        "workerRunning": bool(health["running"]),
        "gpuProcessMemoryMiB": sum(gpu_values),
    }


def _runtime_sample(target: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", target):
        raise ValueError("invalid SSH target")
    command = (
        "sudo systemctl show voice-rvc.service -p MemoryCurrent -p MemoryPeak -p NRestarts --no-pager; "
        "printf '%s\\n' ---health---; curl -fsS http://127.0.0.1:8090/healthz; "
        "printf '\\n%s\\n' ---gpu---; "
        "nvidia-smi --query-compute-apps=used_memory --format=csv,noheader,nounits"
    )
    completed = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", target, command],
        check=True, text=True, capture_output=True, timeout=15,
    )
    return parse_runtime_sample(completed.stdout)


async def _sample_runtime(target: str, done: asyncio.Event, samples: list[dict]) -> None:
    while True:
        samples.append(await asyncio.to_thread(_runtime_sample, target))
        try:
            await asyncio.wait_for(done.wait(), 10)
            return
        except asyncio.TimeoutError:
            pass


def _runtime_summary(samples: list[dict]) -> dict:
    assert samples, "no runtime samples collected"
    result = {
        "samples": len(samples),
        "maxMemoryCurrentBytes": max(item["memoryCurrentBytes"] for item in samples),
        "maxMemoryPeakBytes": max(item["memoryPeakBytes"] for item in samples),
        "maxQueuedWindows": max(item["queuedWindows"] for item in samples),
        "maxGpuProcessMemoryMiB": max(item["gpuProcessMemoryMiB"] for item in samples),
        "serviceRestarts": max(item["serviceRestarts"] for item in samples),
    }
    assert result["maxMemoryPeakBytes"] <= 2700 * 1024 * 1024, result
    assert result["maxQueuedWindows"] <= 1, result
    assert result["serviceRestarts"] == 0, result
    return result


def _write_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm.astype("<i2", copy=False).tobytes())


def _write_artifacts(output_dir: Path, source: np.ndarray, output: np.ndarray) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_count = min(30 * SAMPLE_RATE, output.size)
    input_sample = np.resize(source, sample_count)
    input_path = output_dir / "private-wss-input.wav"
    output_path = output_dir / "private-wss-output.wav"
    seam_path = output_dir / "private-wss-seams.wav"
    _write_wav(input_path, input_sample)
    _write_wav(output_path, output[:sample_count])
    regions = []
    for boundary in range(HOP_SAMPLES, output.size, HOP_SAMPLES):
        regions.append(output[boundary - 2400:boundary + 2400])
        regions.append(np.zeros(2400, dtype="<i2"))
    _write_wav(seam_path, np.concatenate(regions) if regions else output[:4800])
    return {"inputSample": str(input_path), "outputSample": str(output_path),
            "seamRegions": str(seam_path)}


def _check_health(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
        return json.load(response)


async def _main(args) -> dict:
    from websockets.asyncio.client import connect

    source, sources = load_timeline(args.samples_dir)

    def open_socket():
        return connect(args.url, origin=args.origin, max_size=HOP_SAMPLES * 2,
                       max_queue=4, compression=None, proxy=None)

    probes = {}
    if not args.skip_probes:
        async with open_socket() as socket:
            await _ready(socket)
            await socket.send(b"bad")
            error = json.loads(await asyncio.wait_for(socket.recv(), 10))
            assert error.get("type") == "error" and error.get("code") == "invalid_frame", error
            probes["malformedFrame"] = error["code"]
        # A separate clean connection proves ownership was released after the error.
        for attempt in range(20):
            try:
                async with open_socket() as socket:
                    await _ready(socket)
                    await socket.send(json.dumps({"type": "stop"}))
                    assert json.loads(await socket.recv())["type"] == "stopped"
                probes["reconnect"] = "passed"
                break
            except Exception:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.25)

    health = None
    if not args.skip_health:
        health = await asyncio.gather(*[
            asyncio.to_thread(_check_health, url) for url in args.health_url
        ])

    runtime_samples = []
    done = asyncio.Event()
    sampler = None
    if not args.skip_runtime_stats:
        sampler = asyncio.create_task(_sample_runtime(args.ssh_target, done, runtime_samples))
    try:
        async with open_socket() as socket:
            stream = await stream_session(socket, source, args.seconds)
    finally:
        done.set()
        if sampler is not None:
            await sampler

    output = stream.pop("output")
    result = {
        "schemaVersion": 1,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "origin": args.origin,
        "approvedSources": sources,
        "probes": probes,
        "privateHealth": health,
        "stream": stream,
    }
    if runtime_samples:
        result["runtime"] = _runtime_summary(runtime_samples)
    if args.output_dir:
        result["artifacts"] = _write_artifacts(Path(args.output_dir), source, output)
    return result


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="wss://vm-voice-1.lan.awesomeio.ru/ws/rvc")
    parser.add_argument("--origin", default="https://voice.lan.awesomeio.ru")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument(
        "--samples-dir",
        default="/home/oleg/Documents/voice-changer/experiments/phoneguy/samples/input",
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--ssh-target", default="ubuntu@192.168.20.70")
    parser.add_argument("--health-url", action="append", default=[
        "https://voice.lan.awesomeio.ru/healthz",
        "https://vm-voice-1.lan.awesomeio.ru/healthz",
    ])
    parser.add_argument("--skip-probes", action="store_true")
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--skip-runtime-stats", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main(parse_args())), indent=2, sort_keys=True))
