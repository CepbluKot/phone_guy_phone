"""Unit tests for the experimental rtrvc.py-based framer/stitcher
(rvc_service/rt_chunks.py). CPU-only, no GPU/model required -- mirrors the
style of tests/test_rvc_chunks.py."""

import numpy as np
import pytest

from rvc_service.rt_chunks import FRAME_BYTES, FRAME_SAMPLES, RtFramer, RtStitcher, resample_to


def _frames(samples: np.ndarray):
    for offset in range(0, len(samples), FRAME_SAMPLES):
        chunk = samples[offset : offset + FRAME_SAMPLES]
        if chunk.size < FRAME_SAMPLES:
            return
        yield chunk.astype("<i2").tobytes()


class TestRtFramer:
    def test_push_returns_none_until_block_boundary(self) -> None:
        # 0.24s is an exact multiple of the 20ms (960-sample) capture frame;
        # 0.25s is not (12.5 frames), which would silently drop the odd 480
        # trailing samples in the _frames() test helper below.
        framer = RtFramer(block_s=0.24, extra_s=0.5, crossfade_s=0.05, search_s=0.02)
        source = np.zeros(framer.block_48k, dtype="<i2")
        frames = list(_frames(source))
        assert len(frames) * FRAME_SAMPLES == framer.block_48k
        results = [framer.push(f) for f in frames[:-1]]
        assert all(r is None for r in results)
        assert framer.push(frames[-1]) is not None

    def test_returned_buffer_has_expected_window_length(self) -> None:
        framer = RtFramer(block_s=0.24, extra_s=0.5, crossfade_s=0.05, search_s=0.02)
        source = np.zeros(framer.block_48k, dtype="<i2")
        window = None
        for f in _frames(source):
            window = framer.push(f)
        assert window is not None
        assert window.shape == (framer.window_16k,)

    def test_dc_signal_resamples_to_roughly_the_same_dc_level(self) -> None:
        framer = RtFramer(block_s=0.25, extra_s=0.1, crossfade_s=0.02, search_s=0.01)
        level = 8000
        source = np.full(framer.block_48k * 4, level, dtype="<i2")
        window = None
        for f in _frames(source):
            window = framer.push(f)
        assert window is not None
        # after a few blocks of constant input the resampled tail should
        # settle near the same normalized DC level (some edge ripple is fine)
        settled = window[-framer.block_16k // 2 :]
        np.testing.assert_allclose(settled.mean(), level / 32768.0, atol=0.01)

    @pytest.mark.parametrize(
        "bad_frame", [b"", b"\0" * (FRAME_BYTES - 2), b"\0" * (FRAME_BYTES + 2)]
    )
    def test_push_rejects_malformed_frames(self, bad_frame: bytes) -> None:
        framer = RtFramer(block_s=0.25, extra_s=0.5, crossfade_s=0.05, search_s=0.02)
        with pytest.raises(ValueError):
            framer.push(bad_frame)

    def test_reset_clears_state(self) -> None:
        framer = RtFramer(block_s=0.25, extra_s=0.5, crossfade_s=0.05, search_s=0.02)
        for f in _frames(np.full(framer.block_48k, 5000, dtype="<i2")):
            framer.push(f)
        framer.reset()
        assert framer._pending.size == 0
        assert np.all(framer._buf_48k == 0)
        assert np.all(framer._buf_16k == 0)


class TestRtStitcher:
    def _model_output(self, stitcher: RtStitcher, total_samples: int, phase0: float, freq: float) -> np.ndarray:
        # out_np[search_n] is where the "nominal" new block begins (see
        # RtStitcher._aligned_start's `nominal = search_n`), so index 0 of
        # a call's output corresponds to absolute sample (phase0 - search_n).
        t = (np.arange(stitcher.expected_n, dtype=np.float64) + phase0 - stitcher.search_n) / stitcher.tgt_sr
        return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    def test_each_call_emits_exactly_block_n_samples(self) -> None:
        stitcher = RtStitcher(tgt_sr=32000, block_s=0.25, crossfade_s=0.05, search_s=0.02)
        total_out = 0
        for i in range(10):
            out_np = self._model_output(stitcher, stitcher.expected_n, i * stitcher.block_n, 220.0)
            pcm = stitcher.render(out_np)
            assert len(pcm) == stitcher.block_n * 2  # int16 bytes
            total_out += len(pcm) // 2
        assert total_out == 10 * stitcher.block_n

    def test_zero_input_produces_silence_without_crashing(self) -> None:
        stitcher = RtStitcher(tgt_sr=32000, block_s=0.2, crossfade_s=0.04, search_s=0.01)
        zeros = np.zeros(stitcher.expected_n, dtype=np.float32)
        for _ in range(3):
            pcm = stitcher.render(zeros)
            assert np.frombuffer(pcm, dtype="<i2").sum() == 0

    def test_short_output_is_padded_not_rejected(self) -> None:
        stitcher = RtStitcher(tgt_sr=32000, block_s=0.2, crossfade_s=0.04, search_s=0.01)
        short = np.zeros(stitcher.expected_n // 2, dtype=np.float32)
        pcm = stitcher.render(short)
        assert len(pcm) == stitcher.block_n * 2

    def test_rejects_non_finite_input(self) -> None:
        stitcher = RtStitcher(tgt_sr=32000, block_s=0.2, crossfade_s=0.04, search_s=0.01)
        bad = np.full(stitcher.expected_n, np.nan, dtype=np.float32)
        with pytest.raises(ValueError):
            stitcher.render(bad)

    def test_continuous_sine_reconstructs_with_low_error(self) -> None:
        """If successive model calls already agree perfectly on the overlap
        region (a stand-in for a well-cached, phase-continuous model), the
        stitched output should reconstruct the source sine with small error
        -- regression check that blending doesn't introduce audible steps."""
        stitcher = RtStitcher(tgt_sr=32000, block_s=0.25, crossfade_s=0.05, search_s=0.02)
        freq = 220.0
        n_calls = 12
        stitched = bytearray()
        for i in range(n_calls):
            out_np = self._model_output(stitcher, stitcher.expected_n, i * stitcher.block_n, freq)
            stitched += stitcher.render(out_np)
        got = np.frombuffer(bytes(stitched), dtype="<i2").astype(np.float64) / 32768.0
        t = np.arange(got.size, dtype=np.float64) / stitcher.tgt_sr
        expected = 0.2 * np.sin(2 * np.pi * freq * t)
        assert got.size == n_calls * stitcher.block_n
        error = np.sqrt(np.mean((got - expected) ** 2))
        assert error < 0.01, f"stitched RMS error too high: {error}"


def test_resample_to_identity_when_rates_match() -> None:
    audio = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    np.testing.assert_array_equal(resample_to(audio, 32000, 32000), audio)


def test_resample_to_changes_length_by_rate_ratio() -> None:
    audio = np.zeros(3200, dtype=np.float32)
    out = resample_to(audio, 32000, 48000)
    assert out.size == 4800
