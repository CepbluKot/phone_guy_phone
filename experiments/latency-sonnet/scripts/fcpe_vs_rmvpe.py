"""Compare f0method="rmvpe" vs "fcpe" on the actual rt_engine.RtEngine
(infer/rtrvc.py), same model/index, same block config as the live demo.
Read-only, ephemeral -- loads its own engine, exits, frees GPU memory.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, "/tmp/rt_demo")
from rvc_service.rt_chunks import RtFramer  # noqa: E402
from rvc_service.rt_engine import RtEngine  # noqa: E402


def load16k(path: Path, seconds: float) -> np.ndarray:
    import math
    from scipy.signal import resample_poly

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        g = math.gcd(16000, sr)
        data = resample_poly(data, 16000 // g, sr // g).astype(np.float32)
    n = int(seconds * 16000)
    if data.size < n:
        data = np.tile(data, n // data.size + 1)
    return data[:n].astype(np.float32)


def main() -> None:
    framer = RtFramer(block_s=0.3, extra_s=1.5, crossfade_s=0.05, search_s=0.02)
    source = load16k(Path("/tmp/rt_demo/samples-rt/ru.wav"), seconds=10.0)
    engine = RtEngine()  # f0method chosen per-call, not at construction
    import os
    os.chdir("/opt/voice-changer/experiments/phoneguy/upstream")

    results = {}
    for method in ["rmvpe", "fcpe"]:
        times = []
        pos = 0
        n_blocks = (source.size - framer.window_16k) // framer.block_16k
        # first iteration of each method's loop absorbs that method's lazy
        # model load (excluded from the reported stats below)
        for i in range(min(21, max(n_blocks, 1))):
            window = source[pos: pos + framer.window_16k]
            if window.size < framer.window_16k:
                window = np.pad(window, (0, framer.window_16k - window.size))
            import torch
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            out = engine._rvc.infer(
                torch.from_numpy(window).to(engine._device), framer.block_16k,
                framer.skip_head_frames, framer.return_length_frames, method,
            )
            torch.cuda.synchronize()
            dt = time.perf_counter() - t0
            assert torch.isfinite(out).all()
            pos += framer.block_16k
            if i > 0:  # drop the lazy-load call
                times.append(dt)
        times.sort()
        n = len(times)
        results[method] = {
            "p50_ms": round(times[n // 2] * 1000, 2),
            "p95_ms": round(times[int(0.95 * (n - 1))] * 1000, 2),
            "min_ms": round(times[0] * 1000, 2),
            "max_ms": round(times[-1] * 1000, 2),
            "n": n,
        }
        print(method, json.dumps(results[method]), flush=True)

    print("---SUMMARY-JSON---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
