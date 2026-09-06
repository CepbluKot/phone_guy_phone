"""Render a wav through the PROPOSED streaming path: infer/rtrvc.py (the
upstream real-time engine realtime_gui.py actually uses, with pitch caching
and partial vocoder decode via skip_head/return_length) at a small block_time,
reconstructed with a SOLA-style crossfade generalizing the search/blend
already used by rvc_service/chunks.py. Read-only against the shared VM;
loads its own model instance and exits.
"""
from __future__ import annotations

import contextlib
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ASSETS_ROOT = Path("/opt/voice-changer/experiments/phoneguy")
UPSTREAM = ASSETS_ROOT / "upstream"
MODEL = ASSETS_ROOT / "models" / "PhoneGuyfnaf1V1.pth"
INDEX = ASSETS_ROOT / "models" / "added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"

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


def load16k(path: Path, seconds: float) -> np.ndarray:
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
    return data[:n]


def aligned_start(candidate_region: np.ndarray, reference: np.ndarray, search: int) -> int:
    """Generalized version of chunks.Chunker._aligned_start: find the offset
    in [0, 2*search] into candidate_region whose first len(reference) samples
    best match reference (SOLA-style phase alignment)."""
    ref_energy = float(np.dot(reference, reference))
    if ref_energy <= np.finfo(np.float32).eps:
        return search
    best_start, best_err = search, None
    for start in range(0, 2 * search + 1):
        seg = candidate_region[start:start + reference.size]
        if seg.size != reference.size:
            continue
        err = float(np.mean(np.square(seg - reference)))
        if best_err is None or err < best_err:
            best_err, best_start = err, start
    return best_start


def main() -> None:
    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    block_s = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25
    extra_s = float(sys.argv[5]) if len(sys.argv) > 5 else 2.5
    crossfade_s = 0.05
    sola_search_s = 0.02

    with chdir(UPSTREAM):
        import torch
        from configs.config import Config
        from infer.rtrvc import RVC

        argv = sys.argv[:]
        sys.argv = [argv[0]]
        try:
            config = Config()
        finally:
            sys.argv = argv
        config.is_half = False
        config.n_cpu = 2
        torch.set_num_threads(2)

        rvc_obj = RVC(0, 0, str(MODEL), str(INDEX), 0.6, config)
        tgt_sr = rvc_obj.tgt_sr  # 32000

        source = load16k(src_path, seconds)
        block_16k = int(block_s * 16000)
        window_16k = int((extra_s + crossfade_s + sola_search_s + block_s) * 16000)
        skip_head = int(round(extra_s * 100))
        return_len_frames = int(round((block_s + crossfade_s + sola_search_s) * 100))

        crossfade_n = int(crossfade_s * tgt_sr)
        search_n = int(sola_search_s * tgt_sr) // 2
        block_n = int(block_s * tgt_sr)

        buf = np.zeros(window_16k, dtype=np.float32)
        n_blocks = source.size // block_16k

        stitched = []
        prev_tail = None
        hop_times = []
        for i in range(n_blocks):
            new = source[i * block_16k:(i + 1) * block_16k]
            buf[:-block_16k] = buf[block_16k:]
            buf[-block_16k:] = new

            chunk = torch.from_numpy(buf).to(config.device)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            out = rvc_obj.infer(chunk, block_16k, skip_head, return_len_frames, "rmvpe")
            torch.cuda.synchronize()
            hop_times.append(time.perf_counter() - t0)
            out_np = out.detach().cpu().numpy().astype(np.float32)

            if out_np.size < crossfade_n + block_n:
                out_np = np.pad(out_np, (0, crossfade_n + block_n - out_np.size))

            if prev_tail is None:
                stitched.append(out_np[: crossfade_n + block_n])
            else:
                region = out_np[: crossfade_n + 2 * search_n]
                start = aligned_start(region, prev_tail, search_n)
                blended_head = region[start:start + crossfade_n]
                fade = (np.arange(crossfade_n, dtype=np.float32) + 1) / crossfade_n
                blended = prev_tail * (1 - fade) + blended_head * fade
                tail_start = start + crossfade_n
                rest = out_np[tail_start:tail_start + block_n]
                if rest.size < block_n:
                    rest = np.pad(rest, (0, block_n - rest.size))
                stitched.append(np.concatenate([blended, rest]))
            prev_tail = out_np[-crossfade_n:] if out_np.size >= crossfade_n else out_np

        final = np.concatenate(stitched) if stitched else np.zeros(0, dtype=np.float32)
        final = np.clip(final, -1.0, 1.0)

        from scipy.signal import resample_poly
        g = math.gcd(48000, tgt_sr)
        final48 = resample_poly(final, 48000 // g, tgt_sr // g).astype(np.float32)
        sf.write(str(out_path), final48, 48000)

        hop_times.sort()
        n = len(hop_times)
        print(f"block_s={block_s} extra_s={extra_s} blocks={n}")
        if n:
            print(f"processing p50={hop_times[n//2]*1000:.1f}ms "
                  f"p95={hop_times[int(0.95*(n-1))]*1000:.1f}ms "
                  f"max={hop_times[-1]*1000:.1f}ms rtf_p50={hop_times[n//2]/block_s:.3f}")
        print(f"wrote {out_path} ({final48.size/48000:.2f}s)")


if __name__ == "__main__":
    main()
