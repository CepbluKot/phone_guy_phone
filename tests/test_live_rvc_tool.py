import asyncio
import importlib.util
import json
from pathlib import Path
import wave

import numpy as np


TOOL_PATH = Path(__file__).with_name("live-rvc.py")
SPEC = importlib.util.spec_from_file_location("live_rvc", TOOL_PATH)
live_rvc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_rvc)


class Clock:
    def __init__(self):
        self.value = 100.0
        self.deadlines = []

    def now(self):
        return self.value

    async def sleep_until(self, deadline):
        self.deadlines.append(deadline)
        self.value = max(self.value, deadline)
        await asyncio.sleep(0)


class StreamingSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.frames = []

    async def send(self, message):
        if isinstance(message, str):
            control = json.loads(message)
            if control["type"] == "start":
                await self.incoming.put(json.dumps({
                    "type": "ready", "version": 1, "sampleRate": 48000,
                    "channels": 1, "sampleFormat": "s16le", "frameBytes": 1920,
                    "outputSamples": 96000,
                }))
            elif control["type"] == "stop":
                await self.incoming.put(json.dumps({"type": "stopped"}))
            return
        self.frames.append(message)
        count = len(self.frames)
        if count >= 105 and (count - 105) % 100 == 0:
            hop = (count - 105) // 100
            await self.incoming.put(json.dumps({
                "type": "metrics", "outputStart": hop * 96000,
                "outputSamples": 96000, "consumedSamples": (hop + 1) * 96000,
                "processingMs": 500.0,
            }))
            pcm = np.full(96000, 1024 + hop, dtype="<i2")
            await self.incoming.put(pcm.tobytes())

    async def recv(self):
        return await self.incoming.get()


def test_load_timeline_uses_both_approved_24khz_samples(tmp_path):
    t = np.arange(2400) / 24000
    for name, frequency in (("ru.wav", 220), ("en.wav", 330)):
        pcm = np.rint(np.sin(2 * np.pi * frequency * t) * 16000).astype("<i2")
        with wave.open(str(tmp_path / name), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(24000)
            target.writeframes(pcm.tobytes())

    pcm, sources = live_rvc.load_timeline(tmp_path)

    assert sources == ["ru.wav", "en.wav"]
    assert pcm.dtype == np.dtype("<i2")
    assert pcm.size == 9600 + 2 * 12000  # two doubled clips plus 250 ms silence after each
    assert np.max(np.abs(pcm[:4800])) > 1000
    assert np.max(np.abs(pcm[16800:21600])) > 1000


def test_paced_session_sends_exact_frames_and_checks_sample_timeline():
    async def scenario():
        socket = StreamingSocket()
        clock = Clock()
        source = np.arange(3000, dtype="<i2")

        result = await live_rvc.stream_session(
            socket, source, audio_seconds=4, now=clock.now,
            sleep_until=clock.sleep_until,
        )

        assert len(socket.frames) == 205
        assert all(len(frame) == 1920 for frame in socket.frames)
        assert clock.deadlines[0] == 100.0
        assert clock.deadlines[-1] == 104.08
        assert result["framesSent"] == 205
        assert result["hopsReceived"] == 2
        assert result["outputSamples"] == 192000
        assert result["processingRtfP95"] == 0.25
        assert result["finitePcm"] is True
        assert result["nonSilentHops"] == 2
        assert result["queue"]["finalOutstandingHops"] == 0
        assert result["output"].shape == (192000,)

    asyncio.run(scenario())


def test_paced_session_rejects_a_gap_in_output_metadata():
    class GapSocket(StreamingSocket):
        async def send(self, message):
            await super().send(message)
            if len(self.frames) == 105:
                queued = await self.incoming.get()
                pcm = await self.incoming.get()
                metadata = json.loads(queued)
                metadata["outputStart"] = 960
                await self.incoming.put(json.dumps(metadata))
                await self.incoming.put(pcm)

    async def scenario():
        clock = Clock()
        with np.testing.assert_raises_regex(AssertionError, "outputStart"):
            await live_rvc.stream_session(
                GapSocket(), np.ones(960, dtype="<i2"), audio_seconds=2,
                now=clock.now, sleep_until=clock.sleep_until,
            )

    asyncio.run(scenario())


def test_runtime_sample_parser_records_memory_queue_gpu_and_restarts():
    output = """MemoryCurrent=1557135360
MemoryPeak=1577058304
NRestarts=0
---health---
{"status":"ready","active":true,"running":true,"queuedWindows":1}
---gpu---
1047
512
"""

    sample = live_rvc.parse_runtime_sample(output)

    assert sample == {
        "memoryCurrentBytes": 1557135360,
        "memoryPeakBytes": 1577058304,
        "serviceRestarts": 0,
        "queuedWindows": 1,
        "workerRunning": True,
        "gpuProcessMemoryMiB": 1559,
    }
