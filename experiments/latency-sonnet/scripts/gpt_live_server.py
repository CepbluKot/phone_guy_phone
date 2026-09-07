"""Single-model, bounded, memory-only streaming WebSocket service.

Isolated read-only-derived copy of research/latency-optimization's
rvc_service/server.py (byte-identical to the deployed production release
at copy time -- verified via md5sum -- except for two deliberate changes:
ALLOWED_ORIGINS, and the auto-transpose measurement/application described
below). Run as its own process on its own port on VM209 (see
docs/RT_DEMO_SONNET_2026-09-07.md's "GPT live" section), never touching
voice-rvc.service or its port. Exists so
https://voice-claude.lan.awesomeio.ru/gpt-live/ can stream live mic audio
through GPT's actual LOW_LATENCY_PROFILE code, the same way our own
/v2-fcpe/ does for ours -- the offline experiments/latency-sonnet/scripts/
run_gpt_variant.py batch driver is a separate, unrelated use of the same
upstream engine.py/chunks.py.

Auto-transpose: same idea as our own Session (rt_server.py) and
/api/compare's ?transpose=auto -- a Session measures the median F0 of its
first couple seconds of mic audio (pitch.py, deployed alongside this file)
and picks the semitone shift toward TARGET_MEDIAN_F0_HZ. Their Engine has
no transpose parameter at all (engine.py's _convert_with_model hardcodes
f0_up_key=0 in the infer/vc/pipeline.py call) -- applied the same
monkeypatch trick as run_gpt_variant.py's _apply_transpose, overriding
pipeline.pipeline()'s 6th positional argument, except here it's re-applied
per block (see Session.convert) since a session's own measured value can
change from None to a real number mid-session, and reset for whichever
session runs next -- unlike the batch driver, this process serves more
than one session over its lifetime, just never two at once
(app.state.active already enforces that).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
import json
import math
import time

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .chunks import (
    DEFAULT_PROFILE,
    LOW_LATENCY_PROFILE,
    FRAME_BYTES,
    SAMPLE_RATE,
    Chunker,
    RvcProfile,
)
from .pitch import estimate_median_f0


STARTUP_SECONDS = 90.0
STALL_SECONDS = 10.0
# Same target as rt_server.py's TARGET_MEDIAN_F0_HZ (measured from the same
# Voicemod reference clip) so GPT's live page gets the identical correction
# our own live page would compute for the same voice.
TARGET_MEDIAN_F0_HZ = 110.8
AUTO_TRANSPOSE_LIMIT = 12
AUTO_TRANSPOSE_WARMUP_SAMPLES = SAMPLE_RATE * 2
ALLOWED_ORIGINS = {"https://voice.lan.awesomeio.ru",
                   "https://vm-voice-1.lan.awesomeio.ru",
                   # The one deliberate change from their real server.py:
                   # this isolated copy is reached from our own demo page.
                   "https://voice-claude.lan.awesomeio.ru"}
MESSAGES = {
    "busy": "Другой сеанс ещё работает. Остановите его и повторите подключение.",
    "model_unavailable": "Модель недоступна. Проверьте состояние RVC-сервиса и повторите запуск.",
    "invalid_start": "Нужен поддерживаемый протокол: 48000 Гц, моно, PCM16 s16le.",
    "invalid_frame": "Неверный пакет звука. Перезапустите сеанс; ожидается 1920 байт PCM16.",
    "overloaded": "Обработка не успевает за звуком. Остановите сеанс и повторите запуск.",
    "stalled": "Нет прогресса более 10 секунд. Проверьте соединение и повторите запуск.",
}


class SessionError(Exception):
    pass


def _apply_transpose(engine, original_pipeline_call, semitones: float) -> None:
    """Rewraps engine._vc.pipeline.pipeline around the *saved original*
    bound method every time (not whatever it currently is), so repeated
    calls across many sessions over this process's lifetime never chain
    wrappers -- see create_app's initialize() for where the original gets
    saved once, right after the engine loads."""
    def patched(*a, **kw):
        a = list(a)
        if len(a) > 5:
            a[5] = semitones
        return original_pipeline_call(*a, **kw)

    engine._vc.pipeline.pipeline = patched


async def _drain(future):
    """Cancellation of a waiter must never stand in for a finished GPU call."""
    with anyio.CancelScope(shield=True):
        while not future.done():
            try:
                await asyncio.shield(asyncio.wrap_future(future))
            except asyncio.CancelledError:
                continue
            except Exception:
                break
    # Retrieve the result/exception and then let the caller discard its reference.
    with suppress(Exception):
        future.result()


async def _complete_cleanup(cleanup):
    """Keep all cleanup steps alive through repeated outer task cancellation."""
    with anyio.CancelScope(shield=True):
        task = asyncio.create_task(cleanup)
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError


class Session:
    def __init__(self, socket, state, profile: RvcProfile):
        self.socket = socket
        self.state = state
        self.profile = profile
        self.chunks = Chunker(profile)
        self.queue = asyncio.Queue(maxsize=1)
        self.inflight = None
        self.sender = None
        self.accept_audio = False
        self.progress = time.monotonic()
        self.output_start = 0
        self.consumed = 0
        self.transpose = None
        self._pitch_frames = []
        self._pitch_samples = 0

    async def _measure_auto_transpose(self, frame: bytes) -> None:
        import numpy as np

        self._pitch_frames.append(np.frombuffer(frame, dtype="<i2"))
        self._pitch_samples += FRAME_BYTES // 2
        if self._pitch_samples < AUTO_TRANSPOSE_WARMUP_SAMPLES:
            return
        audio = np.concatenate(self._pitch_frames).astype(np.float32) / 32768.0
        self._pitch_frames = []
        median_f0 = estimate_median_f0(audio, SAMPLE_RATE)
        self.transpose = 0.0 if median_f0 is None else max(
            -AUTO_TRANSPOSE_LIMIT, min(AUTO_TRANSPOSE_LIMIT,
            12 * math.log2(TARGET_MEDIAN_F0_HZ / median_f0)),
        )
        with suppress(Exception):
            await self.socket.send_json({
                "type": "autoTranspose", "measuredHz": median_f0, "semitones": self.transpose,
            })

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
                if self.transpose is None:
                    await self._measure_auto_transpose(frame)
                window = self.chunks.push(frame)
                self.progress = time.monotonic()
                if window is not None:
                    self.consumed += self.profile.hop_samples
                    try:
                        self.queue.put_nowait((window, self.consumed))
                    except asyncio.QueueFull:
                        raise SessionError("overloaded") from None
        finally:
            # Invalidate in the receiver's turn, before the supervisor wakes up.
            self.invalidate()

    def invalidate(self):
        self.accept_audio = False
        if self.sender is not None:
            self.sender.cancel()

    async def convert(self):
        while True:
            window, consumed = await self.queue.get()
            begun = time.perf_counter()
            # Only one session is ever active at a time (app.state.active),
            # but re-apply every block anyway: cheap, and it means a fresh
            # session's first block always starts from self.transpose (None
            # -> 0.0) regardless of what the previous session left behind.
            _apply_transpose(self.state.engine, self.state.original_pipeline_call, self.transpose or 0.0)
            self.inflight = self.state.executor.submit(self.state.engine.convert, window)
            try:
                converted = await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(self.inflight)), STALL_SECONDS
                )
                pcm = self.chunks.render(converted)
            except asyncio.TimeoutError:
                raise SessionError("stalled") from None
            except Exception:
                raise SessionError("model_unavailable") from None
            self.inflight = None
            metadata = {"type": "metrics", "outputStart": self.output_start,
                        "outputSamples": self.profile.hop_samples,
                        "consumedSamples": consumed,
                        "processingMs": round((time.perf_counter() - begun) * 1000, 3)}
            # timeout keeps the send in this task. wait_for would create a child
            # send task that could resume before cancellation reaches that child.
            if not self.accept_audio:
                return
            async with asyncio.timeout(STALL_SECONDS):
                await self.socket.send_json(metadata)
            if not self.accept_audio:
                return
            async with asyncio.timeout(STALL_SECONDS):
                await self.socket.send_bytes(pcm)
            self.output_start += self.profile.hop_samples
            self.progress = time.monotonic()
            del converted, pcm, window

    async def watchdog(self):
        while True:
            remaining = STALL_SECONDS - (time.monotonic() - self.progress)
            if remaining <= 0:
                raise SessionError("stalled")
            await asyncio.sleep(remaining)


def _default_engine():
    # Importing the ASGI app on the laptop never loads CUDA or the model.
    from .engine import Engine
    return Engine()


def create_app(engine_factory=_default_engine):
    @asynccontextmanager
    async def lifespan(app):
        state = app.state
        state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rvc")
        state.engine = None
        state.status = "warming"
        state.active = False
        state.session = None
        state.loading = state.executor.submit(engine_factory)

        async def initialize():
            try:
                state.engine = await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(state.loading)), STARTUP_SECONDS
                )
                # Saved once here, not re-read later: _apply_transpose always
                # wraps from this exact original, so many sessions patching
                # it in turn over this process's lifetime never chain
                # wrappers around each other.
                state.original_pipeline_call = state.engine._vc.pipeline.pipeline
                state.status = "ready"
            except Exception:
                state.status = "model_unavailable"

        state.initialization = asyncio.create_task(initialize())
        try:
            yield
        finally:
            state.initialization.cancel()
            with suppress(asyncio.CancelledError):
                await state.initialization
            await _drain(state.loading)
            state.executor.shutdown(wait=True, cancel_futures=True)
            state.engine = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        session = app.state.session
        running = bool(session and session.inflight is not None and not session.inflight.done())
        return JSONResponse({"status": app.state.status, "active": app.state.active,
                             "running": running,
                             "queuedWindows": session.queue.qsize() if session else 0},
                            status_code=200 if app.state.status == "ready" else 503,
                            headers={"Cache-Control": "no-store"})

    async def websocket(socket: WebSocket, profile: RvcProfile):
        if socket.headers.get("origin") not in ALLOWED_ORIGINS:
            await socket.close(code=1008)
            return
        await socket.accept()

        async def error(code):
            with suppress(RuntimeError, WebSocketDisconnect, asyncio.TimeoutError):
                await asyncio.wait_for(socket.send_json(
                    {"type": "error", "code": code, "message": MESSAGES[code]}
                ), STALL_SECONDS)

        if app.state.active:
            await error("busy")
            await socket.close(code=1013)
            return
        app.state.active = True
        session = Session(socket, app.state, profile)
        app.state.session = session
        tasks = []
        try:
            opening = await asyncio.wait_for(socket.receive(), STALL_SECONDS)
            if opening["type"] == "websocket.disconnect":
                return
            try:
                start = json.loads(opening.get("text") or "")
            except (ValueError, TypeError):
                raise SessionError("invalid_start") from None
            if (not isinstance(start, dict) or start.get("type") != "start"
                    or type(start.get("version")) is not int
                    or start["version"] != profile.version
                    or start.get("sampleRate") != SAMPLE_RATE
                    or start.get("channels") != 1 or start.get("sampleFormat") != "s16le"):
                raise SessionError("invalid_start")
            receiver = asyncio.create_task(session.receive())
            tasks.append(receiver)
            if app.state.status == "warming":
                await socket.send_json({"type": "warming", "timeoutSeconds": STARTUP_SECONDS})
                done, _ = await asyncio.wait(
                    [receiver, app.state.initialization], return_when=asyncio.FIRST_COMPLETED
                )
                if receiver in done:
                    if receiver.result() == "stopped":
                        await socket.send_json({"type": "stopped"})
                    return
            if app.state.status != "ready":
                raise SessionError("model_unavailable")
            await socket.send_json({"type": "ready", "version": profile.version,
                                    "sampleRate": SAMPLE_RATE, "channels": 1,
                                    "sampleFormat": "s16le", "frameBytes": FRAME_BYTES,
                                    "outputSamples": profile.hop_samples})
            session.accept_audio = True
            session.progress = time.monotonic()
            session.sender = asyncio.create_task(session.convert())
            tasks.extend([session.sender,
                          asyncio.create_task(session.watchdog())])
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Stop/disconnect takes precedence over a coincident conversion result.
            if receiver in done:
                outcome = receiver.result()
                for task in tasks[1:]:
                    task.cancel()
                if outcome == "stopped":
                    await socket.send_json({"type": "stopped"})
            else:
                for task in done:
                    task.result()
        except SessionError as exc:
            for task in tasks:
                task.cancel()
            await error(str(exc))
        except asyncio.TimeoutError:
            await error("stalled")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.invalidate()

            async def cleanup():
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                while not session.queue.empty():
                    session.queue.get_nowait()
                if session.inflight is not None:
                    await _drain(session.inflight)
                    session.inflight = None
                session.chunks.reset()
                app.state.session = None
                app.state.active = False
                with suppress(RuntimeError, WebSocketDisconnect):
                    await socket.close()

            # The whole sequence, including gather, belongs to a separate task.
            # Repeated cancellation can only interrupt its shielded waiter.
            await _complete_cleanup(cleanup())

    @app.websocket("/ws/rvc")
    async def websocket_v1(socket: WebSocket):
        await websocket(socket, DEFAULT_PROFILE)

    @app.websocket("/ws/rvc-v2")
    async def websocket_v2(socket: WebSocket):
        await websocket(socket, LOW_LATENCY_PROFILE)

    return app


app = create_app()
