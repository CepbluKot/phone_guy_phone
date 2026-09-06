from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME_BYTES = 1920


@dataclass(frozen=True)
class EffectSettings:
    pitch_semitones: float = -1.5
    effect_mix: float = 0.85
    noise_mix: float = 0.04
    output_gain_db: float = 0.0


def validate_settings(data: dict[str, object]) -> EffectSettings:
    values = {
        "pitch_semitones": data.get("pitchSemitones", -1.5),
        "effect_mix": data.get("effectMix", 0.85),
        "noise_mix": data.get("noiseMix", 0.04),
        "output_gain_db": data.get("outputGainDb", 0.0),
    }
    try:
        settings = EffectSettings(**{key: float(value) for key, value in values.items()})
    except (TypeError, ValueError) as exc:
        raise ValueError("settings must be numeric") from exc
    if not -6 <= settings.pitch_semitones <= 6:
        raise ValueError("pitchSemitones must be between -6 and 6")
    if not 0 <= settings.effect_mix <= 1:
        raise ValueError("effectMix must be between 0 and 1")
    if not 0 <= settings.noise_mix <= 0.15:
        raise ValueError("noiseMix must be between 0 and 0.15")
    if not -18 <= settings.output_gain_db <= 12:
        raise ValueError("outputGainDb must be between -18 and 12")
    return settings


def process_pcm16(frame: bytes, settings: EffectSettings, sample_rate: int = 48000) -> bytes:
    if len(frame) != FRAME_BYTES:
        raise ValueError("audio frame must contain exactly 960 mono S16LE samples")
    if sample_rate != 48000:
        raise ValueError("only 48000 Hz audio is supported")
    source = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
    # Simple first-order high-pass then low-pass creates the narrow telephone band.
    high = np.empty_like(source)
    high[0] = source[0]
    high[1:] = source[1:] - 0.96 * source[:-1]
    band = np.empty_like(high)
    band[0] = high[0]
    for index in range(1, len(high)):
        band[index] = band[index - 1] + 0.22 * (high[index] - band[index - 1])
    # Soft saturation, deterministic line hum, and a bounded output gain.
    effected = np.tanh(band * 3.0)
    phase = np.arange(len(source), dtype=np.float32) * (2 * np.pi * 60 / sample_rate)
    effected += np.sin(phase) * settings.noise_mix
    mixed = source * (1 - settings.effect_mix) + effected * settings.effect_mix
    gained = mixed * (10 ** (settings.output_gain_db / 20))
    return np.clip(gained, -1.0, 0.9999).astype("<f4").__mul__(32768).astype("<i2").tobytes()
