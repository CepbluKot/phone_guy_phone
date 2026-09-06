"""Deterministic synthetic sources and a server-only fixture generator.

Run ``python -m conference.scenario DIRECTORY`` on the conference server with
``espeak-ng`` and ``ffmpeg`` installed.  The output is raw mono PCM16 at 48 kHz
and must live outside this checkout.
"""

import itertools
import os
from pathlib import Path
import subprocess

from .media import FRAME_BYTES


PHRASES = {
    "A": "Speaker A. This is Alice speaking in the conference.",
    "B": "Speaker B. This is Bob joining the conference.",
    "C": "Speaker C. My voice passes through the voice converter.",
}
START_FRAMES = {"A": (0, 1200), "B": (350, 1200), "C": (700,)}
LOOP_FRAMES = 1500
SPEECH_FRAMES = 250


def source_frames(role, *, fixture_dir=None, pcm=None):
    """Yield a fixed 30-second loop of raw 20 ms frames for one role."""
    if role not in PHRASES:
        raise ValueError("invalid_source_role")
    if pcm is None:
        directory = fixture_dir or os.environ.get("CONFERENCE_FIXTURE_DIR")
        if not directory:
            raise ValueError("CONFERENCE_FIXTURE_DIR is required")
        path = Path(directory) / f"{role}.pcm"
        if path.stat().st_size % 2:
            raise ValueError("invalid_pcm")
        with path.open("rb") as fixture:
            pcm = fixture.read(SPEECH_FRAMES * FRAME_BYTES)
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
        raise ValueError("invalid_pcm")

    phrase = pcm[:SPEECH_FRAMES * FRAME_BYTES]
    phrase += bytes((-len(phrase)) % FRAME_BYTES)
    silence = bytes(FRAME_BYTES)
    timeline = [silence] * LOOP_FRAMES
    for start in START_FRAMES[role]:
        for offset in range(0, len(phrase), FRAME_BYTES):
            timeline[start + offset // FRAME_BYTES] = phrase[offset:offset + FRAME_BYTES]
    yield from itertools.cycle(timeline)


def generate_fixtures(directory, *, run=subprocess.run):
    """Generate the three labelled fixtures using local server executables."""
    directory = Path(directory).resolve()
    checkout = Path(__file__).resolve().parents[1]
    if directory == checkout or checkout in directory.parents:
        raise ValueError("fixtures must remain outside the checkout")
    directory.mkdir(parents=True, exist_ok=True)

    for role, phrase in PHRASES.items():
        speech = run(
            ["espeak-ng", "--stdout", "-v", "en-us", "-s", "150", phrase],
            check=True,
            stdout=subprocess.PIPE,
            timeout=30,
        )
        run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-n", "-i", "pipe:0",
                "-t", "5", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                "-f", "s16le", str(directory / f"{role}.pcm"),
            ],
            input=speech.stdout,
            check=True,
            timeout=30,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    generate_fixtures(parser.parse_args().directory)
