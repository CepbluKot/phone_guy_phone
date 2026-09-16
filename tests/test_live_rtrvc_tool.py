import asyncio
import importlib.util
import json
from pathlib import Path

import numpy as np


TOOL_PATH = Path(__file__).with_name("live-rtrvc.py")
SPEC = importlib.util.spec_from_file_location("live_rtrvc", TOOL_PATH)
live_rtrvc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_rtrvc)


class Clock:
    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value

    async def sleep_until(self, deadline):
        self.value = max(self.value, deadline)
        await asyncio.sleep(0)


class FcpeSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.frames = []

    async def send(self, message):
        if isinstance(message, str):
            control = json.loads(message)
            if control["type"] == "start":
                await self.incoming.put(json.dumps({
                    "type": "ready", "version": 1, "sampleRate": 48000,
                    "channels": 1, "sampleFormat": "s16le", "blockSamples": 14400,
                    "blockSeconds": 0.3, "extraSeconds": 1.5,
                    "f0method": "fcpe", "variant": "v2-fcpe", "sessionId": 1,
                }))
            elif control["type"] == "stop":
                await self.incoming.put(json.dumps({"type": "stopped"}))
            return
        self.frames.append(message)
        if len(self.frames) % 15 == 0:
            block = len(self.frames) // 15
            await self.incoming.put(json.dumps({
                "type": "metrics", "consumedSamples": block * 14400,
                "outputSamples": 14400, "processingMs": 91.5,
            }))
            await self.incoming.put(np.full(14400, 2048, dtype="<i2").tobytes())

    async def recv(self):
        return await self.incoming.get()


def test_fcpe_probe_sends_20ms_frames_and_requires_three_ordered_blocks():
    async def scenario():
        socket = FcpeSocket()
        clock = Clock()
        result = await live_rtrvc.stream_session(
            socket, np.arange(960, dtype="<i2"), blocks=3,
            now=clock.now, sleep_until=clock.sleep_until,
        )

        assert len(socket.frames) == 45
        assert all(len(frame) == 1920 for frame in socket.frames)
        assert result["framesSent"] == 45
        assert result["blocksReceived"] == 3
        assert result["outputSamples"] == 43200
        assert result["nonSilentBlocks"] == 3
        assert result["processingRtfP95"] < 1
        json.dumps(result)

    asyncio.run(scenario())


def test_fcpe_ready_contract_rejects_a_non_fcpe_endpoint():
    bad_ready = {
        "type": "ready", "version": 1, "sampleRate": 48000,
        "channels": 1, "sampleFormat": "s16le", "blockSamples": 14400,
        "blockSeconds": 0.3, "extraSeconds": 1.5,
        "f0method": "fcpe", "variant": "v2-rmvpe",
    }

    with np.testing.assert_raises_regex(AssertionError, "ready.variant"):
        live_rtrvc.validate_ready(bad_ready)
