"""Text-to-Phone-Guy: Piper TTS (a plain, offline, CPU-only synthesizer --
no relation to the RVC model) generates speech in a generic voice, which is
then run through the same shared infer/rtrvc.py engine rt_server.py already
holds in GPU memory, exactly like a prerecorded wav would be (see
experiments/latency-sonnet/scripts/rt_stream_demo.py for the same pattern).

Piper runs in its own venv (/tmp/tts_venv), separate from the RVC service's
venv, and never touches the GPU -- it's invoked as a subprocess so a slow
synthesis can't block the asyncio event loop or the GPU lock.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

import numpy as np

from .rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE

PIPER_PYTHON = Path("/tmp/tts_venv/bin/python3")
VOICES_DIR = Path("/tmp/rt_demo/piper_voices")
VOICES = {
    "ru": VOICES_DIR / "ru_RU-denis-medium.onnx",
    "en": VOICES_DIR / "en_US-ryan-medium.onnx",
}
MAX_CHARS = 400
TTS_TIMEOUT_SECONDS = 30.0


class TtsError(Exception):
    pass


async def synthesize_base_voice(text: str, lang: str) -> np.ndarray:
    """Run Piper in its own process; returns mono float32 PCM at whatever
    sample rate the voice model uses (resampling happens separately)."""
    voice = VOICES.get(lang)
    if voice is None or not voice.exists() or not PIPER_PYTHON.exists():
        raise TtsError("tts_unavailable")
    text = text.strip()
    if not text:
        raise TtsError("empty_text")
    if len(text) > MAX_CHARS:
        raise TtsError("text_too_long")

    proc = await asyncio.create_subprocess_exec(
        str(PIPER_PYTHON), "-m", "piper", "-m", str(voice), "--output-raw",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(text.encode("utf-8")), TTS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise TtsError("tts_timeout") from None
    if proc.returncode != 0 or not stdout:
        raise TtsError("tts_failed: " + stderr.decode("utf-8", "replace")[-500:])

    pcm16 = np.frombuffer(stdout, dtype="<i2")
    return pcm16.astype(np.float32) / 32768.0


def _piper_sample_rate(lang: str) -> int:
    import json

    voice = VOICES[lang]
    config = json.loads(voice.with_suffix(".onnx.json").read_text())
    return int(config["audio"]["sample_rate"])


def resample_to_48k(audio: np.ndarray, src_sr: int) -> np.ndarray:
    from scipy.signal import resample_poly

    if src_sr == SAMPLE_RATE:
        return audio.astype(np.float32)
    g = math.gcd(src_sr, SAMPLE_RATE)
    return resample_poly(audio, SAMPLE_RATE // g, src_sr // g).astype(np.float32)


async def convert_through_rvc(state, audio_48k: np.ndarray, session_id: int) -> np.ndarray:
    """Feed a full utterance through the shared engine block-by-block, same
    algorithm as a live session, just without a real-time deadline. Returns
    48kHz float32 PCM."""
    framer = RtFramer(block_s=0.3, extra_s=1.5, crossfade_s=0.05, search_s=0.02)
    stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=0.3, crossfade_s=0.05, search_s=0.02)
    pcm16 = np.clip(np.rint(audio_48k * 32768.0), -32768, 32767).astype("<i2")
    # pad to a whole number of capture frames so the trailing partial second
    # of speech isn't silently dropped by the framer
    pad = (-pcm16.size) % FRAME_SAMPLES
    if pad:
        pcm16 = np.pad(pcm16, (0, pad))

    loop = asyncio.get_running_loop()
    out_chunks = []
    for i in range(pcm16.size // FRAME_SAMPLES):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window_16k = framer.push(frame)
        if window_16k is None:
            continue
        async with state.gpu_lock:
            if state.last_session_id != session_id:
                state.engine.reset_pitch_cache()
                state.last_session_id = session_id
            out_np = await loop.run_in_executor(
                state.executor, state.engine.convert_block_48k,
                window_16k, framer.block_16k, framer.skip_head_frames, framer.return_length_frames,
            )
        out_chunks.append(stitcher.render(out_np))
    if state.last_session_id == session_id:
        state.last_session_id = None
    pcm = b"".join(out_chunks)
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


async def text_to_phone_guy(state, text: str, lang: str, session_id: int) -> bytes:
    """Full pipeline: text -> Piper -> resample -> shared RVC engine -> WAV bytes."""
    import io
    import soundfile as sf

    base_audio = await synthesize_base_voice(text, lang)
    base_sr = _piper_sample_rate(lang)
    audio_48k = resample_to_48k(base_audio, base_sr)
    converted = await convert_through_rvc(state, audio_48k, session_id)

    buf = io.BytesIO()
    sf.write(buf, converted, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()
