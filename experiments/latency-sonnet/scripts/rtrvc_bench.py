"""Read-only benchmark of the pinned upstream's purpose-built streaming engine
(infer/rtrvc.py, the class realtime_gui.py actually uses) against the same
Phone Guy model/index, at several block_time settings. RMVPE only (no new
dependencies installed). Does not touch /opt/voice-rvc/current or the running
voice-rvc.service; loads its own model instance and exits, freeing GPU memory.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ASSETS_ROOT = Path("/opt/voice-changer/experiments/phoneguy")
UPSTREAM = ASSETS_ROOT / "upstream"
MODEL = ASSETS_ROOT / "models" / "PhoneGuyfnaf1V1.pth"
INDEX = ASSETS_ROOT / "models" / "added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
SAMPLE_WAV = Path(sys.argv[1]) if len(sys.argv) > 1 else None

os.environ.update(
    TORCH_FORCE_WEIGHTS_ONLY_LOAD="1",
    OMP_NUM_THREADS="2",
    OPENBLAS_NUM_THREADS="1",
    HF_HUB_OFFLINE="1",
    rmvpe_root=str(UPSTREAM / "assets" / "rmvpe"),
)
sys.path.insert(0, str(UPSTREAM))


@contextlib.contextmanager
def chdir(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def load_wav_16k(path: Path | None, seconds: float) -> np.ndarray:
    n = int(seconds * 16000)
    if path is None or not path.exists():
        t = np.arange(n, dtype=np.float32) / 16000
        return (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        data = resample_poly(data, 16000 // g, sr // g).astype(np.float32)
    if data.size < n:
        data = np.tile(data, n // data.size + 1)
    return data[:n].astype(np.float32)


def main() -> None:
    with chdir(UPSTREAM):
        import torch
        from configs.config import Config

        argv = sys.argv[:]
        sys.argv = [argv[0]]
        try:
            config = Config()
        finally:
            sys.argv = argv
        if not str(config.device).startswith("cuda"):
            raise RuntimeError("benchmark requires CUDA")
        config.is_half = False
        config.n_cpu = 2
        torch.set_num_threads(2)

        from infer.rtrvc import RVC

        source = load_wav_16k(SAMPLE_WAV, seconds=8.0)

        # (label, block_time_s, extra_time_s, crossfade_s)
        configs = [
            ("official default block=.25 extra=2.5", 0.25, 2.5, 0.05),
            ("block=.4 extra=1.5", 0.4, 1.5, 0.05),
            ("block=.6 extra=1.0", 0.6, 1.0, 0.05),
            ("block=.15 extra=2.5 (aggressive)", 0.15, 2.5, 0.05),
        ]
        sola_search_s = 0.01
        results = []
        rvc_obj = None
        for label, block_s, extra_s, crossfade_s in configs:
            key = 0
            formant = 0
            rvc_obj = RVC(key, formant, str(MODEL), str(INDEX), 0.6, config, last_rvc=rvc_obj)
            block_16k = int(block_s * 16000)
            skip_head = int(extra_s * 100)
            return_length = int((block_s + crossfade_s + sola_search_s) * 100)
            window_16k = int((extra_s + crossfade_s + sola_search_s + block_s) * 16000)

            pos = 0
            times = []
            for i in range(9):
                end = pos + window_16k
                if end > source.size:
                    pos = 0
                    end = window_16k
                chunk = torch.from_numpy(source[pos:end].copy()).to(config.device)
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                out = rvc_obj.infer(chunk, block_16k, skip_head, return_length, "rmvpe")
                torch.cuda.synchronize()
                dt = time.perf_counter() - t0
                assert torch.isfinite(out).all()
                if i >= 2:  # drop 2 warm-up/cache-fill iterations
                    times.append(dt)
                pos += block_16k

            rtf = [t / block_s for t in times]
            results.append({
                "config": label,
                "block_s": block_s,
                "extra_s": extra_s,
                "p50_ms": round(statistics.median(times) * 1000, 1),
                "p95_ms": round(sorted(times)[int(0.95 * (len(times) - 1))] * 1000, 1),
                "min_ms": round(min(times) * 1000, 1),
                "rtf_p50": round(statistics.median(rtf), 3),
                "rtf_p95": round(sorted(rtf)[int(0.95 * (len(rtf) - 1))], 3),
            })
            print(json.dumps(results[-1]), flush=True)

        print("---SUMMARY-JSON---")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
