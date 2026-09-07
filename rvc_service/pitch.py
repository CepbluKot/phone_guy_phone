"""CPU-only median-F0 estimation (YIN) used to auto-compute a transpose
value for /api/compare, instead of asking the caller to guess a semitone
number by ear (see rt_server.py's compare() and TARGET_MEDIAN_F0_HZ).

Not RMVPE/FCPE: those need the GPU and are already busy doing the actual
conversion. This only needs a decent "typical pitch of this clip" number,
which a classic monophonic pitch tracker gets close enough to for picking
a semitone shift -- sub-Hz accuracy doesn't matter here.
"""
from __future__ import annotations

import numpy as np

SR = 16000
FRAME = 1024
HOP = 320
FMIN, FMAX = 60, 500
THRESHOLD = 0.15


def _yin_frame_f0(x: np.ndarray) -> float:
    x = x.astype(np.float64)
    if np.max(np.abs(x)) < 1e-4:
        return 0.0
    tau_max = SR // FMIN
    tau_min = SR // FMAX
    if tau_max >= len(x):
        return 0.0
    diff = np.zeros(tau_max + 1)
    for tau in range(1, tau_max + 1):
        d = x[: len(x) - tau] - x[tau:]
        diff[tau] = np.sum(d * d)
    cmnd = np.ones(tau_max + 1)
    running_sum = 0.0
    for tau in range(1, tau_max + 1):
        running_sum += diff[tau]
        cmnd[tau] = diff[tau] * tau / running_sum if running_sum > 0 else 1.0
    tau_est = -1
    for tau in range(tau_min, tau_max + 1):
        if cmnd[tau] < THRESHOLD:
            while tau + 1 <= tau_max and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            tau_est = tau
            break
    if tau_est == -1:
        tau_est = tau_min + int(np.argmin(cmnd[tau_min:tau_max + 1]))
        if cmnd[tau_est] > 0.5:
            return 0.0
    if 1 <= tau_est < tau_max:
        s0, s1, s2 = cmnd[tau_est - 1], cmnd[tau_est], cmnd[tau_est + 1]
        denom = 2 * (2 * s1 - s2 - s0)
        if denom != 0:
            tau_est = tau_est + (s2 - s0) / denom
    return SR / tau_est if tau_est > 0 else 0.0


def estimate_median_f0(audio: np.ndarray, sr: int) -> float | None:
    """audio: mono float32/float64 in [-1, 1]. Returns the median F0 over
    voiced frames in Hz, or None if too little voiced content was found to
    trust (silence, noise, or a clip too short)."""
    if sr != SR:
        from scipy.signal import resample_poly
        import math

        g = math.gcd(sr, SR)
        audio = resample_poly(audio, SR // g, sr // g)
    f0s = []
    for start in range(0, len(audio) - FRAME, HOP):
        f0 = _yin_frame_f0(audio[start:start + FRAME])
        if f0 > 0:
            f0s.append(f0)
    if len(f0s) < 10:
        return None
    return float(np.median(f0s))
