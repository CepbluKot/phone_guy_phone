#!/usr/bin/env python3
"""Offline batch runner for the "GPT" branch's (research/latency-optimization)
low-latency profile -- their v2 canary logic, deployed live at
vm-voice-1.lan.awesomeio.ru/ws/rvc-v2, run here against an isolated copy of
their exact chunks.py/engine.py (copied read-only from the deployed
production release, never the live process) so a compare-page recording can
go through their approach too without touching production or its GPU slot.

Usage: run from a directory containing a `rvc_service/` package that is
THEIR chunks.py + engine.py (not ours -- different module, same dotted
name, hence run as its own subprocess/interpreter to avoid colliding with
our own rvc_service.* already imported elsewhere).

    python3 run_gpt_variant.py --input in.wav --output out.wav [--assets-root PATH]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import soundfile as sf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--assets-root", default=None)
    args = parser.parse_args()

    from rvc_service.chunks import FRAME_SAMPLES, LOW_LATENCY_PROFILE, SAMPLE_RATE, Chunker
    from rvc_service.engine import Engine

    audio, sr = sf.read(args.input, dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        print(f"error: input must be {SAMPLE_RATE} Hz, got {sr}", file=sys.stderr)
        return 1
    if audio.ndim != 1:
        audio = audio.mean(axis=1).astype(np.float32)

    pcm16 = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2")
    pad = (-pcm16.size) % FRAME_SAMPLES
    if pad:
        pcm16 = np.pad(pcm16, (0, pad))

    engine = Engine(args.assets_root)
    chunker = Chunker(LOW_LATENCY_PROFILE)
    out_chunks = []
    for i in range(pcm16.size // FRAME_SAMPLES):
        frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
        window = chunker.push(frame)
        if window is None:
            continue
        converted = engine.convert(window)
        out_chunks.append(chunker.render(converted))

    if not out_chunks:
        print("error: input too short for even one hop under LOW_LATENCY_PROFILE", file=sys.stderr)
        return 1

    pcm = b"".join(out_chunks)
    out = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    sf.write(args.output, out, SAMPLE_RATE, subtype="PCM_16")
    print(f"ok: {out.size} samples ({out.size / SAMPLE_RATE:.2f}s) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
