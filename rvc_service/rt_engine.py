"""Experimental persistent adapter for infer/rtrvc.py (the streaming engine
realtime_gui.py uses), as an alternative to rvc_service/engine.py's use of
the offline infer/vc/pipeline.py. See docs/LATENCY_RESEARCH_SONNET_2026-09-07.md
for why. Not wired into rvc_service/server.py -- this is a research prototype.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import sys

import numpy as np

from .rt_chunks import resample_to

DEFAULT_ASSETS_ROOT = Path("/opt/voice-changer/experiments/phoneguy")
MODEL_NAME = "PhoneGuyfnaf1V1.pth"
INDEX_NAME = "added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
MODEL_INPUT_RATE = 16_000
SAMPLE_RATE = 48_000


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class RtEngine:
    """Load Phone Guy once via infer/rtrvc.py and convert 16kHz rolling
    windows block-by-block, keeping the pitch cache alive across calls."""

    def __init__(
        self,
        assets_root: str | Path | None = None,
        f0method: str = "rmvpe",
        index_rate: float = 0.6,
    ) -> None:
        root = Path(
            assets_root if assets_root is not None else os.environ.get("RVC_ASSETS_ROOT", DEFAULT_ASSETS_ROOT)
        ).resolve()
        self.f0method = f0method
        self._load(root, index_rate)

    def _load(self, root: Path, index_rate: float) -> None:
        upstream = root / "upstream"
        model = root / "models" / MODEL_NAME
        index = root / "models" / INDEX_NAME
        for path, description in (
            (upstream, "pinned RVC source"),
            (model, "RVC model"),
            (index, "RVC index"),
        ):
            if not path.exists():
                raise FileNotFoundError(f"{description} not found: {path}")

        os.environ.update(
            TORCH_FORCE_WEIGHTS_ONLY_LOAD="1",
            OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="1",
            HF_HUB_OFFLINE="1",
            rmvpe_root=str(upstream / "assets" / "rmvpe"),
        )
        upstream_text = str(upstream)
        if upstream_text in sys.path:
            sys.path.remove(upstream_text)
        sys.path.insert(0, upstream_text)

        with _working_directory(upstream):
            import torch
            from configs.config import Config
            from infer.rtrvc import RVC

            torch.set_num_threads(2)
            original_argv = sys.argv[:]
            sys.argv = [sys.argv[0]]
            try:
                config = Config()
            finally:
                sys.argv = original_argv
            if not str(config.device).startswith("cuda"):
                raise RuntimeError("RtEngine requires a supported CUDA GPU")
            config.is_half = False
            config.n_cpu = 2

            self._torch = torch
            self._rvc = RVC(0, 0, str(model), str(index), index_rate, config)
            self.tgt_sr = self._rvc.tgt_sr
            self._device = config.device
        # infer/rtrvc.py's get_f0_rmvpe/get_f0_fcpe lazy-load their model on
        # the *first inference call* (not during RVC.__init__ above) using a
        # path relative to cwd, not the rmvpe_root env var pipeline.py reads
        # -- so cwd must still be `upstream` for every convert_block() call,
        # not just while loading. os.chdir is process-global: fine here and
        # in rvc_service/server.py's single-GPU-worker-thread model (only one
        # inference call is ever in flight), but would race with any other
        # thread doing relative-path I/O in the same process -- flagged for
        # whoever does the real server integration, not fixed further here.
        self._upstream = upstream

    def set_transpose(self, semitones: float) -> None:
        """infer/rtrvc.py's RVC.f0_up_key: shifts the *extracted* pitch
        contour before synthesis (change_key just mutates the attribute,
        infer() reads it fresh every call -- no reload needed). Left at 0
        everywhere so far, which means the output's F0 contour is exactly
        the source speaker's own, just re-timbred -- a well-known reason a
        conversion still reads as "the source person doing an impression"
        rather than the target voice: RVC does not automatically retarget
        pitch register, only timbre. The right value is whatever closes the
        gap between the caller's natural range and the target model's
        trained range -- see rt_server.py's TARGET_MEDIAN_F0_HZ/pitch.py for
        the auto-computed version of this; fractional semitones are fine,
        infer/rtrvc.py's f0 *= 2**(key/12) doesn't require an integer."""
        self._rvc.change_key(semitones)

    def set_index_rate(self, rate: float) -> None:
        """infer/rtrvc.py's RVC.index_rate: how much of the target
        speaker's own nearest-neighbour HuBERT features (from the FAISS
        index) get blended in versus the source's raw features (see
        RVC.infer's index-search block). Higher pulls harder toward the
        target's own recorded timbre at some risk of artifacts if the
        index has thin coverage for a given sound."""
        self._rvc.change_index_rate(rate)

    def set_formant_shift(self, semitones: float) -> None:
        """infer/rtrvc.py's RVC.formant_shift: resamples the synthesis
        window by 2**(shift/12) before vocoding (see RVC.infer's `factor`),
        which moves formant frequencies -- i.e. the *vocal tract size* cue
        (what mainly reads as "a man" vs "a woman" vs "a child" independent
        of pitch) -- while infer() compensates the F0 extraction by the
        same amount (f0_up_key - formant_shift) so this doesn't also shift
        perceived pitch. transpose (set_transpose) only moves pitch
        register; this is the separate, also-always-0-until-now knob for
        vocal tract size, which is what actually carries the male/female/
        speaker-identity cue that survives pitch correction alone. Typical
        useful range is small (roughly -3..+3), unlike transpose's wider
        useful range -- large values distort quickly."""
        self._rvc.change_formant(semitones)

    def reset_pitch_cache(self) -> None:
        """Zero infer/rtrvc.py's cache_pitch/cache_pitchf. Call this whenever
        the engine switches from serving one session/speaker to another --
        see experiments/latency-sonnet/scripts/rt_restart_check.py: without
        it, up to ~1024 frames (a few seconds) of the previous speaker's
        pitch history keep sliding through and bias the next speaker's
        first calls."""
        self._rvc.cache_pitch.zero_()
        self._rvc.cache_pitchf.zero_()

    def convert_block(
        self,
        window_16k: np.ndarray,
        block_16k: int,
        skip_head_frames: int,
        return_length_frames: int,
        f0method: str | None = None,
    ) -> np.ndarray:
        """Run one rtrvc.py inference call; returns target-sample-rate audio
        covering the new crossfade+search+block portion (see RtStitcher).
        `f0method` overrides the instance default for this call only, so one
        loaded engine (one set of GPU-resident weights) can serve sessions
        that each picked a different pitch method -- see rt_server.py's
        VARIANTS, compared side by side in docs/LATENCY_VERDICT_SONNET_2026-09-07.md."""
        audio = np.asarray(window_16k, dtype=np.float32)
        if not np.isfinite(audio).all():
            raise ValueError("RtEngine input window must contain only finite samples")
        chunk = self._torch.from_numpy(audio).to(self._device)
        method = f0method or self.f0method
        with _working_directory(self._upstream), self._torch.no_grad():
            out = self._rvc.infer(chunk, block_16k, skip_head_frames, return_length_frames, method)
        result = out.detach().cpu().numpy().astype(np.float32)
        if not np.isfinite(result).all():
            raise RuntimeError("rtrvc.py returned non-finite audio")
        return result

    def convert_block_48k(
        self,
        window_16k: np.ndarray,
        block_16k: int,
        skip_head_frames: int,
        return_length_frames: int,
        f0method: str | None = None,
    ) -> np.ndarray:
        out = self.convert_block(window_16k, block_16k, skip_head_frames, return_length_frames, f0method)
        return resample_to(out, self.tgt_sr, SAMPLE_RATE)
