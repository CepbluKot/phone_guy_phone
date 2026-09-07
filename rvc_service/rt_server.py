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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .rt_chunks import FRAME_BYTES, RtFramer, RtStitcher, SAMPLE_RATE
from .rt_jobs import JobStore

CROSSFADE_S = 0.05
SEARCH_S = 0.02
MAX_SESSIONS = 4
STALL_SECONDS = 15.0
MAX_COMPARE_SECONDS = 60
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

# Down to the one variant listening picked as best (see
# docs/LATENCY_VERDICT_SONNET_2026-09-07.md and the user's own A/B result:
# "FCPE, блок 0.3с - топ"). RMVPE is no longer loaded anywhere in this
# process as a result -- rt_tts.py and rt_bots.py were switched to fcpe too
# so nothing lazy-loads it, freeing that memory for the cross-agent GPT
# comparison in /api/compare (see _run_gpt_variant below).
VARIANTS = {
    "v2-fcpe": {
        "label": "Sonnet: FCPE, блок 0.3с",
        "block_s": 0.3, "extra_s": 1.5, "f0method": "fcpe",
    },
}
DEFAULT_VARIANT = "v2-fcpe"

# Cross-agent comparison entries for /api/compare -- see
# experiments/latency-sonnet/scripts/run_gpt_variant.py and run_glm_variant.py
# for what each actually runs.
GPT_LABEL = ("GPT (research/latency-optimization): их v2-профиль, hop 1.0с / "
             "context 0.5с — прогнано через вашу запись в изоляции (их deployed "
             "release, скопирован read-only), не через живой прод-канарейку")
GLM_LABEL = ("GLM (research/latency-glm): их рекомендованный профиль, hop 1.0с / "
             "context 0.5с — прогнано через вашу запись в изоляции (их собственная "
             "бенчмарк-копия, скопирована read-only)")
