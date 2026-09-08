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

CROSSFADE_S = 0.05
SEARCH_S = 0.02
MAX_SESSIONS = 4
STALL_SECONDS = 15.0

# FCPE stays available solely as the managed canary. The accepted production
# path is GPT v2 on voice-rvc.service; this process must not load a duplicate
# GPT model or depend on an ad-hoc comparison directory.
VARIANTS = {
    "v2-fcpe": {
        "label": "FCPE canary, block 0.3 s",
        "block_s": 0.3, "extra_s": 1.5, "f0method": "fcpe",
    },
}
DEFAULT_VARIANT = "v2-fcpe"

# Median F0 (YIN, see pitch.py) measured from the Voicemod-made reference
# clip the user originally provided. Briefly changed to 152.9Hz (measured
# the same way from an actual ~2m30s FNAF1 dialogue clip, 4919 voiced
# frames) on the theory that genuine game audio beats a third-party
# reconstruction -- reverted after the user judged the +5.1-semitone
# result it produced as worse, not better, than this value. Likely cause,
# not chased further: per-20s-chunk F0 on that dialogue ranged ~119-197Hz
# (checked after the fact) -- it includes dramatic/tense delivery, so its
# overall median sits well above the character's neutral conversational
# pitch, which is closer to what a calm speaking recording should be
# matched against. The user's ear on the actual output is the real test
# here, not which reference sounds more "authentic" on paper.
TARGET_MEDIAN_F0_HZ = 110.8
AUTO_TRANSPOSE_LIMIT = 12
# How much live mic audio a Session accumulates before measuring its own
# median F0 once (see Session._measure_auto_transpose) -- long enough for
# pitch.py's YIN tracker to see plenty of voiced frames, short enough that
# the untransposed warm-up period at the start of a session is barely
# noticeable.
AUTO_TRANSPOSE_WARMUP_SAMPLES = SAMPLE_RATE * 2

