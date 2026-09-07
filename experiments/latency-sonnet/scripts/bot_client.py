"""A "bot" participant for the multi-connection rt_server.py demo: speaks
the same WebSocket protocol a real browser client would, streaming a WAV
file as real-time-paced 20ms PCM16 frames, and saves what comes back.
Run several of these concurrently (plus, optionally, a real human on the
web-rt/ page) to demonstrate rt_server.py serving multiple simultaneous
sessions off one shared GPU-resident engine, each with correctly isolated
pitch state (see rvc_service/rt_engine.py's reset_pitch_cache and
rt_restart_check.py for why that isolation was needed).
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

SAMPLE_RATE = 48000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2


def load48k(path: Path, seconds: float) -> np.ndarray:
    from scipy.signal import resample_poly

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        g = math.gcd(SAMPLE_RATE, sr)
        data = resample_poly(data, SAMPLE_RATE // g, sr // g).astype(np.float32)
    n = int(seconds * SAMPLE_RATE)
    if data.size < n:
        data = np.tile(data, n // data.size + 1)
    return data[:n].astype(np.float32)


async def run_bot(name: str, url: str, wav_path: Path, seconds: float, out_path: Path, start_delay: float) -> dict:
    await asyncio.sleep(start_delay)
    audio = load48k(wav_path, seconds)
    pcm16 = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2")
    out_chunks = []
    processing_ms = []
    t_start = time.perf_counter()

    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "version": 1, "sampleRate": SAMPLE_RATE,
                                   "channels": 1, "sampleFormat": "s16le"}))
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            raise RuntimeError(f"{name}: unexpected first message {ready}")
        print(f"[{name}] ready, sessionId={ready['sessionId']} block={ready['blockSeconds']}s", flush=True)

        async def sender():
            n_frames = pcm16.size // FRAME_SAMPLES
            for i in range(n_frames):
                frame = pcm16[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()
                await ws.send(frame)
                await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)  # real-time pacing, like a real mic
            await ws.send(json.dumps({"type": "stop"}))

        send_task = asyncio.create_task(sender())
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "metrics":
                        processing_ms.append(data["processingMs"])
                    elif data.get("type") in ("stopped", "error"):
                        break
                else:
                    out_chunks.append(message)
        finally:
            send_task.cancel()

    wall = time.perf_counter() - t_start
    out_audio = np.frombuffer(b"".join(out_chunks), dtype="<i2").astype(np.float32) / 32768.0
    sf.write(str(out_path), out_audio, SAMPLE_RATE)
    processing_ms.sort()
    n = len(processing_ms)
    result = {
        "name": name, "wall_s": round(wall, 2),
        "input_s": round(pcm16.size / SAMPLE_RATE, 2),
        "output_s": round(out_audio.size / SAMPLE_RATE, 2),
        "blocks": n,
        "processing_p50_ms": processing_ms[n // 2] if n else None,
        "processing_p95_ms": processing_ms[int(0.95 * (n - 1))] if n else None,
        "processing_max_ms": processing_ms[-1] if n else None,
    }
    print(f"[{name}] done: {json.dumps(result)}", flush=True)
    return result


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8092/ws/rvc"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp")

    bots = [
        ("bot-ru", Path("/tmp/ru.wav"), 0.0),
        ("bot-en", Path("/tmp/en.wav"), 1.0),
        ("bot-ru2", Path("/tmp/ru.wav"), 2.0),
    ]
    results = await asyncio.gather(*[
        run_bot(name, url, wav, seconds, out_dir / f"{name}_out.wav", delay)
        for name, wav, delay in bots
    ])
    print("---SUMMARY-JSON---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
