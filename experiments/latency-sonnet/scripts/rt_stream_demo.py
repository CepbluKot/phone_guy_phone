"""Proper incremental streaming test of the FIXED rt_chunks.py (RtFramer +
RtStitcher, ported from chunks.Chunker) driving infer/rtrvc.py (rt_engine.py)
end to end, frame-by-frame (20ms), exactly like a real client would feed it --
not the batch/manual-slicing approach of the earlier new_way.py draft, which
had a stitching bug (see rvc_service/rt_chunks.py's module docstring).

Read-only against the shared VM: loads its own model instance, never touches
/opt/voice-rvc/current or voice-rvc.service, exits and frees GPU memory when
done. Prints periodic GPU-memory snapshots to check for growth over a longer
run (pitch-cache / rolling-buffer leak check).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, "/tmp")
from rvc_service.rt_chunks import FRAME_SAMPLES, RtFramer, RtStitcher, SAMPLE_RATE  # noqa: E402
from rvc_service.rt_engine import RtEngine  # noqa: E402


def load48k_loop(paths: list[Path], seconds: float) -> np.ndarray:
    import math

    from scipy.signal import resample_poly

    chunks = []
    for p in paths:
        data, sr = sf.read(str(p), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != SAMPLE_RATE:
            g = math.gcd(SAMPLE_RATE, sr)
            data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
        chunks.append(data)
    one_round = np.concatenate(chunks)
    n = int(seconds * SAMPLE_RATE)
    reps = n // one_round.size + 1
    return np.tile(one_round, reps)[:n].astype(np.float32)


def gpu_mem_mb() -> float:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1.0


def main() -> None:
    block_s = float(sys.argv[1]) if len(sys.argv) > 1 else 0.3
    extra_s = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 40.0
    out_path = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("/tmp/rt_stream_out.wav")
    crossfade_s = 0.05
    search_s = 0.02

    framer = RtFramer(block_s=block_s, extra_s=extra_s, crossfade_s=crossfade_s, search_s=search_s)
    print(f"skip_head_frames={framer.skip_head_frames} return_length_frames={framer.return_length_frames} "
          f"window_16k={framer.window_16k} block_16k={framer.block_16k}", flush=True)

    print(f"gpu_mem_before_load_mb={gpu_mem_mb()}", flush=True)
    engine = RtEngine()
    # convert_block_48k() already resamples the model's native tgt_sr (32kHz)
    # up to SAMPLE_RATE (48kHz), so the stitcher must slice at 48kHz too --
    # using engine.tgt_sr (32000) here was the bug that shrank a 40s input
    # down to 26.6s of output (9600/14400 = the exact ratio of the mismatch).
    stitcher = RtStitcher(tgt_sr=SAMPLE_RATE, block_s=block_s, crossfade_s=crossfade_s, search_s=search_s)
    zeros_window = np.zeros(framer.window_16k, dtype=np.float32)
    engine.convert_block(zeros_window, framer.block_16k, framer.skip_head_frames, framer.return_length_frames)
    print(f"gpu_mem_after_load_mb={gpu_mem_mb()}", flush=True)

    source = load48k_loop(
        [Path("/tmp/ru.wav"), Path("/tmp/en.wav")], seconds=seconds
    )
    pcm16 = np.clip(np.rint(source * 32768.0), -32768, 32767).astype("<i2")

    n_frames = pcm16.size // FRAME_SAMPLES
    call_times = []
    out_chunks = []
    t_wall0 = time.perf_counter()
    calls = 0
    for i in range(n_frames):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window_16k = framer.push(frame)
        if window_16k is None:
            continue
        t0 = time.perf_counter()
        out_np = engine.convert_block_48k(
            window_16k, framer.block_16k, framer.skip_head_frames, framer.return_length_frames
        )
        call_times.append(time.perf_counter() - t0)
        out_chunks.append(stitcher.render(out_np))
        calls += 1
        if calls % 20 == 0:
            print(f"calls={calls} gpu_mem_mb={gpu_mem_mb()} last_processing_ms={call_times[-1]*1000:.1f}",
                  flush=True)
    wall = time.perf_counter() - t_wall0

    out_pcm = b"".join(out_chunks)
    out_audio = np.frombuffer(out_pcm, dtype="<i2").astype(np.float32) / 32768.0
    sf.write(str(out_path), out_audio, SAMPLE_RATE)

    call_times.sort()
    n = len(call_times)
    result = {
        "block_s": block_s,
        "extra_s": extra_s,
        "input_seconds": round(pcm16.size / SAMPLE_RATE, 3),
        "output_seconds": round(out_audio.size / SAMPLE_RATE, 3),
        "calls": n,
        "wall_s": round(wall, 2),
        "processing_p50_ms": round(call_times[n // 2] * 1000, 1) if n else None,
        "processing_p95_ms": round(call_times[int(0.95 * (n - 1))] * 1000, 1) if n else None,
        "processing_max_ms": round(call_times[-1] * 1000, 1) if n else None,
        "rtf_p50": round(call_times[n // 2] / block_s, 3) if n else None,
        "gpu_mem_final_mb": gpu_mem_mb(),
    }
    print("---RESULT-JSON---")
    print(json.dumps(result, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
