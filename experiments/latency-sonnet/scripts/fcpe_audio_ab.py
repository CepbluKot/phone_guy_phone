"""Render the same speech through the validated streaming path
(RtFramer+RtStitcher+RtEngine) once with f0method=rmvpe and once with fcpe,
for a real side-by-side listen -- not just the speed numbers.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, "/tmp/rt_demo")
from rvc_service.rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE  # noqa: E402
from rvc_service.rt_engine import RtEngine  # noqa: E402


def load48k(path: Path, seconds: float) -> np.ndarray:
    import math
    from scipy.signal import resample_poly

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        g = math.gcd(SAMPLE_RATE, sr)
        data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
    n = int(seconds * SAMPLE_RATE)
    if data.size < n:
        data = np.tile(data, n // data.size + 1)
    return data[:n].astype(np.float32)


def render(method: str, source: np.ndarray, out_path: Path) -> None:
    framer = RtFramer(block_s=0.3, extra_s=1.5, crossfade_s=0.05, search_s=0.02)
    stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=0.3, crossfade_s=0.05, search_s=0.02)
    engine = RtEngine(f0method=method)
    pcm16 = np.clip(np.rint(source * 32768.0), -32768, 32767).astype("<i2")
    out_chunks = []
    times = []
    for i in range(pcm16.size // FRAME_SAMPLES):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window_16k = framer.push(frame)
        if window_16k is None:
            continue
        t0 = time.perf_counter()
        out_np = engine.convert_block_48k(window_16k, framer.block_16k, framer.skip_head_frames, framer.return_length_frames)
        times.append(time.perf_counter() - t0)
        out_chunks.append(stitcher.render(out_np))
    audio = np.frombuffer(b"".join(out_chunks), dtype="<i2").astype(np.float32) / 32768.0
    sf.write(str(out_path), audio, SAMPLE_RATE)
    times.sort()
    n = len(times)
    print(f"{method}: wrote {out_path} ({audio.size/SAMPLE_RATE:.2f}s), "
          f"p50={times[n//2]*1000:.1f}ms p95={times[int(0.95*(n-1))]*1000:.1f}ms "
          f"(first call incl. lazy model load: {times[0]*1000:.1f}ms)")


def main() -> None:
    source = load48k(Path("/tmp/rt_demo/samples-rt/ru.wav"), seconds=8.0)
    render("rmvpe", source, Path("/tmp/ab_rmvpe.wav"))
    render("fcpe", source, Path("/tmp/ab_fcpe.wav"))


if __name__ == "__main__":
    main()
