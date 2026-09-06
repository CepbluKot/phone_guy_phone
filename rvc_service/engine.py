"""Persistent in-memory adapter for the pinned RVC inference pipeline."""

from __future__ import annotations

import math
import os
from pathlib import Path
import sys

import numpy as np

from .chunks import (
    ALIGNMENT_SEARCH_SAMPLES,
    CONTEXT_SAMPLES,
    HOP_SAMPLES,
    OVERLAP_SAMPLES,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
)


DEFAULT_ASSETS_ROOT = Path("/opt/voice-changer/experiments/phoneguy")
MODEL_NAME = "PhoneGuyfnaf1V1.pth"
INDEX_NAME = "added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
MODEL_SAMPLE_RATE = 32_000
MODEL_INPUT_RATE = 16_000


class Engine:
    """Load Phone Guy once, warm it, and convert normalized array windows."""

    def __init__(self, assets_root: str | Path | None = None) -> None:
        root = Path(
            assets_root
            if assets_root is not None
            else os.environ.get("RVC_ASSETS_ROOT", DEFAULT_ASSETS_ROOT)
        ).resolve()
        self._load(root)
        # Deliberately bypass the public silence shortcut: readiness means the
        # full production-shaped CUDA/RMVPE path has completed once.
        self._convert_with_model(np.zeros(WINDOW_SAMPLES, dtype=np.float32))

    def convert(self, window: np.ndarray) -> np.ndarray:
        """Convert one normalized 48 kHz contextual window entirely in memory."""
        audio = np.asarray(window)
        if audio.ndim != 1 or audio.size != WINDOW_SAMPLES:
            raise ValueError(
                f"RVC input window must contain exactly {WINDOW_SAMPLES} samples"
            )
        if not np.isfinite(audio).all():
            raise ValueError("RVC input window must contain only finite samples")
        audio = audio.astype(np.float32, copy=False)
        if not np.any(audio):
            return np.zeros(WINDOW_SAMPLES, dtype=np.float32)
        return self._convert_with_model(audio)

    def _load(self, root: Path) -> None:
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
            RVC_CUDA_GRAPH="0",
            OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="1",
            HF_HUB_OFFLINE="1",
            weight_root=str(model.parent),
            index_root=str(model.parent),
            outside_index_root=str(model.parent),
            rmvpe_root=str(upstream / "assets" / "rmvpe"),
        )
        upstream_text = str(upstream)
        if upstream_text in sys.path:
            sys.path.remove(upstream_text)
        sys.path.insert(0, upstream_text)

        import torch
        from configs.config import Config
        from infer.vc.modules import VC
        from infer.vc.utils import load_hubert

        torch.set_num_threads(2)
        original_argv = sys.argv[:]
        sys.argv = [sys.argv[0]]
        try:
            config = Config()
        finally:
            sys.argv = original_argv
        if not str(config.device).startswith("cuda"):
            raise RuntimeError("RVC Engine requires a supported CUDA GPU")
        config.is_half = False
        config.dtype = torch.float32
        config.n_cpu = 2

        vc = VC(config)
        vc.get_vc(model.name)
        vc.hubert_model = load_hubert(config)
        if vc.tgt_sr != MODEL_SAMPLE_RATE:
            raise RuntimeError(f"unexpected Phone Guy sample rate: {vc.tgt_sr}")

        self._vc = vc
        self._index = str(index)

    def _convert_with_model(self, window: np.ndarray) -> np.ndarray:
        from scipy.signal import resample_poly

        model_input = resample_poly(window, MODEL_INPUT_RATE, SAMPLE_RATE).astype(
            np.float32
        )
        peak_ratio = float(np.max(np.abs(model_input))) / 0.95
        if peak_ratio > 1.0:
            model_input /= peak_ratio

        converted = self._vc.pipeline.pipeline(
            self._vc.hubert_model,
            self._vc.net_g,
            0,
            model_input,
            [0.0, 0.0, 0.0],
            0,
            "rmvpe",
            self._index,
            0.6,
            self._vc.if_f0,
            self._vc.tgt_sr,
            0,
            1.0,
            self._vc.version,
            0.33,
        )
        normalized = np.asarray(converted, dtype=np.float32) / 32768.0
        if (
            normalized.ndim != 1
            or not normalized.size
            or not np.isfinite(normalized).all()
        ):
            raise RuntimeError("RVC pipeline returned invalid audio")

        divisor = math.gcd(self._vc.tgt_sr, SAMPLE_RATE)
        output = resample_poly(
            normalized,
            SAMPLE_RATE // divisor,
            self._vc.tgt_sr // divisor,
        ).astype(np.float32)
        minimum = (
            CONTEXT_SAMPLES
            + HOP_SAMPLES
            + ALIGNMENT_SEARCH_SAMPLES
            + OVERLAP_SAMPLES
        )
        if output.size < minimum:
            raise RuntimeError(
                f"RVC output is too short for aligned rendering: {output.size} samples"
            )
        if output.size < WINDOW_SAMPLES:
            output = np.pad(output, (0, WINDOW_SAMPLES - output.size), mode="edge")
        else:
            output = output[:WINDOW_SAMPLES]
        return np.clip(output, -1.0, 1.0).astype(np.float32, copy=False)
