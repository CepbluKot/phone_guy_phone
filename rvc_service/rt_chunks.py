"""Session-local framing/stitching for the proposed infer/rtrvc.py streaming
path. Pure numpy/scipy, CPU-only, GPU-free -- mirrors rvc_service/chunks.py's
split (framing+render here, GPU inference in rt_engine.py) so this half is
unit-testable without CUDA. Experimental: not wired into rvc_service/server.py.

The render/stitch algorithm below is a direct, parameterized port of
chunks.Chunker.render/_aligned_start: each call emits exactly `block_n`
samples (never more), blending the start of the new block against a
`crossfade_n`-long "pending" tail held back from the previous call, and
holding back this call's own trailing crossfade_n samples for the next one.
An earlier draft of this (experiments/latency-sonnet/scripts/new_way.py)
instead appended crossfade_n+block_n samples every call, which double-counts
the overlap and made a 6s input render as 7.2s of output -- fixed here.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import resample_poly

SAMPLE_RATE = 48_000
MODEL_INPUT_RATE = 16_000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
RESAMPLE_MARGIN_SECONDS = 0.02


class RtFramer:
    """Accumulate 20ms 48kHz PCM16 frames into fixed block_s chunks and keep
    a persistent 16kHz rolling buffer sized for extra_s+crossfade_s+2*search_s
    +block_s of history, resampling only each new block (plus a small margin
    to prime the anti-alias filter) instead of the whole window every call --
    the same incremental-resample trick realtime_gui.py uses."""

    def __init__(self, block_s: float, extra_s: float, crossfade_s: float, search_s: float) -> None:
        if block_s <= 0 or extra_s < 0 or crossfade_s < 0 or search_s < 0:
            raise ValueError("durations must be non-negative, block_s must be positive")
        self.block_s = block_s
        self.extra_s = extra_s
        self.crossfade_s = crossfade_s
        self.search_s = search_s
        self.block_48k = round(block_s * SAMPLE_RATE)
        self.block_16k = round(block_s * MODEL_INPUT_RATE)
        # window must hold extra (history) + crossfade + 2*search (alignment
        # margin on both sides of nominal) + block, matching return_length
        # below so RtStitcher always has enough samples to search+blend.
        window_s = extra_s + crossfade_s + 2 * search_s + block_s
        self.window_48k = round(window_s * SAMPLE_RATE)
        self.window_16k = round(window_s * MODEL_INPUT_RATE)
        self.margin_48k = round(RESAMPLE_MARGIN_SECONDS * SAMPLE_RATE)
        self.skip_head_frames = round(extra_s * 100)
        self.return_length_frames = round((crossfade_s + 2 * search_s + block_s) * 100)
        self.reset()

    def reset(self) -> None:
        self._buf_48k = np.zeros(self.window_48k, dtype=np.float32)
        self._buf_16k = np.zeros(self.window_16k, dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)

    def push(self, frame: bytes) -> np.ndarray | None:
        """Accept one 20ms PCM16 48kHz frame; return the 16kHz rolling buffer
        (ready to feed straight into infer/rtrvc.py's RVC.infer) once a full
        block_s worth of new audio has accumulated, else None."""
        if not isinstance(frame, (bytes, bytearray, memoryview)) or len(frame) != FRAME_BYTES:
            raise ValueError(f"PCM frame must contain exactly {FRAME_BYTES} bytes")
        pcm = np.frombuffer(frame, dtype="<i2")
        samples = pcm.astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, samples))
        if self._pending.size < self.block_48k:
            return None
        new_block, self._pending = self._pending[: self.block_48k], self._pending[self.block_48k :]

        self._buf_48k[: -self.block_48k] = self._buf_48k[self.block_48k :]
        self._buf_48k[-self.block_48k :] = new_block

        margin = min(self.margin_48k, self._buf_48k.size - self.block_48k)
        resample_input = self._buf_48k[-(self.block_48k + margin) :]
        resampled = resample_poly(resample_input, MODEL_INPUT_RATE, SAMPLE_RATE).astype(np.float32)
        tail = resampled[-self.block_16k :]
        if tail.size < self.block_16k:
            tail = np.pad(tail, (self.block_16k - tail.size, 0))

        self._buf_16k[: -self.block_16k] = self._buf_16k[self.block_16k :]
        self._buf_16k[-self.block_16k :] = tail
        return self._buf_16k.copy()


class RtStitcher:
    """Parameterized port of chunks.Chunker.render/_aligned_start for
    infer/rtrvc.py output: each call's model output covers
    [crossfade_n + 2*search_n + block_n] samples ending at the current block
    boundary. Exactly `block_n` samples are emitted per call (never more),
    found by SOLA-searching +/-search_n around the nominal offset for the
    best match against the previous call's held-back tail, and blending the
    new block's own start against that tail -- same shape as chunks.py, just
    without a fixed WINDOW_SAMPLES tied to one global window size."""

    def __init__(self, tgt_sr: int, block_s: float, crossfade_s: float, search_s: float) -> None:
        self.tgt_sr = tgt_sr
        self.block_n = round(block_s * tgt_sr)
        self.crossfade_n = round(crossfade_s * tgt_sr)
        self.search_n = round(search_s * tgt_sr)
        self.expected_n = self.block_n + self.crossfade_n + 2 * self.search_n
        self._pending: np.ndarray | None = None

    def reset(self) -> None:
        self._pending = None

    def render(self, out_np: np.ndarray) -> bytes:
        out_np = np.asarray(out_np, dtype=np.float32)
        if out_np.size < self.expected_n:
            out_np = np.pad(out_np, (0, self.expected_n - out_np.size))
        if not np.isfinite(out_np).all():
            raise ValueError("rtrvc output block must contain only finite samples")

        nominal = self.search_n
        start = nominal
        if self._pending is not None and self.search_n:
            start = self._aligned_start(out_np, self._pending, nominal)

        block = out_np[start : start + self.block_n].copy()
        if block.size < self.block_n:
            block = np.pad(block, (0, self.block_n - block.size))

        if self._pending is not None and self.crossfade_n:
            fade = (np.arange(self.crossfade_n, dtype=np.float32) + 1) / self.crossfade_n
            block[: self.crossfade_n] = self._pending * (1.0 - fade) + block[: self.crossfade_n] * fade

        pending_start = start + self.block_n
        pending = out_np[pending_start : pending_start + self.crossfade_n]
        self._pending = np.pad(pending, (0, self.crossfade_n - pending.size)) if pending.size < self.crossfade_n else pending

        scaled = np.rint(np.clip(block, -1.0, 1.0) * 32768.0)
        return np.clip(scaled, -32768, 32767).astype("<i2").tobytes()

    def _aligned_start(self, out_np: np.ndarray, reference: np.ndarray, nominal: int) -> int:
        ref_energy = float(np.dot(reference, reference))
        if ref_energy <= np.finfo(np.float32).eps:
            return nominal
        best_start = nominal
        nominal_head = out_np[nominal : nominal + self.crossfade_n]
        best_error = float(np.mean(np.square(nominal_head - reference)))
        for distance in range(1, self.search_n + 1):
            for candidate in (nominal - distance, nominal + distance):
                end = candidate + self.block_n + self.crossfade_n
                if candidate < 0 or end > out_np.size:
                    continue
                head = out_np[candidate : candidate + self.crossfade_n]
                error = float(np.mean(np.square(head - reference)))
                if error < best_error:
                    best_error, best_start = error, candidate
        return best_start


def resample_to(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return np.asarray(audio, dtype=np.float32)
    g = math.gcd(src_sr, dst_sr)
    return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)
