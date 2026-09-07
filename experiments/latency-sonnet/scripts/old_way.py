"""Render a wav through the CURRENT production path (rvc_service.chunks+engine,
2s hop / 2.6s window, full offline pipeline.pipeline() every hop) for an A/B
quality comparison against the proposed rtrvc streaming path. Read-only;
does not touch the running voice-rvc.service.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, "/tmp")
from rvc_pkg.chunks import Chunker, FRAME_SAMPLES, SAMPLE_RATE  # noqa: E402
from rvc_pkg.engine import Engine  # noqa: E402


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
    return data[:n]


def main() -> None:
    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0

    audio = load48k(src_path, seconds)
    pcm16 = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2")

    engine = Engine()
    chunker = Chunker()

    out_chunks = []
    hop_times = []
    n_frames = pcm16.size // FRAME_SAMPLES
    t_start = time.perf_counter()
    for i in range(n_frames):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window = chunker.push(frame)
        if window is None:
            continue
        t0 = time.perf_counter()
        converted = engine.convert(window)
        hop_times.append(time.perf_counter() - t0)
        out_chunks.append(chunker.render(converted))
    wall = time.perf_counter() - t_start

    out_pcm = b"".join(out_chunks)
    out_audio = np.frombuffer(out_pcm, dtype="<i2").astype(np.float32) / 32768.0
    sf.write(str(out_path), out_audio, SAMPLE_RATE)

    print(f"hops={len(hop_times)} wall={wall:.2f}s")
    if hop_times:
        hop_times.sort()
        print(f"processing p50={hop_times[len(hop_times)//2]*1000:.1f}ms "
              f"p95={hop_times[int(0.95*(len(hop_times)-1))]*1000:.1f}ms "
              f"max={hop_times[-1]*1000:.1f}ms")
    print(f"wrote {out_path} ({out_audio.size/SAMPLE_RATE:.2f}s)")


if __name__ == "__main__":
    main()