# Same idea as TARGET_MEDIAN_F0_HZ but for the first formant (see
# formant.py) -- median F1 of the same Voicemod reference clip. Opt-in via
# ?formant=auto, NOT the default the way transpose=auto is: LPC formant
# tracking is a much shakier measurement than YIN pitch tracking, and the
# auto-transpose episode (a "more authentic" reference producing a *worse*
# result per the user's own ear, reverted) is reason enough to not repeat
# that mistake by defaulting an even less trustworthy auto-correction.
# Clamped tighter than transpose, too -- formant_shift distorts audibly
# well before +-12.
TARGET_F1_HZ = 572.1
AUTO_FORMANT_LIMIT = 3

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
        # Same idea as /api/compare's ?transpose=auto (see TARGET_MEDIAN_F0_HZ),
        # just measured from the first couple seconds of live mic audio
        # instead of a whole recording up front -- None until then, so the
        # very start of a session runs untransposed and self-corrects once
        # enough signal has come in.
        self.transpose = None
        self._pitch_frames = []
        self._pitch_samples = 0

    async def _measure_auto_transpose(self, frame: bytes) -> None:
        import math

        import numpy as np

        from .pitch import estimate_median_f0

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
                # Shared engine, so re-apply every block in case another
                # session's turn changed it in between (same reasoning as
                # rt_batch.py's convert_utterance).
                engine.set_transpose(self.transpose or 0.0)
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

        try:
            yield
        finally:
            state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def remove_auxiliary_demo_routes(request: Request, call_next):
        if request.url.path == "/api/tts" or request.url.path.startswith("/api/tts/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if request.url.path == "/api/compare" or request.url.path.startswith("/api/compare/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

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

    async def _run_compare_job(
        job_id: str, audio_48k, input_wav_bytes: bytes,
        transpose: float = 0.0, index_rate: float | None = None, formant_shift: float = 0.0,
        auto_transpose_note: str | None = None, auto_formant_note: str | None = None,
    ) -> None:
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
                    transpose=transpose, index_rate=index_rate, formant_shift=formant_shift,
                )
                wall_ms = round((time.perf_counter() - begun) * 1000, 1)
                buf = io.BytesIO()
                sf.write(buf, converted, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                label = variant["label"]
                if auto_transpose_note:
                    label += f" · {auto_transpose_note}"
                elif transpose:
                    label += f" · транспонирование {transpose:+.2f} полутонов"
                if auto_formant_note:
                    label += f" · {auto_formant_note}"
                elif formant_shift:
                    label += f" · формант {formant_shift:+g}"
                if index_rate is not None:
                    label += f" · index_rate {index_rate:g}"
                job["results"][key] = {
                    "label": label,
                    "wavBase64": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "wallMs": wall_ms,
                    "outputSeconds": round(converted.size / SAMPLE_RATE, 2),
                }
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

        transpose_param = request.query_params.get("transpose", "auto")
        auto_transpose = transpose_param.strip().lower() == "auto"
        transpose = 0
        if not auto_transpose:
            try:
                transpose = float(transpose_param)
            except ValueError:
                return JSONResponse({"code": "invalid_request", "message": "transpose must be a number or \"auto\""}, status_code=400)
            transpose = max(-24, min(24, transpose))
        index_rate_param = request.query_params.get("indexRate")
        index_rate = None
        if index_rate_param is not None:
            try:
                index_rate = max(0.0, min(1.0, float(index_rate_param)))
            except ValueError:
                return JSONResponse({"code": "invalid_request", "message": "indexRate must be a number"}, status_code=400)
        formant_param = request.query_params.get("formant", "0")
        auto_formant = formant_param.strip().lower() == "auto"
        formant_shift = 0.0
        if not auto_formant:
            try:
                formant_shift = float(formant_param)
            except ValueError:
                return JSONResponse({"code": "invalid_request", "message": "formant must be a number or \"auto\""}, status_code=400)
            formant_shift = max(-24.0, min(24.0, formant_shift))

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

        auto_transpose_note = None
        if auto_transpose:
            from .pitch import estimate_median_f0

            median_f0 = estimate_median_f0(audio_48k, SAMPLE_RATE)
            if median_f0 is None:
                transpose = 0
                auto_transpose_note = "не удалось измерить высоту голоса в записи, транспонирование не применено"
            else:
                import math

                transpose = max(
                    -AUTO_TRANSPOSE_LIMIT, min(AUTO_TRANSPOSE_LIMIT,
                    12 * math.log2(TARGET_MEDIAN_F0_HZ / median_f0)),
                )
                auto_transpose_note = f"авто: ваша высота ~{median_f0:.0f}Гц -> сдвиг {transpose:+.2f} полутонов"

        auto_formant_note = None
        if auto_formant:
            from .formant import estimate_median_f1

            median_f1 = estimate_median_f1(audio_48k, SAMPLE_RATE)
            if median_f1 is None:
                formant_shift = 0.0
                auto_formant_note = "не удалось измерить формант в записи, сдвиг не применён"
            else:
                import math

                formant_shift = max(
                    -AUTO_FORMANT_LIMIT, min(AUTO_FORMANT_LIMIT,
                    12 * math.log2(TARGET_F1_HZ / median_f1)),
                )
                auto_formant_note = f"авто-формант (менее надёжно, проверьте на слух): ваш F1 ~{median_f1:.0f}Гц -> сдвиг {formant_shift:+.2f}"

        import io

        import soundfile as sf

        wav_buf = io.BytesIO()
        sf.write(wav_buf, audio_48k, SAMPLE_RATE, format="WAV", subtype="PCM_16")

        order = list(VARIANTS)
        job_id = app.state.compare_jobs.create(
            status="running", inputSeconds=input_seconds, order=order, results={},
        )
        asyncio.create_task(_run_compare_job(
            job_id, audio_48k, wav_buf.getvalue(),
            transpose=transpose, index_rate=index_rate, formant_shift=formant_shift,
            auto_transpose_note=auto_transpose_note, auto_formant_note=auto_formant_note,
        ))
        return JSONResponse({
            "jobId": job_id, "inputSeconds": input_seconds, "order": order,
            "autoTransposeNote": auto_transpose_note, "autoFormantNote": auto_formant_note,
        })

    static_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "web-rt"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def _default_engine():
    from .rt_engine import RtEngine

    return RtEngine(f0method="fcpe")


app = create_app(_default_engine)
