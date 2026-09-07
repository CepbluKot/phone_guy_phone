"""Feed one whole recorded/synthesized utterance through the shared
infer/rtrvc.py engine, block by block, exactly like a live session would --
just without a real-time deadline. Shared by rt_tts.py (text-to-Phone-Guy)
and rt_server.py's /api/compare (one recording through every VARIANT).
"""
from __future__ import annotations

import asyncio

import numpy as np

from .rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE


async def convert_utterance(
    state,
    audio_48k: np.ndarray,
    session_id: int,
    block_s: float,
    extra_s: float,
    f0method: str,
    crossfade_s: float = 0.05,
    search_s: float = 0.02,
    transpose: int = 0,
    index_rate: float | None = None,
) -> np.ndarray:
    """Returns 48kHz float32 PCM, same length (rounded up to a whole 20ms
    frame) as the input."""
    framer = RtFramer(block_s=block_s, extra_s=extra_s, crossfade_s=crossfade_s, search_s=search_s)
    stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=block_s, crossfade_s=crossfade_s, search_s=search_s)
    pcm16 = np.clip(np.rint(audio_48k * 32768.0), -32768, 32767).astype("<i2")
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
            # Cheap attribute sets (see RtEngine.set_transpose/set_index_rate),
            # not a model reload -- but the engine is shared across
            # sessions/jobs, so re-apply every block in case another caller
            # changed them while this one waited for the lock.
            state.engine.set_transpose(transpose)
            if index_rate is not None:
                state.engine.set_index_rate(index_rate)
            out_np = await loop.run_in_executor(
                state.executor, state.engine.convert_block_48k,
                window_16k, framer.block_16k, framer.skip_head_frames,
                framer.return_length_frames, f0method,
            )
        out_chunks.append(stitcher.render(out_np))
    if state.last_session_id == session_id:
        state.last_session_id = None
    pcm = b"".join(out_chunks)
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
