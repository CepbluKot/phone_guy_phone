from pathlib import Path

import numpy as np
import pytest

import rvc_service.engine as engine_module
from rvc_service.chunks import (
    CONTEXT_SAMPLES,
    FRAME_BYTES,
    FRAME_SAMPLES,
    HOP_SAMPLES,
    LOOKAHEAD_SAMPLES,
    WINDOW_SAMPLES,
    Chunker,
)
from rvc_service.engine import Engine


def _frames(samples: np.ndarray):
    for offset in range(0, len(samples), FRAME_SAMPLES):
        yield samples[offset : offset + FRAME_SAMPLES].astype("<i2").tobytes()


def _push_all(chunker: Chunker, samples: np.ndarray) -> list[np.ndarray]:
    return [
        window
        for frame in _frames(samples)
        if (window := chunker.push(frame)) is not None
    ]


def test_push_builds_fixed_windows_with_previous_context_and_lookahead() -> None:
    source = (
        (np.arange(HOP_SAMPLES * 2 + LOOKAHEAD_SAMPLES) % 20000) - 10000
    ).astype("<i2")

    windows = _push_all(Chunker(), source)

    assert len(windows) == 2
    assert all(window.shape == (WINDOW_SAMPLES,) for window in windows)
    np.testing.assert_array_equal(windows[0][:CONTEXT_SAMPLES], 0)
    np.testing.assert_allclose(
        windows[0][CONTEXT_SAMPLES:],
        source[: HOP_SAMPLES + LOOKAHEAD_SAMPLES] / 32768.0,
    )
    np.testing.assert_allclose(
        windows[1][:CONTEXT_SAMPLES],
        source[HOP_SAMPLES - CONTEXT_SAMPLES : HOP_SAMPLES] / 32768.0,
    )
    np.testing.assert_allclose(
        windows[1][CONTEXT_SAMPLES:],
        source[HOP_SAMPLES : HOP_SAMPLES * 2 + LOOKAHEAD_SAMPLES] / 32768.0,
    )


@pytest.mark.parametrize(
    "bad_frame", [b"", b"\0" * (FRAME_BYTES - 2), b"\0" * (FRAME_BYTES + 2)]
)
def test_push_rejects_malformed_frames_without_consuming_input(bad_frame: bytes) -> None:
    chunker = Chunker()
    good_frame = (np.ones(FRAME_SAMPLES, dtype="<i2") * 1234).tobytes()

    with pytest.raises(ValueError, match="1920"):
        chunker.push(bad_frame)
    for _ in range((HOP_SAMPLES + LOOKAHEAD_SAMPLES) // FRAME_SAMPLES - 1):
        assert chunker.push(good_frame) is None

    window = chunker.push(good_frame)
    assert window is not None
    np.testing.assert_array_equal(window[:CONTEXT_SAMPLES], 0)


def test_render_emits_exact_timeline_and_preserves_identity_ramp_at_seam() -> None:
    source = np.linspace(-0.8, 0.8, HOP_SAMPLES * 2 + LOOKAHEAD_SAMPLES)
    pcm = np.rint(source * 32767).astype("<i2")
    chunker = Chunker()
    windows = _push_all(chunker, pcm)

    chunks = [chunker.render(window) for window in windows]
    output = b"".join(chunks)
    rendered = np.frombuffer(output, dtype="<i2")

    for output_chunk in chunks:
        assert len(output_chunk) == 192000
        assert np.isfinite(np.frombuffer(output_chunk, dtype="<i2")).all()
    assert len(output) == HOP_SAMPLES * 4
    np.testing.assert_allclose(rendered, pcm[: HOP_SAMPLES * 2], atol=1)


def test_render_aligns_bounded_model_delay_without_shrinking_timeline() -> None:
    time = np.arange(HOP_SAMPLES * 2 + LOOKAHEAD_SAMPLES) / 48_000
    source = np.rint(np.sin(2 * np.pi * 233 * time) * 12000).astype("<i2")
    chunker = Chunker()
    first, second = _push_all(chunker, source)
    first_output = chunker.render(first)
    delay = FRAME_SAMPLES // 4
    delayed_second = np.pad(second, (delay, 0))[:WINDOW_SAMPLES]

    second_output = chunker.render(delayed_second)
    output = np.frombuffer(first_output + second_output, dtype="<i2")

    assert len(second_output) == 192000
    np.testing.assert_allclose(output, source[: HOP_SAMPLES * 2], atol=1)


def test_render_clips_finite_audio_to_pcm16() -> None:
    converted = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
    converted[CONTEXT_SAMPLES : CONTEXT_SAMPLES + HOP_SAMPLES] = np.linspace(
        -2.0, 2.0, HOP_SAMPLES
    )

    rendered = np.frombuffer(Chunker().render(converted), dtype="<i2")

    assert len(rendered) == HOP_SAMPLES
    assert rendered.min() == -32768
    assert rendered.max() == 32767
    assert np.isfinite(rendered).all()


def test_reset_clears_input_context_and_pending_render_overlap() -> None:
    chunker = Chunker()
    old = np.full(HOP_SAMPLES + LOOKAHEAD_SAMPLES, -12000, dtype="<i2")
    first_window = _push_all(chunker, old)[0]
    chunker.render(first_window)

    chunker.reset()
    new = np.full(HOP_SAMPLES + LOOKAHEAD_SAMPLES, 12000, dtype="<i2")
    new_window = _push_all(chunker, new)[0]
    output = np.frombuffer(chunker.render(new_window), dtype="<i2")

    np.testing.assert_array_equal(new_window[:CONTEXT_SAMPLES], 0)
    np.testing.assert_array_equal(output, new[:HOP_SAMPLES])


def test_render_rejects_wrong_shape_and_non_finite_audio() -> None:
    chunker = Chunker()

    with pytest.raises(ValueError, match="124800"):
        chunker.render(np.zeros(WINDOW_SAMPLES - 1, dtype=np.float32))
    invalid = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
    invalid[CONTEXT_SAMPLES] = np.nan
    with pytest.raises(ValueError, match="finite"):
        chunker.render(invalid)


def test_engine_returns_silence_without_running_model_for_silent_window() -> None:
    engine = Engine.__new__(Engine)

    output = engine.convert(np.zeros(WINDOW_SAMPLES, dtype=np.float32))

    assert output.shape == (WINDOW_SAMPLES,)
    assert output.dtype == np.float32
    np.testing.assert_array_equal(output, 0)


def test_upstream_working_directory_is_scoped_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_directory = tmp_path / "service"
    upstream_directory = tmp_path / "upstream"
    service_directory.mkdir()
    upstream_directory.mkdir()
    monkeypatch.chdir(service_directory)

    with engine_module._working_directory(upstream_directory):
        assert Path.cwd() == upstream_directory
    assert Path.cwd() == service_directory

    with pytest.raises(RuntimeError, match="startup failed"):
        with engine_module._working_directory(upstream_directory):
            raise RuntimeError("startup failed")
    assert Path.cwd() == service_directory
