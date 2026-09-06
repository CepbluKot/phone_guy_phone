import pytest

from app.dsp import EffectSettings, process_pcm16, validate_settings


def test_process_preserves_20ms_frame_size_and_changes_signal() -> None:
    source = b"\x00\x10" * 960

    result = process_pcm16(source, EffectSettings())

    assert len(result) == 1920
    assert result != source


@pytest.mark.parametrize(
    ("key", "value"),
    [("pitchSemitones", 7), ("effectMix", 1.1), ("noiseMix", -0.1)],
)
def test_settings_reject_out_of_range_values(key: str, value: float) -> None:
    with pytest.raises(ValueError):
        validate_settings({key: value})
