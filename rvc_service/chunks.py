"""Session-local framing and seamless fixed-timeline rendering."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


SAMPLE_RATE = 48_000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2


@dataclass(frozen=True)
class RvcProfile:
    """One wire-compatible RVC timeline, expressed in 48 kHz samples."""

    version: int
    hop_samples: int
    context_samples: int
    lookahead_samples: int
    overlap_samples: int
    alignment_search_samples: int

    @property
    def window_samples(self) -> int:
        return self.context_samples + self.hop_samples + self.lookahead_samples


DEFAULT_PROFILE = RvcProfile(
    version=1,
    hop_samples=SAMPLE_RATE * 2,
    context_samples=SAMPLE_RATE // 2,
    lookahead_samples=SAMPLE_RATE // 10,
    overlap_samples=SAMPLE_RATE * 40 // 1_000,
    alignment_search_samples=FRAME_SAMPLES,
)
LOW_LATENCY_PROFILE = RvcProfile(
    version=2,
    hop_samples=SAMPLE_RATE,
    context_samples=SAMPLE_RATE // 2,
    lookahead_samples=SAMPLE_RATE // 10,
    overlap_samples=SAMPLE_RATE * 40 // 1_000,
    alignment_search_samples=FRAME_SAMPLES,
)

# Preserve the version-1 names for the established service, tests and docs.
HOP_SAMPLES = DEFAULT_PROFILE.hop_samples
CONTEXT_SAMPLES = DEFAULT_PROFILE.context_samples
LOOKAHEAD_SAMPLES = DEFAULT_PROFILE.lookahead_samples
OVERLAP_SAMPLES = DEFAULT_PROFILE.overlap_samples
ALIGNMENT_SEARCH_SAMPLES = DEFAULT_PROFILE.alignment_search_samples
WINDOW_SAMPLES = DEFAULT_PROFILE.window_samples


class Chunker:
    """Build contextual inference windows and render them in submission order."""

    def __init__(self, profile: RvcProfile = DEFAULT_PROFILE) -> None:
        if not isinstance(profile, RvcProfile):
            raise TypeError("profile must be an RvcProfile")
        self.profile = profile
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

        required_end = (
            self._next_hop_start
            + self.profile.hop_samples
            + self.profile.lookahead_samples
        )
        available_end = self._input_start + self._input.size
        if available_end < required_end:
            return None

        window_start = self._next_hop_start - self.profile.context_samples
        stored_start = max(0, window_start)
        offset = stored_start - self._input_start
        stored = self._input[offset : required_end - self._input_start]
        prefix = max(0, -window_start)
        window = np.pad(stored, (prefix, 0)).astype(np.float32, copy=False)
        if window.shape != (self.profile.window_samples,):
            raise RuntimeError(f"assembled RVC window has {window.size} samples")

        self._next_hop_start += self.profile.hop_samples
        retain_from = max(0, self._next_hop_start - self.profile.context_samples)
        discard = retain_from - self._input_start
        if discard > 0:
            self._input = self._input[discard:].copy()
            self._input_start = retain_from
        return window

    def render(self, converted: np.ndarray) -> bytes:
        """Align one converted window and emit exactly one 2-second PCM hop."""
        audio = np.asarray(converted)
        if audio.ndim != 1 or audio.size != self.profile.window_samples:
            raise ValueError(
                "converted window must contain exactly "
                f"{self.profile.window_samples} samples"
            )
        if not np.isfinite(audio).all():
            raise ValueError("converted window must contain only finite samples")
        audio = audio.astype(np.float32, copy=False)

        start = self.profile.context_samples
        if self._render_overlap is not None:
            start = self._aligned_start(audio, self._render_overlap)

        output = audio[start : start + self.profile.hop_samples].copy()
        future = audio[
            start + self.profile.hop_samples : start
            + self.profile.hop_samples
            + self.profile.overlap_samples
        ].copy()
        if (
            output.size != self.profile.hop_samples
            or future.size != self.profile.overlap_samples
        ):
            raise ValueError("converted window does not retain enough aligned look-ahead")

        if self._render_overlap is not None:
            fade_in = (
                np.arange(self.profile.overlap_samples, dtype=np.float32) + 1
            ) / self.profile.overlap_samples
            output[:OVERLAP_SAMPLES] = (
                self._render_overlap * (1.0 - fade_in)
                + output[:OVERLAP_SAMPLES] * fade_in
            )
        self._render_overlap = future

        scaled = np.rint(np.clip(output, -1.0, 1.0) * 32768.0)
        return np.clip(scaled, -32768, 32767).astype("<i2").tobytes()

    def _aligned_start(self, audio: np.ndarray, reference: np.ndarray) -> int:
        nominal = self.profile.context_samples
        reference_energy = float(np.dot(reference, reference))
        if reference_energy <= np.finfo(np.float32).eps:
            return nominal

        best_start = nominal
        nominal_overlap = audio[
            nominal : nominal + self.profile.overlap_samples
        ]
        best_error = float(np.mean(np.square(nominal_overlap - reference)))
        for distance in range(1, self.profile.alignment_search_samples + 1):
            for candidate_start in (nominal - distance, nominal + distance):
                candidate_end = candidate_start + self.profile.overlap_samples
                render_end = (
                    candidate_start
                    + self.profile.hop_samples
                    + self.profile.overlap_samples
                )
                if candidate_start < 0 or render_end > audio.size:
                    continue
                candidate = audio[candidate_start:candidate_end]
                error = float(np.mean(np.square(candidate - reference)))
                if error < best_error:
                    best_error = error
                    best_start = candidate_start
        return best_start
