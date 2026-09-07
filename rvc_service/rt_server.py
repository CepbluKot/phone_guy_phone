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

Exposes several parameter VARIANTS side by side (different block_time/pitch
method combinations) on distinct paths, sharing the one loaded engine --
see docs/LATENCY_VERDICT_SONNET_2026-09-07.md for what each trades off; the
point is to let a human ear pick, not to declare a winner here.

Also serves the demo static files (web-rt/) on the same port.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import itertools
import json
import time

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .rt_chunks import FRAME_BYTES, RtFramer, RtStitcher, SAMPLE_RATE

CROSSFADE_S = 0.05
SEARCH_S = 0.02
MAX_SESSIONS = 4
STALL_SECONDS = 15.0

# Each variant is reachable at /ws/rvc/<key> and web-rt/<key>/index.html.
VARIANTS = {
    "v1-rmvpe": {
        "label": "RMVPE, блок 0.3с (сегодняшний вариант по умолчанию)",
        "block_s": 0.3, "extra_s": 1.5, "f0method": "rmvpe",
    },
    "v2-fcpe": {
        "label": "FCPE, блок 0.3с (~28% быстрее по питчу, качество не проверено на слух)",
        "block_s": 0.3, "extra_s": 1.5, "f0method": "fcpe",
    },
    "v3-rmvpe-fast": {
        "label": "RMVPE, блок 0.15с (агрессивно, самый малый запас RTF)",
        "block_s": 0.15, "extra_s": 2.5, "f0method": "rmvpe",
    },
    "v4-fcpe-fast": {
        "label": "FCPE, блок 0.15с (агрессивно + быстрый питч)",
        "block_s": 0.15, "extra_s": 2.5, "f0method": "fcpe",
    },
}
DEFAULT_VARIANT = "v1-rmvpe"

_ids = itertools.count(1)


class SessionError(Exception):
    pass


