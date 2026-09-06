import importlib.util
from pathlib import Path
import struct

from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response


TOOL_PATH = Path(__file__).with_name("live-conference.py")
SPEC = importlib.util.spec_from_file_location("live_conference", TOOL_PATH)
live_conference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_conference)


def test_wrong_origin_http_403_is_accepted_as_a_rejection():
    rejected = InvalidStatus(Response(403, "Forbidden", Headers()))
    unexpected = InvalidStatus(Response(500, "Server Error", Headers()))

    assert live_conference.is_wrong_origin_rejection(rejected)
    assert not live_conference.is_wrong_origin_rejection(unexpected)


def test_pcm_validator_accepts_expected_silence_and_marks_sounding_frames():
    assert not live_conference.pcm_is_finite(bytes(live_conference.FRAME_BYTES))
    sounding = struct.pack("<960h", *([0] * 959 + [1]))
    assert live_conference.pcm_is_finite(sounding)


def test_busy_probe_is_explicitly_fail_closed_and_does_not_persist_audio():
    source = Path(__file__).with_name("live-conference-busy.py").read_text()

    assert '"error", "code": "busy"' in source
    assert "no media" in source
    assert "write(" not in source
