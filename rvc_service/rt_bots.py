"""Listen-only "bot room": a fixed set of prerecorded voices loop forever
through the same shared rtrvc.py engine rt_server.py already runs for live
human sessions, and any number of listener WebSockets receive the mixed,
converted result -- no microphone needed on the listener side at all.

Deliberately reuses rt_server.py's engine/gpu_lock and RtEngine.reset_pitch_cache
(rather than loading a second model instance): experiments/latency-sonnet's
own load test found this GPU sustains ~2 fully-continuous concurrent
sessions before "overloaded" -- so with rt_server.py's human demo sharing
the same GPU, keeping this to 2 looping bots leaves headroom rather than
silently degrading everyone (see docs/RT_DEMO_SONNET_2026-09-07.md).
"""
from __future__ import annotations

import asyncio
import itertools
import math
from pathlib import Path

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from scipy.signal import resample_poly

from .rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE

_room_ids = itertools.count(-1, -1)  # negative, distinct from rt_server.py's per-connection _ids


def _load_frames(path: Path) -> list[bytes]:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        g = math.gcd(SAMPLE_RATE, sr)
        data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
    pcm16 = np.clip(np.rint(data * 32768.0), -32768, 32767).astype("<i2")
    n = pcm16.size // FRAME_SAMPLES
    return [pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes() for i in range(n)]


class Bot:
    def __init__(self, name: str, wav_path: Path):
        self.name = name
        self.wav_path = wav_path
        self.id = next(_room_ids)
        self.framer = RtFramer(block_s=0.3, extra_s=1.5, crossfade_s=0.05, search_s=0.02)
        self.stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=0.3, crossfade_s=0.05, search_s=0.02)
        self.last_block = np.zeros(self.framer.block_48k, dtype="<i2")


class BotRoom:
    def __init__(self, state, wav_paths: list[Path]):
        self.state = state
        self.bots = [Bot(f"bot{i}", p) for i, p in enumerate(wav_paths) if p.exists()]
        self.listeners: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.bots and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        frames_by_bot = {bot.name: _load_frames(bot.wav_path) for bot in self.bots}
        engine = self.state.engine
        indices = {bot.name: 0 for bot in self.bots}
        while True:
            for bot in self.bots:
                frames = frames_by_bot[bot.name]
                frame = frames[indices[bot.name] % len(frames)]
                indices[bot.name] += 1
                window_16k = bot.framer.push(frame)
                if window_16k is not None:
                    async with self.state.gpu_lock:
                        if self.state.last_session_id != bot.id:
                            engine.reset_pitch_cache()
                            self.state.last_session_id = bot.id
                        out_np = await loop.run_in_executor(
                            self.state.executor, engine.convert_block_48k,
                            window_16k, bot.framer.block_16k, bot.framer.skip_head_frames,
                            bot.framer.return_length_frames,
                        )
                    pcm = bot.stitcher.render(out_np)
                    bot.last_block = np.frombuffer(pcm, dtype="<i2")
                    await self._broadcast()
                await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)

    async def _broadcast(self) -> None:
        if not self.listeners:
            return
        mixed = np.zeros(self.bots[0].framer.block_48k, dtype=np.int32)
        for bot in self.bots:
            mixed[: bot.last_block.size] += bot.last_block
        data = np.clip(mixed, -32768, 32767).astype("<i2").tobytes()
        dead = set()
        for socket in self.listeners:
            try:
                await socket.send_bytes(data)
            except Exception:
                dead.add(socket)
        self.listeners -= dead

    async def handle_listener(self, socket: WebSocket) -> None:
        await socket.accept()
        self.listeners.add(socket)
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            pass
        finally:
            self.listeners.discard(socket)
