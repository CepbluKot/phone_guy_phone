"""Check what happens across a simulated session restart: does infer/rtrvc.py's
RVC object need its cache_pitch/cache_pitchf explicitly zeroed between
sessions (like realtime_gui.py's prewarm_cuda_graph does), or is stale state
from a previous "call" silently carried into a fresh one? Read-only/ephemeral,
same isolation as the other scripts in this batch.
"""
from __future__ import annotations

import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, "/tmp")
from rvc_service.rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE  # noqa: E402
from rvc_service.rt_engine import RtEngine  # noqa: E402


def run_session(engine, framer_kwargs, source, label):
    framer = RtFramer(**framer_kwargs)
    stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=framer_kwargs["block_s"],
                           crossfade_s=framer_kwargs["crossfade_s"], search_s=framer_kwargs["search_s"])
    pcm16 = np.clip(np.rint(source * 32768.0), -32768, 32767).astype("<i2")
    out_chunks = []
    for i in range(pcm16.size // FRAME_SAMPLES):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window_16k = framer.push(frame)
        if window_16k is None:
            continue
        out_np = engine.convert_block_48k(window_16k, framer.block_16k, framer.skip_head_frames, framer.return_length_frames)
        out_chunks.append(stitcher.render(out_np))
    audio = np.frombuffer(b"".join(out_chunks), dtype="<i2").astype(np.float32) / 32768.0
    rms_first_100ms = float(np.sqrt(np.mean(audio[: int(0.1 * SAMPLE_RATE)] ** 2))) if audio.size else 0.0
    print(f"{label}: emitted={audio.size/SAMPLE_RATE:.2f}s rms_first_100ms={rms_first_100ms:.4f} "
          f"cache_pitch_nonzero={int((engine._rvc.cache_pitch != 0).sum())}/1024")
    return audio


def main() -> None:
    data, sr = sf.read("/tmp/en.wav", dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        import math
        from scipy.signal import resample_poly
        g = math.gcd(SAMPLE_RATE, sr)
        data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
    silence = np.zeros(int(1.0 * SAMPLE_RATE), dtype=np.float32)

    engine = RtEngine()
    kwargs = dict(block_s=0.3, extra_s=1.5, crossfade_s=0.05, search_s=0.02)
    zeros_window = np.zeros(RtFramer(**kwargs).window_16k, dtype=np.float32)
    engine.convert_block(zeros_window, RtFramer(**kwargs).block_16k,
                          RtFramer(**kwargs).skip_head_frames, RtFramer(**kwargs).return_length_frames)

    print("--- session 1: real speech, fresh engine ---")
    run_session(engine, kwargs, data, "session1(speech)")

    print("--- session 2: SAME engine object reused, no explicit reset ---")
    print(f"cache_pitch_nonzero_before_session2={int((engine._rvc.cache_pitch != 0).sum())}/1024 "
          "(should be leftover from session1 if nothing clears it)")
    run_session(engine, kwargs, silence, "session2(silence, stale-cache)")

    print("--- explicitly zeroing cache_pitch/cache_pitchf (what realtime_gui.py does on restart) ---")
    engine._rvc.cache_pitch.zero_()
    engine._rvc.cache_pitchf.zero_()
    run_session(engine, kwargs, silence, "session3(silence, cache explicitly cleared)")


if __name__ == "__main__":
    main()
