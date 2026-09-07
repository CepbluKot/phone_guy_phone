"""Experimental multi-session streaming server built on infer/rtrvc.py
(rt_chunks.RtFramer/RtStitcher + rt_engine.RtEngine) -- the "best result"
engine identified in docs/LATENCY_RESEARCH_SONNET_2026-09-07.md, wired up
for real use rather than a one-off benchmark script.

Deliberately separate from rvc_service/server.py (the production offline-
pipeline service): this listens on its own port, is started by hand for
demo/testing, and is not a systemd unit. It supports several *simultaneous*
WebSocket sessions (not server.py's single-active-session lock) sharing one
GPU-resident engine: a global lock serializes the actual model calls in
first-come-first-served order, and cache_pitch/cache_pitchf are zeroed
whenever the engine switches from serving one session to another, so one
caller's pitch history never bleeds into another's (see
experiments/latency-sonnet/scripts/rt_restart_check.py for why that matters).

Also serves the demo static files (web-rt/) on the same port so the whole
thing is reachable through a single SSH-forwarded port.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import itertools
import json
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .rt_chunks import FRAME_BYTES, RtFramer, RtStitcher, SAMPLE_RATE

BLOCK_S = 0.3
EXTRA_S = 1.5
CROSSFADE_S = 0.05
SEARCH_S = 0.02
MAX_SESSIONS = 4
STALL_SECONDS = 15.0

_ids = itertools.count(1)


class SessionError(Exception):
    pass


class Session:
    def __init__(self, socket: WebSocket, state):
        self.id = next(_ids)
        self.socket = socket
        self.state = state
        self.framer = RtFramer(block_s=BLOCK_S, extra_s=EXTRA_S, crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
        self.stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=BLOCK_S, crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self.accept_audio = False
        self.progress = time.monotonic()
        self.consumed = 0

    async def receive(self):
        try:
            while True:
                message = await self.socket.receive()
                if message["type"] == "websocket.disconnect":
                    return "disconnected"
                if message.get("text") is not None:
                    try:
                        control = json.loads(message["text"])
                    except (ValueError, TypeError):
                        raise SessionError("invalid_frame") from None
                    if isinstance(control, dict) and control.get("type") == "stop":
                        return "stopped"
                    raise SessionError("invalid_frame")
                frame = message.get("bytes")
                if not self.accept_audio or frame is None or len(frame) != FRAME_BYTES:
                    raise SessionError("invalid_frame")
                window = self.framer.push(frame)
                self.progress = time.monotonic()
                if window is not None:
                    self.consumed += self.framer.block_48k
                    try:
                        self.queue.put_nowait((window, self.consumed))
                    except asyncio.QueueFull:
                        raise SessionError("overloaded") from None
        finally:
            self.accept_audio = False

    async def convert(self):
        engine = self.state.engine
        while True:
            window_16k, consumed = await self.queue.get()
            begun = time.perf_counter()
            async with self.state.gpu_lock:
                if self.state.last_session_id != self.id:
                    engine.reset_pitch_cache()
                    self.state.last_session_id = self.id
                loop = asyncio.get_running_loop()
                out_np = await loop.run_in_executor(
                    self.state.executor, engine.convert_block_48k,
                    window_16k, self.framer.block_16k, self.framer.skip_head_frames,
                    self.framer.return_length_frames,
                )
            pcm = self.stitcher.render(out_np)
            metadata = {
                "type": "metrics", "consumedSamples": consumed,
                "outputSamples": self.framer.block_48k,
                "processingMs": round((time.perf_counter() - begun) * 1000, 3),
            }
            if not self.accept_audio:
                return
            await self.socket.send_json(metadata)
            await self.socket.send_bytes(pcm)
            self.progress = time.monotonic()


def create_app(engine_factory):
    @asynccontextmanager
    async def lifespan(app):
        state = app.state
        from concurrent.futures import ThreadPoolExecutor

        state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rtrvc")
        state.gpu_lock = asyncio.Lock()
        state.last_session_id = None
        state.active_sessions = 0
        state.status = "warming"
        state.engine = None
        loop = asyncio.get_running_loop()
        def load_and_warm():
            engine = engine_factory()
            # Absorb the one-time RMVPE lazy-load (infer/rtrvc.py loads it on
            # its *first* inference call, not during construction) here, not
            # on whichever session happens to connect first.
            sizing = RtFramer(block_s=BLOCK_S, extra_s=EXTRA_S, crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
            import numpy as np

            zeros = np.zeros(sizing.window_16k, dtype=np.float32)
            engine.convert_block(zeros, sizing.block_16k, sizing.skip_head_frames, sizing.return_length_frames)
            return engine

        state.loading = loop.run_in_executor(state.executor, load_and_warm)
        try:
            state.engine = await state.loading
            state.status = "ready"
        except Exception:
            state.status = "model_unavailable"
        try:
            yield
        finally:
            state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse(
            {"status": app.state.status, "activeSessions": app.state.active_sessions,
             "maxSessions": MAX_SESSIONS, "blockS": BLOCK_S, "extraS": EXTRA_S},
            status_code=200 if app.state.status == "ready" else 503,
        )

    @app.websocket("/ws/rvc")
    async def websocket(socket: WebSocket):
        await socket.accept()
        if app.state.status == "warming":
            await socket.send_json({"type": "warming", "timeoutSeconds": 60})
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(app.state.loading), 60)
        if getattr(app.state, "engine", None) is None:
            await socket.send_json({"type": "error", "code": "model_unavailable"})
            await socket.close()
            return
        if app.state.active_sessions >= MAX_SESSIONS:
            await socket.send_json({"type": "error", "code": "busy",
                                     "message": f"max {MAX_SESSIONS} concurrent demo sessions"})
            await socket.close(code=1013)
            return

        app.state.active_sessions += 1
        session = Session(socket, app.state)
        tasks = []
        try:
            opening = await asyncio.wait_for(socket.receive(), STALL_SECONDS)
            if opening["type"] == "websocket.disconnect":
                return
            start = json.loads(opening.get("text") or "{}")
            if (not isinstance(start, dict) or start.get("type") != "start"
                    or start.get("sampleRate") != SAMPLE_RATE):
                raise SessionError("invalid_start")

            receiver = asyncio.create_task(session.receive())
            tasks.append(receiver)
            await socket.send_json({
                "type": "ready", "version": 1, "sampleRate": SAMPLE_RATE,
                "channels": 1, "sampleFormat": "s16le",
                "blockSamples": session.framer.block_48k,
                "blockSeconds": BLOCK_S, "extraSeconds": EXTRA_S,
                "sessionId": session.id,
            })
            session.accept_audio = True
            converter = asyncio.create_task(session.convert())
            tasks.append(converter)

            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                outcome = receiver.result()
                converter.cancel()
                if outcome == "stopped":
                    await socket.send_json({"type": "stopped"})
            else:
                for task in done:
                    task.result()
        except SessionError as exc:
            with suppress(Exception):
                await socket.send_json({"type": "error", "code": str(exc)})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            session.accept_audio = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if app.state.last_session_id == session.id:
                app.state.last_session_id = None
            app.state.active_sessions -= 1
            with suppress(RuntimeError):
                await socket.close()

    static_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "web-rt"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def _default_engine():
    from .rt_engine import RtEngine

    return RtEngine()


app = create_app(_default_engine)
