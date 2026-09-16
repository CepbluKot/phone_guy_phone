#!/usr/bin/env python3
"""Full offline live check of the 1999 echo line, no physical phone needed.

Runs on VM209 while voice-selfmonitor.service is STOPPED (one ARI app
instance at a time). Creates a Local channel whose ;2 half enters the real
dialplan extension 1999 -> Stasis(selfmonitor), so the production wiring
runs exactly as for a phone call. The ;1 half is bridged with a test
injection channel (synthetic speech) and a test listener channel that must
capture the converted Phone Guy audio back. Reports every stage and audio
statistics, and empirically validates the snoop direction.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import uuid

import numpy as np

from conference.media import FRAME_BYTES
from conference.rvc import RvcStream

from .ari import SelfMonitorAri
from .service import EchoService, EchoSession


def synthetic_speech(frames: int, seed: int = 7) -> list[bytes]:
    """Amplitude-modulated tone stack, 20 ms PCM16 frames, mostly speech."""
    rng = np.random.default_rng(seed)
    chunks = []
    for index in range(frames):
        t = (np.arange(FRAME_BYTES // 2) + index * (FRAME_BYTES // 2)) / 48000.0
        voiced = (index // 25) % 4 != 3  # 25 frames speech, 8 frames pause
        if voiced:
            env = 0.4 + 0.3 * np.sin(2 * np.pi * 2.3 * t)
            wave = (np.sin(2 * np.pi * 190 * t)
                    + 0.5 * np.sin(2 * np.pi * 570 * t + rng.random())
                    + 0.25 * np.sin(2 * np.pi * 950 * t))
            pcm = np.rint(wave * env * 0.55 * 32767)
        else:
            pcm = np.zeros(FRAME_BYTES // 2)
        chunks.append(pcm.clip(-32768, 32767).astype("<i2").tobytes())
    return chunks


async def main() -> int:
    ari = SelfMonitorAri(
        os.environ.get("SELFMONITOR_ARI_URL", "http://127.0.0.1:8092/ari"),
        os.environ.get("SELFMONITOR_ARI_USERNAME", "phoneguy"),
        os.environ.get("SELFMONITOR_ARI_PASSWORD", ""),
        stasis_handler=None,
        destroyed_handler=None,
        spy_direction=os.environ.get("SELFMONITOR_SPY", "in"),
    )

    service = EchoService(ari, model_factory=default_model)

    async def on_stasis(channel_id, endpoint):
        await service.handle_stasis_start(channel_id, endpoint)

    async def on_destroyed(channel_id):
        await service.handle_destroyed(channel_id)

    ari.stasis_handler = on_stasis
    ari.destroyed_handler = on_destroyed

    speech = synthetic_speech(300)  # 6 s
    captured: list[bytes] = []

    async with ari:
        print("check: ARI connected", flush=True)
        # ;1 half: enters our app directly, bridged with test media channels.
        test_id = f"selfmonitor-check-{uuid.uuid4().hex}"
        await ari.request("POST", "/channels",
                          params={"endpoint": "Local/1999@phoneguy-sip",
                                  "app": "selfmonitor", "channelId": test_id})
        test_bridge = f"selfmonitor-check-bridge-{uuid.uuid4().hex[:8]}"
        await ari.create_bridge(test_bridge)
        test_speaker = await ari.open_media("check-speaker", receive=False)
        test_listener = await ari.open_media("check-listener", receive=True)
        await ari.add_to_bridge(test_bridge, test_id)
        await ari.add_to_bridge(test_bridge, test_speaker.channel_id)
        await ari.add_to_bridge(test_bridge, test_listener.channel_id)
        # POST /channels with endpoint+app originates immediately; the Local
        # ;2 half runs the real dialplan extension 1999 -> Stasis(selfmonitor).
        print("check: local channel dialing 1999", flush=True)

        async def speak():
            for frame in speech:
                await test_speaker.send_pcm(frame)
                await asyncio.sleep(0.02)

        async def capture():
            deadline = asyncio.get_running_loop().time() + 25
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    frame = await asyncio.wait_for(test_listener.recv_pcm(),
                                                   min(remaining, 3))
                except asyncio.TimeoutError:
                    if service.session is None and captured:
                        break
                    continue
                captured.append(frame)
                if len(captured) >= len(speech) - 10:
                    break

        speaker_task = asyncio.create_task(speak())
        capture_task = asyncio.create_task(capture())
        await asyncio.gather(speaker_task, capture_task)

        for task in (*ari.handler_tasks,):
            task.cancel()
        await service.close()
        await test_listener.close()
        await test_speaker.close()
        await ari.delete_bridge(test_bridge)
        with suppress(Exception):
            await ari.request("DELETE", "/channels/" + test_id)

    audio = np.frombuffer(b"".join(captured), dtype="<i2")
    speech_array = np.frombuffer(b"".join(speech), dtype="<i2")
    print(f"check: captured {captured and len(captured)} frames "
          f"({audio.size / 48000:.1f} s)", flush=True)
    if not audio.size:
        print("check: FAIL - no audio captured on the caller side", flush=True)
        return 1
    rms = float(np.sqrt(np.mean((audio.astype(np.float64) / 32768) ** 2)))
    non_silent = int(np.count_nonzero(audio)) 
    print(f"check: captured rms={rms:.4f} non-zero samples={non_silent}/{audio.size}",
          flush=True)
    if rms < 0.005:
        print("check: FAIL - captured audio is silent (bridges ok, snoop "
              "or injection broken)", flush=True)
        return 2
    # The echo must differ from the raw input: RVC shifts the spectrum.
    same = audio.size == speech_array.size and bool(
        np.array_equal(audio, speech_array)
    )
    if same:
        print("check: FAIL - captured audio equals the raw input "
              "(spy captured the wrong direction: injection loop)", flush=True)
        return 3
    print("check: PASS - distinct converted audio returned to the caller "
          f"(spy={os.environ.get('SELFMONITOR_SPY', 'in')})", flush=True)
    return 0


async def default_model():
    model = RvcStream(os.environ.get("SELFMONITOR_RVC_URL",
                                     "ws://127.0.0.1:8090/ws/rvc-v2"))
    return await model.__aenter__()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