class Session:
    def __init__(self, socket: WebSocket, state, variant_key: str):
        self.id = next(_ids)
        self.socket = socket
        self.state = state
        self.variant_key = variant_key
        variant = VARIANTS[variant_key]
        self.f0method = variant["f0method"]
        self.framer = RtFramer(block_s=variant["block_s"], extra_s=variant["extra_s"],
                                crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
        self.stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=variant["block_s"],
                                    crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
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
                    self.framer.return_length_frames, self.f0method,
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
            # Absorb each f0method's one-time lazy model load (infer/rtrvc.py
            # loads rmvpe/fcpe on their *first* inference call, not during
            # construction) here, not on whichever session connects first.
            # Block/extra size doesn't gate a lazy load, so one sizing per
            # distinct f0method used across VARIANTS is enough.
            import numpy as np

            seen_methods = set()
            for variant in VARIANTS.values():
                if variant["f0method"] in seen_methods:
                    continue
                seen_methods.add(variant["f0method"])
                sizing = RtFramer(block_s=variant["block_s"], extra_s=variant["extra_s"],
                                   crossfade_s=CROSSFADE_S, search_s=SEARCH_S)
                zeros = np.zeros(sizing.window_16k, dtype=np.float32)
                engine.convert_block(zeros, sizing.block_16k, sizing.skip_head_frames,
                                      sizing.return_length_frames, variant["f0method"])
            return engine

        state.loading = loop.run_in_executor(state.executor, load_and_warm)
        try:
            state.engine = await state.loading
            state.status = "ready"
        except Exception:
            state.status = "model_unavailable"

        state.bot_room = None
        if state.status == "ready":
            from pathlib import Path

            from .rt_bots import BotRoom

            samples_dir = Path(__file__).resolve().parent.parent / "samples-rt"
            wav_paths = [samples_dir / "ru.wav", samples_dir / "en.wav"]
            state.bot_room = BotRoom(state, wav_paths)
            state.bot_room.start()
        try:
            yield
        finally:
            state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse(
            {"status": app.state.status, "activeSessions": app.state.active_sessions,
             "maxSessions": MAX_SESSIONS,
             "variants": {k: {"label": v["label"], "blockS": v["block_s"], "extraS": v["extra_s"],
                               "f0method": v["f0method"]} for k, v in VARIANTS.items()}},
            status_code=200 if app.state.status == "ready" else 503,
        )

    async def run_session(socket: WebSocket, variant_key: str):
        await socket.accept()
        if variant_key not in VARIANTS:
            await socket.send_json({"type": "error", "code": "invalid_start",
                                     "message": f"unknown variant {variant_key!r}"})
            await socket.close()
            return
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
        session = Session(socket, app.state, variant_key)
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
            variant = VARIANTS[variant_key]
            await socket.send_json({
                "type": "ready", "version": 1, "sampleRate": SAMPLE_RATE,
                "channels": 1, "sampleFormat": "s16le",
                "blockSamples": session.framer.block_48k,
                "blockSeconds": variant["block_s"], "extraSeconds": variant["extra_s"],
                "f0method": variant["f0method"], "variant": variant_key,
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

    @app.websocket("/ws/rvc/{variant_key}")
    async def websocket_variant(socket: WebSocket, variant_key: str):
        await run_session(socket, variant_key)

    @app.websocket("/ws/rvc")
    async def websocket_default(socket: WebSocket):
        await run_session(socket, DEFAULT_VARIANT)

    @app.websocket("/ws/listen")
    async def listen(socket: WebSocket):
        room = app.state.bot_room
        if room is None:
            await socket.close(code=1013)
            return
        await room.handle_listener(socket)

    @app.post("/api/tts")
    async def tts(request: Request):
        from .rt_tts import TtsError, text_to_phone_guy

        if getattr(app.state, "engine", None) is None:
            return JSONResponse({"code": "model_unavailable"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"code": "invalid_request"}, status_code=400)
        text = body.get("text") if isinstance(body, dict) else None
        lang = body.get("lang") if isinstance(body, dict) else None
        if not isinstance(text, str) or lang not in ("ru", "en"):
            return JSONResponse({"code": "invalid_request"}, status_code=400)
        try:
            wav_bytes = await text_to_phone_guy(app.state, text, lang, next(_ids))
        except TtsError as exc:
            code = str(exc).split(":", 1)[0]
            return JSONResponse({"code": code, "message": str(exc)}, status_code=422)
        return Response(content=wav_bytes, media_type="audio/wav")

    @app.post("/api/compare")
    async def compare(request: Request):
        """One recording (raw PCM16 48kHz mono in the request body) run
        through every VARIANT in turn, so it can be judged from a single
        take instead of re-recording per page. Sequential, not parallel --
        all variants share the one GPU-resident engine and its lock."""
        import base64
        import io
        import time

        import numpy as np
        import soundfile as sf

        from .rt_batch import convert_utterance
        from .rt_chunks import FRAME_SAMPLES

        if getattr(app.state, "engine", None) is None:
            return JSONResponse({"code": "model_unavailable"}, status_code=503)
        body = await request.body()
        if len(body) < FRAME_SAMPLES * 2 or len(body) % 2 != 0:
            return JSONResponse({"code": "invalid_request"}, status_code=400)
        max_seconds = 15
        if len(body) > max_seconds * SAMPLE_RATE * 2:
            return JSONResponse({"code": "recording_too_long",
                                  "message": f"max {max_seconds}s"}, status_code=413)
        pcm16 = np.frombuffer(body, dtype="<i2")
        audio_48k = pcm16.astype(np.float32) / 32768.0

        results = {}
        for key, variant in VARIANTS.items():
            begun = time.perf_counter()
            converted = await convert_utterance(
                app.state, audio_48k, next(_ids),
                block_s=variant["block_s"], extra_s=variant["extra_s"], f0method=variant["f0method"],
            )
            wall_ms = round((time.perf_counter() - begun) * 1000, 1)
            buf = io.BytesIO()
            sf.write(buf, converted, SAMPLE_RATE, format="WAV", subtype="PCM_16")
            results[key] = {
                "label": variant["label"],
                "wavBase64": base64.b64encode(buf.getvalue()).decode("ascii"),
                "wallMs": wall_ms,
                "outputSeconds": round(converted.size / SAMPLE_RATE, 2),
            }
        return JSONResponse({"inputSeconds": round(pcm16.size / SAMPLE_RATE, 2), "results": results})

    static_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "web-rt"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def _default_engine():
    from .rt_engine import RtEngine

    return RtEngine()


app = create_app(_default_engine)
