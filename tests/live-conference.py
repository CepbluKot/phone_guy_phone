#!/usr/bin/env python3
"""Receive-only smoke/soak probe for the deployed Asterisk conference.

The probe deliberately keeps only counters and timing samples: it never writes
the synthetic conference PCM to disk and has no microphone or audio-input path.
Run it only while the shared RVC worker is otherwise idle.
"""

import argparse
import asyncio
import json
import math
import os
import ssl
import struct
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


URL = "wss://vm-voice-1.lan.awesomeio.ru/ws/conference"
ORIGIN = "https://voice.lan.awesomeio.ru"
FRAME_BYTES = 1920


def pcm_is_finite(frame):
    if len(frame) != FRAME_BYTES:
        raise AssertionError(f"unexpected PCM frame length {len(frame)}")
    samples = struct.unpack("<960h", frame)
    # PCM16 values are inherently finite; retain an explicit check so a future
    # float conversion cannot silently weaken the acceptance contract.
    if not all(math.isfinite(sample) for sample in samples):
        raise AssertionError("non-finite conference PCM")
    return any(samples)


async def read_status(socket):
    message = await asyncio.wait_for(socket.recv(), 30)
    if not isinstance(message, str):
        raise AssertionError("expected conference control message")
    return json.loads(message)


async def collect(url, origin, seconds):
    ssl_context = ssl.create_default_context() if url.startswith("wss:") else None
    async with connect(url, origin=origin, ssl=ssl_context, max_size=4096,
                       max_queue=8, open_timeout=15, close_timeout=10) as socket:
        await socket.send(json.dumps({"type": "listen", "version": 1}))
        if await read_status(socket) != {"type": "preparing"}:
            raise AssertionError("missing preparing state")
        ready = await read_status(socket)
        expected = {
            "type": "ready", "version": 1, "sampleRate": 48000,
            "channels": 1, "sampleFormat": "s16le", "frameBytes": FRAME_BYTES,
        }
        if ready != expected:
            raise AssertionError(f"invalid ready metadata: {ready!r}")
        began = time.monotonic()
        deadline = began + seconds
        frames = nonzero = 0
        gaps = []
        previous = began
        while time.monotonic() < deadline:
            item = await asyncio.wait_for(socket.recv(), 15)
            now = time.monotonic()
            if not isinstance(item, bytes):
                raise AssertionError(f"unexpected conference control during media: {item!r}")
            sounding = pcm_is_finite(item)
            frames += 1
            nonzero += int(sounding)
            gaps.append(now - previous)
            previous = now
        await socket.send(json.dumps({"type": "stop"}))
        while True:
            status = await read_status(socket)
            if status == {"type": "stopped"}:
                break
            if status.get("type") == "error":
                raise AssertionError(f"conference ended with {status!r}")
        if nonzero == 0:
            raise AssertionError("conference produced no sounding PCM frames")
        return {"frames": frames, "nonzero_frames": nonzero,
                "wall_seconds": round(time.monotonic() - began, 3),
                "max_gap_ms": round(max(gaps, default=0) * 1000, 3)}


async def wrong_origin_is_rejected(url):
    ssl_context = ssl.create_default_context() if url.startswith("wss:") else None
    try:
        async with connect(url, origin="https://wrong.invalid", ssl=ssl_context,
                           open_timeout=15):
            raise AssertionError("wrong Origin unexpectedly connected")
    except (ConnectionClosed, InvalidStatus) as error:
        if not is_wrong_origin_rejection(error):
            raise AssertionError(f"wrong Origin rejection was {error!r}") from error


def is_wrong_origin_rejection(error):
    return (
        isinstance(error, ConnectionClosed) and error.code == 1008
    ) or (
        isinstance(error, InvalidStatus) and error.response.status_code == 403
    )


async def main_async(args):
    await wrong_origin_is_rejected(args.url)
    first = await collect(args.url, args.origin, args.seconds)
    # Last-listener cleanup is awaited by the server; the cooldown is intentional.
    await asyncio.sleep(6)
    reconnect = await collect(args.url, args.origin, min(args.seconds, 10))
    result = {"main": first, "reconnect": reconnect, "origin_rejected": True}
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("CONFERENCE_URL", URL))
    parser.add_argument("--origin", default=os.environ.get("CONFERENCE_ORIGIN", ORIGIN))
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if args.seconds < 2:
        parser.error("--seconds must be at least 2")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
