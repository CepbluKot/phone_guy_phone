"""CPU-only first-formant (F1) estimation via LPC, used for the *optional*
?formant=auto on /api/compare -- see rt_server.py's TARGET_F1_HZ.

Much less trustworthy than pitch.py's YIN-based F0 estimate: LPC formant
tracking is more sensitive to mic quality, noise, and frame-to-frame
octave-style errors than pitch detection is, and the useful correction
range for formant_shift is narrow (~+/-3 semitones) before it audibly
distorts -- so callers should clamp hard and treat this as a starting
guess to listen to, not a confident default the way auto-transpose is.
"""
from __future__ import annotations

import numpy as np

SR = 16000
FRAME = 800
HOP = 200
LPC_ORDER = 2 + SR // 1000
F1_MIN_HZ, F1_MAX_HZ = 150, 1200
MAX_BANDWIDTH_HZ = 400


def _lpc_coeffs(x: np.ndarray, order: int) -> np.ndarray | None:
    x = x * np.hamming(len(x))
    autocorr = np.correlate(x, x, mode="full")[len(x) - 1:][: order + 1]
    if autocorr[0] <= 0:
        return None
    a = np.zeros(order + 1)
    a[0] = 1.0
    e = autocorr[0]
    for i in range(1, order + 1):
        acc = autocorr[i] + np.sum(a[1:i] * autocorr[i - 1:0:-1])
        k = -acc / e if e != 0 else 0.0
        a_new = a.copy()
        for j in range(1, i):
            a_new[j] = a[j] + k * a[i - j]
        a_new[i] = k
        a = a_new
        e *= 1 - k * k
        if e <= 0:
            break
    return a


def _first_formant(a: np.ndarray) -> float | None:
    roots = np.roots(a)
    roots = roots[np.imag(roots) >= 0]
    formants = []
    for r in roots:
        if abs(r) < 1e-6:
            continue
        freq = np.angle(r) * SR / (2 * np.pi)
        bandwidth = -0.5 * SR / np.pi * np.log(abs(r) + 1e-12)
        if F1_MIN_HZ < freq < F1_MAX_HZ and bandwidth < MAX_BANDWIDTH_HZ:
            formants.append(freq)
    return min(formants) if formants else None


def estimate_median_f1(audio: np.ndarray, sr: int) -> float | None:
    """audio: mono float32/float64 in [-1, 1]. Returns the median first
    formant over active frames in Hz, or None if too little usable content
    was found to trust."""
    if sr != SR:
        from scipy.signal import resample_poly
        import math

        g = math.gcd(sr, SR)
        audio = resample_poly(audio, SR // g, sr // g)
    f1s = []
    for start in range(0, len(audio) - FRAME, HOP):
        frame = audio[start:start + FRAME]
        if np.max(np.abs(frame)) < 1e-3:
            continue
        a = _lpc_coeffs(frame.astype(np.float64), LPC_ORDER)
        if a is None:
            continue
        f1 = _first_formant(a)
        if f1 is not None:
            f1s.append(f1)
    if len(f1s) < 10:
        return None
    return float(np.median(f1s))
