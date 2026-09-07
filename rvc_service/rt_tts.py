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

from .rt_batch import convert_utterance
from .rt_chunks import SAMPLE_RATE

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


async def text_to_phone_guy(state, text: str, lang: str, session_id: int) -> bytes:
    """Full pipeline: text -> Piper -> resample -> shared RVC engine -> WAV bytes."""
    import io
    import soundfile as sf

    base_audio = await synthesize_base_voice(text, lang)
    base_sr = _piper_sample_rate(lang)
    audio_48k = resample_to_48k(base_audio, base_sr)
    converted = await convert_utterance(state, audio_48k, session_id,
                                         block_s=0.3, extra_s=1.5, f0method="fcpe")

    buf = io.BytesIO()
    sf.write(buf, converted, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()
