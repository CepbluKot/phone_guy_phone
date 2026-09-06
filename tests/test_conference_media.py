import itertools

import pytest


def test_rvc_block_is_split_without_changing_audio():
    from conference.media import split_pcm
    block = bytes(range(256)) * 750
    frames = split_pcm(block)
    assert len(frames) == 100
    assert all(len(f) == 1920 for f in frames)
    assert b"".join(frames) == block


@pytest.mark.parametrize("block", [b"", bytes(1921)])
def test_partial_frame_rejected(block):
    from conference.media import split_pcm
    with pytest.raises(ValueError, match="invalid_pcm"):
        split_pcm(block)


@pytest.mark.parametrize("role,active", [
    ("A", {0, 1, 1200, 1201}),
    ("B", {350, 351, 1200, 1201}),
    ("C", {700, 701}),
])
def test_source_timeline_is_exact_and_loops_with_silence(role, active):
    from conference.scenario import source_frames
    phrase = b"\x01\x00" * 1920
    frames = list(itertools.islice(source_frames(role, pcm=phrase), 3000))
    assert all(len(frame) == 1920 for frame in frames)
    assert {i for i, frame in enumerate(frames[:1500]) if any(frame)} == active
    assert frames[:1500] == frames[1500:]


def test_source_clips_long_phrases_and_reserves_trailing_silence():
    from conference.scenario import source_frames
    frames = list(itertools.islice(source_frames("A", pcm=b"\x01\x00" * 480000), 1500))
    assert any(frames[249]) and not any(frames[250])
    assert any(frames[1449]) and not any(frames[1450])


def test_source_requires_external_fixture_and_valid_pcm(tmp_path, monkeypatch):
    from conference.scenario import source_frames
    monkeypatch.setenv("CONFERENCE_FIXTURE_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        next(source_frames("A"))
    with pytest.raises(ValueError):
        next(source_frames("D", pcm=bytes(1920)))
    with pytest.raises(ValueError):
        next(source_frames("A", pcm=b"x"))


def test_fixture_generator_uses_only_local_speech_and_pcm48_contract(tmp_path):
    from conference.scenario import generate_fixtures
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return type("Result", (), {"stdout": b"fake-wave"})()

    generate_fixtures(tmp_path, run=run)
    assert len(calls) == 6
    labels = []
    for i, role in enumerate("ABC"):
        synth, convert = calls[i * 2:i * 2 + 2]
        assert synth[0][0] == "espeak-ng"
        labels.append(synth[0][-1])
        assert f"Speaker {role}" in labels[-1]
        assert convert[1]["input"] == b"fake-wave"
        assert convert[0][-9:] == ["-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", "-f", "s16le", str(tmp_path / f"{role}.pcm")]
        assert synth[1]["check"] and convert[1]["check"]
    assert len(set(labels)) == 3


def test_fixture_generator_refuses_repo_destination():
    from conference.scenario import generate_fixtures
    with pytest.raises(ValueError, match="outside"):
        generate_fixtures("conference/fixtures")
