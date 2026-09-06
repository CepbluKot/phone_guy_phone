from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pedalboard import Pedalboard, HighpassFilter, LowpassFilter, Compressor

FRAME_BYTES = 1920


@dataclass(frozen=True)
class EffectSettings:
    pitch_semitones: float = -1.5
    effect_mix: float = 0.85
    noise_mix: float = 0.04
    output_gain_db: float = 0.0


def validate_settings(data: dict[str, object]) -> EffectSettings:
    if not isinstance(data, dict) or any(isinstance(v, bool) for v in data.values()):
        raise ValueError('settings must be a numeric object')
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


class VoiceProcessor:
    """Session-local filters and crossfaded delay-line pitch effect (20–60 ms history)."""

    def __init__(self, settings: EffectSettings):
        self.settings = settings
        self.filters = Pedalboard([HighpassFilter(cutoff_frequency_hz=350),
                                  LowpassFilter(cutoff_frequency_hz=3400),
                                  Compressor(threshold_db=-18, ratio=3)])
        self.history = np.zeros(8192, dtype=np.float32)
        self.position = 0
        self.phase = 0.0

    def process(self, frame: bytes) -> bytes:
        if len(frame) != FRAME_BYTES:
            raise ValueError("audio frame must contain exactly 960 mono S16LE samples")
        source = np.frombuffer(frame, dtype='<i2').astype(np.float32) / 32768.0
        timeline = self.position + np.arange(960)
        self.history[timeline % len(self.history)] = source
        settings = self.settings
        if abs(settings.pitch_semitones) < 0.001:
            pitched = source
        else:
            step = 1 - 2 ** (settings.pitch_semitones / 12)
            phase = (self.phase + np.arange(960) * step) % 1920
            other = (phase + 960) % 1920

            def read(delay):
                positions = timeline - 960 - delay
                lower = np.floor(positions).astype(np.int64)
                fraction = positions - lower
                return (self.history[lower % 8192] * (1 - fraction)
                        + self.history[(lower + 1) % 8192] * fraction)

            weight = np.sin(np.pi * phase / 1920) ** 2
            pitched = (read(phase) * weight + read(other) * (1 - weight)).astype(np.float32)
            self.phase = (self.phase + 960 * step) % 1920
        band = self.filters(pitched, 48000, reset=False)
        effected = np.tanh(band * 2) / 1.5
        if float(np.sqrt(np.mean(source ** 2))) > 0.001:
            effected += np.sin(timeline * (2 * np.pi * 60 / 48000)) * settings.noise_mix * 0.2
        mixed = source * (1 - settings.effect_mix) + effected * settings.effect_mix
        gained = mixed * 10 ** (settings.output_gain_db / 20)
        self.position += 960
        return np.rint(np.clip(gained, -1, 32767 / 32768) * 32768).astype('<i2').tobytes()


def process_pcm16(frame: bytes, settings: EffectSettings, sample_rate: int = 48000) -> bytes:
    """One-shot helper; live audio must retain a VoiceProcessor for the session."""
    if sample_rate != 48000:
        raise ValueError("only 48000 Hz audio is supported")
    return VoiceProcessor(settings).process(frame)