GPT_ROOT = "/tmp/rt_demo_gpt"
GPT_PYTHON = "/opt/voice-rvc/venv/bin/python3"
GPT_TIMEOUT_S = 150.0
GLM_ROOT = "/tmp/rt_demo_glm"
GLM_PYTHON = "/opt/voice-rvc/venv/bin/python3"
GLM_TIMEOUT_S = 150.0

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
        # Separate from gpu_lock on purpose: holding gpu_lock for an entire
        # GPT/GLM subprocess run (can be tens of seconds for a long
        # recording) would starve every live v2-fcpe/bots/tts caller's
        # per-block calls long enough to trip their own STALL_SECONDS
        # watchdog. This only needs to stop two of these transient ~1-1.5GB
        # copies (GPT and/or GLM, from one or two concurrent compare jobs)
        # from being resident at once -- see the VRAM budget note on
        # _run_gpt_variant below.
        state.external_lock = asyncio.Lock()
        state.last_session_id = None
        state.active_sessions = 0
        state.compare_jobs = JobStore()
        state.tts_jobs = JobStore()
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

    @app.get("/api/tts")
    async def tts_get_hint():
        # Browsers land here directly (typed/pasted URL, not the page's own
        # fetch()) often enough that a bare 404 is worth replacing with a
        # pointer to the actual page -- confirmed via server logs that a
        # real visitor did exactly this.
        return JSONResponse(
            {"code": "use_post", "message": "This endpoint only accepts POST from the /tts/ page -- open https://voice-claude.lan.awesomeio.ru/tts/ instead."},
            status_code=405,
        )

    @app.get("/api/compare")
    async def compare_get_hint():
        return JSONResponse(
            {"code": "use_post", "message": "This endpoint only accepts POST from the /compare/ page -- open https://voice-claude.lan.awesomeio.ru/compare/ instead."},
            status_code=405,
        )

    @app.get("/api/tts/{job_id}")
    async def tts_status(job_id: str):
        job = app.state.tts_jobs.get(job_id)
        if job is None:
            return JSONResponse({"code": "not_found"}, status_code=404)
        return JSONResponse({
            "status": job["status"],
            "text": job.get("text"),
            "lang": job.get("lang"),
            "wavBase64": job.get("wavBase64"),
            "error": job.get("error"),
        })

    async def _run_tts_job(job_id: str, text: str, lang: str) -> None:
        import base64

        from .rt_tts import TtsError, text_to_phone_guy

        job = app.state.tts_jobs.get(job_id)
        try:
            wav_bytes = await text_to_phone_guy(app.state, text, lang, next(_ids))
            job["wavBase64"] = base64.b64encode(wav_bytes).decode("ascii")
            job["status"] = "done"
        except TtsError as exc:
            job["status"] = "error"
            job["error"] = str(exc).split(":", 1)[0]
        except Exception:
            job["status"] = "error"
            job["error"] = "internal_error"
        finally:
            app.state.tts_jobs.notify(job_id)

    @app.websocket("/ws/tts/{job_id}")
    async def tts_ws(socket: WebSocket, job_id: str):
        """Pushed alternative to polling GET /api/tts/<job_id>: sends a
        snapshot on connect and again every time the job changes, until it
        reaches a terminal status."""
        await socket.accept()
        try:
            while True:
                job = app.state.tts_jobs.get(job_id)
                if job is None:
                    await socket.send_json({"code": "not_found"})
                    return
                await socket.send_json({
                    "status": job["status"], "text": job.get("text"), "lang": job.get("lang"),
                    "wavBase64": job.get("wavBase64"), "error": job.get("error"),
                })
                if job["status"] != "running":
                    return
                await app.state.tts_jobs.wait_for_update(job_id)
        except WebSocketDisconnect:
            pass
        finally:
            with suppress(RuntimeError):
                await socket.close()

    @app.post("/api/tts")
    async def tts(request: Request):
        """Kicks off Piper + RVC synthesis as a background job and returns
        its id immediately -- see rt_jobs.py for why (the same reasoning as
        /api/compare, even though a single tts job is usually quick: a
        dropped connection shouldn't lose work that already started, and a
        job id in the URL means the result can be reopened later)."""
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
        job_id = app.state.tts_jobs.create(status="running", text=text, lang=lang)
        asyncio.create_task(_run_tts_job(job_id, text, lang))
        return JSONResponse({"jobId": job_id})

    async def _decode_upload_to_pcm16_48k(raw: bytes, max_seconds: int) -> bytes:
        """Any container/codec ffmpeg understands -> raw PCM16 48kHz mono,
        trimmed to max_seconds during decode so a long upload never gets
        fully decoded into memory."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-v", "error", "-i", "pipe:0",
            "-t", str(max_seconds), "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(raw)
        if proc.returncode != 0 or not stdout:
            lines = stderr.decode("utf-8", "replace").strip().splitlines()
            raise ValueError((lines[-1] if lines else "decode failed")[:300])
        return stdout

    @app.get("/api/compare/{job_id}")
    async def compare_status(job_id: str):
        job = app.state.compare_jobs.get(job_id)
        if job is None:
            return JSONResponse({"code": "not_found"}, status_code=404)
        return JSONResponse({
            "status": job["status"],
            "inputSeconds": job["inputSeconds"],
            "order": job["order"],
            "results": job["results"],
            "error": job.get("error"),
        })

    async def _run_external_variant(
        *, name: str, script: str, root: str, python: str, timeout_s: float,
        label: str, input_wav_bytes: bytes,
    ) -> dict:
        """Runs the recording through another agent's own chunks.py/engine.py
        -- copied read-only into an isolated directory on VM209, never a
        live process of theirs -- as its own subprocess with its own CUDA
        context. Serialized behind external_lock (its own lock, not the
        real-time engine's gpu_lock -- see that field's comment) so two
        compare jobs can't have two of these transient ~1-1.5GB copies
        resident at once on top of production (~1.0GB) and our own engine
        (~0.8GB, trimmed to fcpe-only; see VARIANTS above)."""
        import base64
        import tempfile
        from pathlib import Path

        import soundfile as sf

        with tempfile.TemporaryDirectory(prefix=f"rt_compare_{name}_") as tmp:
            in_path = Path(tmp) / "in.wav"
            out_path = Path(tmp) / "out.wav"
            in_path.write_bytes(input_wav_bytes)

            async with app.state.external_lock:
                begun = time.perf_counter()
                proc = await asyncio.create_subprocess_exec(
                    python, script,
                    "--input", str(in_path), "--output", str(out_path),
                    cwd=root,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise RuntimeError(f"{name}_timeout: превышено время ожидания") from None
                wall_ms = round((time.perf_counter() - begun) * 1000, 1)

            if proc.returncode != 0 or not out_path.exists():
                tail = stderr.decode("utf-8", "replace").strip().splitlines()
                raise RuntimeError(f"{name}_failed: " + (tail[-1] if tail else "unknown error")[:250])

            info = sf.info(str(out_path))
            wav_bytes = out_path.read_bytes()
            return {
                "label": label,
                "wavBase64": base64.b64encode(wav_bytes).decode("ascii"),
                "wallMs": wall_ms,
                "outputSeconds": round(info.frames / info.samplerate, 2),
            }

    async def _run_compare_job(job_id: str, audio_48k, input_wav_bytes: bytes) -> None:
        import base64
        import io

        import soundfile as sf

        from .rt_batch import convert_utterance

        job = app.state.compare_jobs.get(job_id)
        try:
            for key, variant in VARIANTS.items():
                begun = time.perf_counter()
                converted = await convert_utterance(
                    app.state, audio_48k, next(_ids),
                    block_s=variant["block_s"], extra_s=variant["extra_s"], f0method=variant["f0method"],
                )
                wall_ms = round((time.perf_counter() - begun) * 1000, 1)
                buf = io.BytesIO()
                sf.write(buf, converted, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                job["results"][key] = {
                    "label": variant["label"],
                    "wavBase64": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "wallMs": wall_ms,
                    "outputSeconds": round(converted.size / SAMPLE_RATE, 2),
                }
                app.state.compare_jobs.notify(job_id)

            # Cross-agent entries are best-effort and run one at a time (see
            # external_lock): a failure here (an isolated copy OOMing,
            # timing out, etc.) shows up as an error on just that one card,
            # not as a failure of the whole job -- our own result above
            # already landed regardless.
            for name, script, root, python, timeout_s, label in (
                ("gpt", "run_gpt_variant.py", GPT_ROOT, GPT_PYTHON, GPT_TIMEOUT_S, GPT_LABEL),
                ("glm", "run_glm_variant.py", GLM_ROOT, GLM_PYTHON, GLM_TIMEOUT_S, GLM_LABEL),
            ):
                try:
                    job["results"][name] = await _run_external_variant(
                        name=name, script=script, root=root, python=python,
                        timeout_s=timeout_s, label=label, input_wav_bytes=input_wav_bytes,
                    )
                except Exception as exc:
                    job["results"][name] = {"label": label, "error": str(exc)[:300]}
                app.state.compare_jobs.notify(job_id)

            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)[:300]
        finally:
            app.state.compare_jobs.notify(job_id)

    @app.websocket("/ws/compare/{job_id}")
    async def compare_ws(socket: WebSocket, job_id: str):
        """Pushed alternative to polling GET /api/compare/<job_id>: sends a
        snapshot on connect and again every time a new result lands, until
        the job reaches a terminal status."""
        await socket.accept()
        try:
            while True:
                job = app.state.compare_jobs.get(job_id)
                if job is None:
                    await socket.send_json({"code": "not_found"})
                    return
                await socket.send_json({
                    "status": job["status"], "inputSeconds": job["inputSeconds"],
                    "order": job["order"], "results": job["results"], "error": job.get("error"),
                })
                if job["status"] != "running":
                    return
                await app.state.compare_jobs.wait_for_update(job_id)
        except WebSocketDisconnect:
            pass
        finally:
            with suppress(RuntimeError):
                await socket.close()

    @app.post("/api/compare")
    async def compare(request: Request):
        """One recording -- either raw PCM16 48kHz mono in the request body
        (the mic-recording page) or an uploaded audio file of any format
        the server's ffmpeg understands (multipart field "audio") -- run
        through every VARIANT in turn as a background job (see rt_jobs.py):
        this returns a job id immediately, the page polls
        GET /api/compare/<job_id> for progress instead of holding one HTTP
        response open for the whole multi-minute conversion. Sequential,
        not parallel -- all variants share the one GPU-resident engine and
        its lock, and only one variant's audio is ever held in memory at a
        time."""
        import numpy as np

        from .rt_chunks import FRAME_SAMPLES

        if getattr(app.state, "engine", None) is None:
            return JSONResponse({"code": "model_unavailable"}, status_code=503)

        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("audio")
            if upload is None:
                return JSONResponse({"code": "invalid_request"}, status_code=400)
            raw = await upload.read()
            if not raw:
                return JSONResponse({"code": "invalid_request"}, status_code=400)
            if len(raw) > MAX_UPLOAD_BYTES:
                return JSONResponse(
                    {"code": "file_too_large", "message": f"max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"},
                    status_code=413,
                )
            try:
                body = await _decode_upload_to_pcm16_48k(raw, MAX_COMPARE_SECONDS)
            except ValueError as exc:
                return JSONResponse({"code": "decode_failed", "message": str(exc)}, status_code=400)
        else:
            body = await request.body()
            if len(body) > MAX_COMPARE_SECONDS * SAMPLE_RATE * 2:
                body = body[:MAX_COMPARE_SECONDS * SAMPLE_RATE * 2]

        if len(body) < FRAME_SAMPLES * 2 or len(body) % 2 != 0:
            return JSONResponse({"code": "invalid_request"}, status_code=400)
        pcm16 = np.frombuffer(body, dtype="<i2")
        audio_48k = pcm16.astype(np.float32) / 32768.0
        input_seconds = round(pcm16.size / SAMPLE_RATE, 2)

        import io

        import soundfile as sf

        wav_buf = io.BytesIO()
        sf.write(wav_buf, audio_48k, SAMPLE_RATE, format="WAV", subtype="PCM_16")

        order = [*VARIANTS.keys(), "gpt", "glm"]
        job_id = app.state.compare_jobs.create(
            status="running", inputSeconds=input_seconds, order=order, results={},
        )
        asyncio.create_task(_run_compare_job(job_id, audio_48k, wav_buf.getvalue()))
        return JSONResponse({"jobId": job_id, "inputSeconds": input_seconds, "order": order})

    static_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "web-rt"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def _default_engine():
    from .rt_engine import RtEngine

    return RtEngine(f0method="fcpe")


app = create_app(_default_engine)
