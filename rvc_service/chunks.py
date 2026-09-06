"""Session-local framing and seamless fixed-timeline rendering."""

from __future__ import annotations

import numpy as np


SAMPLE_RATE = 48_000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
HOP_SAMPLES = SAMPLE_RATE * 2
CONTEXT_SAMPLES = SAMPLE_RATE // 2
LOOKAHEAD_SAMPLES = SAMPLE_RATE // 10
OVERLAP_SAMPLES = SAMPLE_RATE * 40 // 1_000
ALIGNMENT_SEARCH_SAMPLES = FRAME_SAMPLES
WINDOW_SAMPLES = CONTEXT_SAMPLES + HOP_SAMPLES + LOOKAHEAD_SAMPLES


class Chunker:
    """Build contextual inference windows and render them in submission order."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Discard every piece of receiver and renderer state for this session."""
        self._input = np.empty(0, dtype=np.float32)
        self._input_start = 0
        self._next_hop_start = 0
        self._render_overlap: np.ndarray | None = None

    def push(self, frame: bytes) -> np.ndarray | None:
        """Accept one 20 ms PCM16 frame and return the next contextual window."""
        if (
            not isinstance(frame, (bytes, bytearray, memoryview))
            or len(frame) != FRAME_BYTES
        ):
            raise ValueError(f"PCM frame must contain exactly {FRAME_BYTES} bytes")

        pcm = np.frombuffer(frame, dtype="<i2")
        samples = pcm.astype(np.float32) / 32768.0
        self._input = np.concatenate((self._input, samples))

        required_end = self._next_hop_start + HOP_SAMPLES + LOOKAHEAD_SAMPLES
        available_end = self._input_start + self._input.size
        if available_end < required_end:
            return None

        window_start = self._next_hop_start - CONTEXT_SAMPLES
        stored_start = max(0, window_start)
        offset = stored_start - self._input_start
        stored = self._input[offset : required_end - self._input_start]
        prefix = max(0, -window_start)
        window = np.pad(stored, (prefix, 0)).astype(np.float32, copy=False)
        if window.shape != (WINDOW_SAMPLES,):
            raise RuntimeError(f"assembled RVC window has {window.size} samples")

        self._next_hop_start += HOP_SAMPLES
        retain_from = max(0, self._next_hop_start - CONTEXT_SAMPLES)
        discard = retain_from - self._input_start
        if discard > 0:
            self._input = self._input[discard:].copy()
            self._input_start = retain_from
        return window

    def render(self, converted: np.ndarray) -> bytes:
        """Align one converted window and emit exactly one 2-second PCM hop."""
        audio = np.asarray(converted)
        if audio.ndim != 1 or audio.size != WINDOW_SAMPLES:
            raise ValueError(
                f"converted window must contain exactly {WINDOW_SAMPLES} samples"
            )
        if not np.isfinite(audio).all():
            raise ValueError("converted window must contain only finite samples")
        audio = audio.astype(np.float32, copy=False)

        start = CONTEXT_SAMPLES
        if self._render_overlap is not None:
            start = self._aligned_start(audio, self._render_overlap)

        output = audio[start : start + HOP_SAMPLES].copy()
        future = audio[
            start + HOP_SAMPLES : start + HOP_SAMPLES + OVERLAP_SAMPLES
        ].copy()
        if output.size != HOP_SAMPLES or future.size != OVERLAP_SAMPLES:
            raise ValueError("converted window does not retain enough aligned look-ahead")

        if self._render_overlap is not None:
            fade_in = (
                np.arange(OVERLAP_SAMPLES, dtype=np.float32) + 1
            ) / OVERLAP_SAMPLES
            output[:OVERLAP_SAMPLES] = (
                self._render_overlap * (1.0 - fade_in)
                + output[:OVERLAP_SAMPLES] * fade_in
            )
        self._render_overlap = future

        scaled = np.rint(np.clip(output, -1.0, 1.0) * 32768.0)
        return np.clip(scaled, -32768, 32767).astype("<i2").tobytes()

    @staticmethod
    def _aligned_start(audio: np.ndarray, reference: np.ndarray) -> int:
        nominal = CONTEXT_SAMPLES
        reference_energy = float(np.dot(reference, reference))
        if reference_energy <= np.finfo(np.float32).eps:
            return nominal

        best_start = nominal
        nominal_overlap = audio[nominal : nominal + OVERLAP_SAMPLES]
        best_error = float(np.mean(np.square(nominal_overlap - reference)))
        for distance in range(1, ALIGNMENT_SEARCH_SAMPLES + 1):
            for candidate_start in (nominal - distance, nominal + distance):
                candidate_end = candidate_start + OVERLAP_SAMPLES
                render_end = candidate_start + HOP_SAMPLES + OVERLAP_SAMPLES
                if candidate_start < 0 or render_end > audio.size:
                    continue
                candidate = audio[candidate_start:candidate_end]
                error = float(np.mean(np.square(candidate - reference)))
                if error < best_error:
                    best_error = error
                    best_start = candidate_start
        return best_start
