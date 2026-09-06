#!/usr/bin/env python3
"""Prove that an occupied real RVC session fails the conference closed.

This tool briefly owns the existing RVC protocol, then asks the public
receive-only conference endpoint to start.  Success means the listener gets
``preparing``, ``error/busy``, ``stopped`` and no media.  It stores no audio.
Run only when no real listener is using the one shared RVC session.
"""

import asyncio
import json
import ssl

from websockets.asyncio.client import connect


ORIGIN = "https://voice.lan.awesomeio.ru"
RVC_URL = "wss://vm-voice-1.lan.awesomeio.ru/ws/rvc"
CONFERENCE_URL = "wss://vm-voice-1.lan.awesomeio.ru/ws/conference"
START = {"type": "start", "version": 1, "sampleRate": 48000,
         "channels": 1, "sampleFormat": "s16le"}


async def receive_json(socket, timeout=30):
    value = await asyncio.wait_for(socket.recv(), timeout)
    if not isinstance(value, str):
        raise AssertionError("expected a control message")
    return json.loads(value)


async def main():
    context = ssl.create_default_context()
    async with connect(RVC_URL, origin=ORIGIN, ssl=context, open_timeout=15) as rvc:
        await rvc.send(json.dumps(START))
        opening = await receive_json(rvc)
        while opening.get("type") == "warming":
            opening = await receive_json(rvc, timeout=100)
        if opening.get("type") != "ready":
            raise AssertionError(f"RVC did not become ready: {opening!r}")
        async with connect(CONFERENCE_URL, origin=ORIGIN, ssl=context,
                           open_timeout=15) as conference:
            await conference.send(json.dumps({"type": "listen", "version": 1}))
            states = [await receive_json(conference) for _ in range(3)]
            expected = [
                {"type": "preparing"},
                {"type": "error", "code": "busy"},
                {"type": "stopped"},
            ]
            if states != expected:
                raise AssertionError(f"conference did not fail closed: {states!r}")
    print(json.dumps({"rvc_busy_rejected": True, "conference_states": states}))


if __name__ == "__main__":
    asyncio.run(main())
