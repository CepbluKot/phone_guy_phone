"""Shared mono PCM16, 48 kHz framing, pacing, and cleanup helpers."""

import asyncio
import time

FRAME_BYTES = 1920
BLOCK_BYTES = 192000
FRAME_SECONDS = 0.020


def split_pcm(block: bytes) -> list[bytes]:
    if not isinstance(block, bytes) or not block or len(block) % FRAME_BYTES:
        raise ValueError("invalid_pcm")
    return [block[offset:offset + FRAME_BYTES]
            for offset in range(0, len(block), FRAME_BYTES)]


class Pacer:
    """Keep sends 20 ms apart without catching up in a burst after delays."""

    def __init__(self, clock=time.monotonic, sleep=asyncio.sleep):
        self.clock = clock
        self.sleep = sleep
        self.deadline = None

    async def wait(self):
        now = self.clock()
        if self.deadline is not None and now < self.deadline:
            await self.sleep(self.deadline - now)
        self.deadline = max(self.clock(), self.deadline or now) + FRAME_SECONDS


async def finish_cleanup(coroutine):
    """Complete owned-resource cleanup despite repeated outer cancellation."""
    task = asyncio.create_task(coroutine)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError
