#!/usr/bin/env python3
"""Synthetic browser publisher -> real 1999 dialplan -> captured caller audio.

Run on VM209 as root while voice-selfmonitor.service is active. The test uses
its own ARI application for a Local caller; it never opens a microphone or
loads RVC. No audio is saved to disk, only RMS and frame counts are reported.
"""

from __future__ import annotations

import asyncio
from array import array
from contextlib import suppress
import json
from pathlib import Path
import sys
import time
import uuid

import httpx
from websockets.asyncio.client import connect

from conference.asterisk import AsteriskRoom
from conference.media import FRAME_BYTES
from .service import ORIGIN


TONE = (b"\x20\x03" * (FRAME_BYTES // 2))


def rms(frame: bytes) -> float:
    if len(frame) != FRAME_BYTES:
        raise ValueError("invalid_test_frame")
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    return (sum((sample / 32768) ** 2 for sample in samples) / len(samples)) ** .5


def credentials():
    values = {}
    for line in Path("/etc/voice-selfmonitor.env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


async def add_when_stasis(room, bridge_id, channel_id):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        try:
            await room.add_to_bridge(bridge_id, channel_id)
            return
        except httpx.HTTPStatusError as error:
            if (error.response.status_code != 422 or
                    error.response.json().get("message") != "Channel not in Stasis application" or
                    asyncio.get_running_loop().time() >= deadline):
                raise
            await asyncio.sleep(.1)


async def check():
    config = credentials()
    room = AsteriskRoom(
        config["SELFMONITOR_ARI_URL"], config["SELFMONITOR_ARI_USERNAME"],
        config["SELFMONITOR_ARI_PASSWORD"], app_name="live-mirror-check",
    )
    collected = []
    call_id = "live-mirror-check-" + uuid.uuid4().hex
    bridge_id = "live-mirror-check-bridge-" + uuid.uuid4().hex
    listener = None
    collector = None

    async with room:
        try:
            await room.create_bridge(bridge_id)
            listener = await room.open_media("listener", receive=True)
            await room.add_to_bridge(bridge_id, listener.channel_id)
            await room.request("POST", "/channels", params={
                "endpoint": "Local/1999@phoneguy-sip", "app": room.app,
                "channelId": call_id, "formats": "slin48",
            })
            await add_when_stasis(room, bridge_id, call_id)

            async def collect():
                while True:
                    frame = await listener.receive_pcm()
                    collected.append((time.monotonic(), rms(frame)))

            collector = asyncio.create_task(collect())
            await asyncio.sleep(1)
            before = time.monotonic()
            async with connect("ws://127.0.0.1:8097/ws/live-mirror", origin=ORIGIN,
                               max_size=FRAME_BYTES) as publisher:
                started = time.monotonic()
                for _ in range(100):
                    await publisher.send(TONE)
                    await asyncio.sleep(.02)
                ended = time.monotonic()
            await asyncio.sleep(1)
            after = time.monotonic()
        finally:
            if collector is not None:
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
            with suppress(Exception):
                await room.hangup_channel(call_id)
            if listener is not None:
                with suppress(Exception):
                    await listener.close()
            with suppress(Exception):
                await room.delete_bridge(bridge_id)

    initial = [level for ts, level in collected if before - .6 <= ts < before]
    sounding = [level for ts, level in collected if started + .2 <= ts < ended]
    final = [level for ts, level in collected if ended + .3 <= ts <= after]
    report = {
        "initialFrames": len(initial), "soundingFrames": len(sounding),
        "finalFrames": len(final),
        "initialMaxRms": round(max(initial, default=0), 4),
        "soundingMaxRms": round(max(sounding, default=0), 4),
        "finalMaxRms": round(max(final, default=0), 4),
    }
    print(json.dumps(report, sort_keys=True))
    if min(map(len, (initial, sounding, final))) < 5:
        raise AssertionError("too_few_captured_frames")
    if max(initial) > .005 or max(sounding) < .01 or max(final) > .005:
        raise AssertionError("silence_audio_silence_failed")


if __name__ == "__main__":
    asyncio.run(check())
