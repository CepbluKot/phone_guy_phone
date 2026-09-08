#!/usr/bin/env python3
"""Acceptance probe for the isolated FCPE Phone Guy canary.

It exercises only the public streaming contract: 20-ms PCM16 frames in,
ordered 0.3-second FCPE blocks out.  The generated sine source makes the
probe self-contained and guarantees no user recording is written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import time
import urllib.request

import numpy as np


SAMPLE_RATE = 48_000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
BLOCK_SAMPLES = 14_400
FRAMES_PER_BLOCK = BLOCK_SAMPLES // FRAME_SAMPLES
START = {
    "type": "start", "version": 1, "sampleRate": SAMPLE_RATE,
    "channels": 1, "sampleFormat": "s16le",
}
EXPECTED_READY = {
    "type": "ready", "version": 1, "sampleRate": SAMPLE_RATE,
    "channels": 1, "sampleFormat": "s16le", "blockSamples": BLOCK_SAMPLES,
    "blockSeconds": 0.3, "extraSeconds": 1.5,
    "f0method": "fcpe", "variant": "v2-fcpe",
}


def validate_ready(message: dict) -> None:
    for key, expected in EXPECTED_READY.items():
        assert message.get(key) == expected, (
            f"ready.{key}={message.get(key)!r}, expected {expected!r}"
        )


def source_frame(number: int) -> bytes:
    positions = np.arange(FRAME_SAMPLES) + number * FRAME_SAMPLES
    pcm = np.rint(np.sin(2 * np.pi * 220 * positions / SAMPLE_RATE) * 12_000)
    return pcm.astype("<i2").tobytes()


async def _sleep_until(deadline: float) -> None:
    await asyncio.sleep(max(0.0, deadline - time.monotonic()))


async def _ready(socket) -> dict:
    await socket.send(json.dumps(START, separators=(",", ":")))
    message = json.loads(await asyncio.wait_for(socket.recv(), timeout=95))
    if message.get("type") == "warming":
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=95))
    validate_ready(message)
    return message


def _p95(values: list[float]) -> float:
    return float(np.percentile(values, 95)) if values else 0.0


async def stream_session(socket, source: np.ndarray, blocks: int, *, now=time.monotonic,
                         sleep_until=_sleep_until) -> dict:
    """Send capture-paced frames and require a contiguous FCPE output timeline."""
    if blocks < 3:
        raise ValueError("blocks must be at least three")
    if source.dtype != np.dtype("<i2") or not source.size:
        raise ValueError("source must be non-empty little-endian PCM16")

    await _ready(socket)
    begun = now()
    processing = []
    output = []
    drift_ms = []

    async def sender():
        for number in range(blocks * FRAMES_PER_BLOCK):
            deadline = begun + number * 0.020
            await sleep_until(deadline)
            drift_ms.append((now() - deadline) * 1000)
            offset = (number * FRAME_SAMPLES) % source.size
            pcm = np.resize(source[offset:], FRAME_SAMPLES).astype("<i2", copy=False)
            await socket.send(pcm.tobytes())

    async def receiver():
        for block in range(1, blocks + 1):
            while True:
                raw = await asyncio.wait_for(socket.recv(), timeout=20)
                assert isinstance(raw, str), f"expected metrics for block {block}"
                metadata = json.loads(raw)
                if metadata.get("type") == "autoTranspose":
                    continue
                assert metadata.get("type") == "metrics", metadata
                break
            assert metadata.get("consumedSamples") == block * BLOCK_SAMPLES, metadata
            assert metadata.get("outputSamples") == BLOCK_SAMPLES, metadata
            processing_ms = metadata.get("processingMs")
            assert isinstance(processing_ms, (float, int)) and 0 <= processing_ms < 10_000
            raw_pcm = await asyncio.wait_for(socket.recv(), timeout=20)
            assert isinstance(raw_pcm, bytes) and len(raw_pcm) == BLOCK_SAMPLES * 2
            pcm = np.frombuffer(raw_pcm, dtype="<i2").copy()
            assert np.isfinite(pcm.astype(np.float32)).all()
            processing.append(float(processing_ms))
            output.append(pcm)

    await asyncio.gather(sender(), receiver())
    await socket.send(json.dumps({"type": "stop"}))
    stopped = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
    assert stopped.get("type") == "stopped", stopped

    combined = np.concatenate(output)
    result = {
        "framesSent": blocks * FRAMES_PER_BLOCK,
        "blocksReceived": len(output),
        "outputSamples": int(combined.size),
        "outputSeconds": combined.size / SAMPLE_RATE,
        "nonSilentBlocks": int(sum(
            np.sqrt(np.mean(np.square(block.astype(np.float64) / 32768.0))) > 0.001
            for block in output
        )),
        "processingMsP95": _p95(processing),
        "processingRtfP95": _p95(processing) / (BLOCK_SAMPLES / SAMPLE_RATE * 1000),
        "sendDriftMsP95": _p95(drift_ms),
    }
    assert result["nonSilentBlocks"] == blocks, result
    assert result["processingRtfP95"] < 1.0, result
    assert result["sendDriftMsP95"] < 100, result
    return result


def _health(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
        body = json.load(response)
    assert body.get("status") == "ready", body
    assert set(body.get("variants", {})) == {"v2-fcpe"}, body
    return body


async def main(args) -> dict:
    from websockets.asyncio.client import connect

    health = None if args.skip_health else await asyncio.to_thread(_health, args.health_url)
    source = np.arange(FRAME_SAMPLES, dtype="<i2")
    async with connect(args.url, origin=args.origin, max_size=BLOCK_SAMPLES * 2,
                       max_queue=4, compression=None, proxy=None) as socket:
        stream = await stream_session(socket, source, args.blocks)
    return {
        "schemaVersion": 1,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "health": health,
        "stream": stream,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="wss://voice-claude.lan.awesomeio.ru/ws/rvc")
    parser.add_argument("--origin", default="https://voice-claude.lan.awesomeio.ru")
    parser.add_argument("--health-url", default="https://voice-claude.lan.awesomeio.ru/healthz")
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--skip-health", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(parse_args())), indent=2, sort_keys=True))
