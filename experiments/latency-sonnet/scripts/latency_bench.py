"""Standalone, read-only latency benchmark for the pinned Phone Guy RVC pipeline.

Does NOT touch /opt/voice-rvc/current or the running voice-rvc.service.
Loads the model once from the same read-only assets the service uses, then
times pipeline.pipeline() calls for a matrix of (window shape, f0 method)
configs on a synthetic + real speech sample. Prints a table and exits,
freeing all GPU memory when the process ends.
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

SAMPLE_RATE = 48_000
MODEL_INPUT_RATE = 16_000
MODEL_SAMPLE_RATE = 32_000

os.environ.update(
    TORCH_FORCE_WEIGHTS_ONLY_LOAD="1",
    RVC_CUDA_GRAPH="0",
    OMP_NUM_THREADS="2",
    OPENBLAS_NUM_THREADS="1",
    HF_HUB_OFFLINE="1",
    weight_root=str(MODEL.parent),
    index_root=str(MODEL.parent),
    outside_index_root=str(MODEL.parent),
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


def load_wav_mono48k(path: Path | None, seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    if path is None or not path.exists():
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        return (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly

        g = math.gcd(SAMPLE_RATE, sr)
        data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
    if data.size < n:
        reps = n // data.size + 1
        data = np.tile(data, reps)
    return data[:n].astype(np.float32)


def main() -> None:
    with chdir(UPSTREAM):
        import torch
        from configs.config import Config
        from infer.vc.modules import VC
        from infer.vc.utils import load_hubert
        from scipy.signal import resample_poly

        torch.set_num_threads(2)
        argv = sys.argv[:]
        sys.argv = [argv[0]]
        try:
            config = Config()
        finally:
            sys.argv = argv
        if not str(config.device).startswith("cuda"):
            raise RuntimeError("benchmark requires CUDA")
        config.is_half = False
        config.dtype = torch.float32
        config.n_cpu = 2

        vc = VC(config)
        vc.get_vc(MODEL.name)
        vc.hubert_model = load_hubert(config)
        assert vc.tgt_sr == MODEL_SAMPLE_RATE

        def convert_once(window: np.ndarray, f0method: str) -> float:
            model_input = resample_poly(window, MODEL_INPUT_RATE, SAMPLE_RATE).astype(np.float32)
            peak = float(np.max(np.abs(model_input))) / 0.95
            if peak > 1.0:
                model_input = model_input / peak
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            out = vc.pipeline.pipeline(
                vc.hubert_model, vc.net_g, 0, model_input, [0.0, 0.0, 0.0], 0,
                f0method, str(INDEX), 0.6, vc.if_f0, vc.tgt_sr, 0, 1.0, vc.version, 0.33,
            )
            torch.cuda.synchronize()
            dt = time.perf_counter() - t0
            assert np.isfinite(np.asarray(out, dtype=np.float32)).all()
            return dt

        # window configs: (label, context_s, hop_s, lookahead_s)
        configs = [
            ("current 2.6s (ctx.5+hop2+la.1)", 0.5, 2.0, 0.1),
            ("1.1s (ctx.2+hop.8+la.1)", 0.2, 0.8, 0.1),
            ("0.7s (ctx.15+hop.5+la.05)", 0.15, 0.5, 0.05),
            ("0.4s (ctx.1+hop.25+la.05)", 0.1, 0.25, 0.05),
            ("0.25s (ctx.05+hop.15+la.05)", 0.05, 0.15, 0.05),
        ]
        methods = ["rmvpe"]

        source = load_wav_mono48k(SAMPLE_WAV, seconds=6.0)

        results = []
        for label, ctx_s, hop_s, la_s in configs:
            window_s = ctx_s + hop_s + la_s
            window_n = int(round(window_s * SAMPLE_RATE))
            if window_n > source.size:
                window = np.pad(source, (0, window_n - source.size), mode="edge")
            else:
                window = source[:window_n]
            for method in methods:
                # warm-up (first call per method loads fcpe lazily / autotunes)
                convert_once(window, method)
                samples = [convert_once(window, method) for _ in range(6)]
                rtf = [s / hop_s for s in samples]
                results.append({
                    "config": label,
                    "f0method": method,
                    "hop_s": hop_s,
                    "window_s": round(window_s, 3),
                    "p50_ms": round(statistics.median(samples) * 1000, 1),
                    "p95_ms": round(sorted(samples)[int(0.95 * (len(samples) - 1))] * 1000, 1),
                    "min_ms": round(min(samples) * 1000, 1),
                    "rtf_p50": round(statistics.median(rtf), 3),
                    "rtf_p95": round(sorted(rtf)[int(0.95 * (len(rtf) - 1))], 3),
                })
                print(json.dumps(results[-1]), flush=True)

        print("---SUMMARY-JSON---")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
