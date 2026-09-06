"""Single native client of the unchanged version-1 RVC protocol."""

import asyncio
from contextlib import suppress
import json
import math
import time

from websockets.asyncio.client import connect as websocket_connect

from .media import BLOCK_BYTES, FRAME_BYTES, Pacer, finish_cleanup


ORIGIN = "https://voice.lan.awesomeio.ru"
ALLOWED_ORIGINS = {ORIGIN, "https://vm-voice-1.lan.awesomeio.ru"}
START = {
    "type": "start",
    "version": 1,
    "sampleRate": 48000,
    "channels": 1,
    "sampleFormat": "s16le",
}
READY = {
    "type": "ready",
    "version": 1,
    "sampleRate": 48000,
    "channels": 1,
    "sampleFormat": "s16le",
    "frameBytes": FRAME_BYTES,
    "outputSamples": 96000,
}
KNOWN_ERRORS = {
    "busy",
    "model_unavailable",
    "invalid_start",
    "invalid_frame",
    "overloaded",
    "stalled",
}


def _control(message):
    if not isinstance(message, str):
        raise ValueError("invalid_rvc_control")
    try:
        value = json.loads(message)
    except (TypeError, ValueError):
        raise ValueError("invalid_rvc_control") from None
    if not isinstance(value, dict):
        raise ValueError("invalid_rvc_control")
    if value.get("type") == "error":
        code = value.get("code")
        raise ValueError(code if code in KNOWN_ERRORS else "rvc_error")
    return value


def _matches(message, expected):
    return all(type(message.get(key)) is type(value) and message[key] == value
               for key, value in expected.items())


class RvcStream:
    def __init__(
        self,
        url,
        *,
        connect=websocket_connect,
        origin=ORIGIN,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        timeout=10,
    ):
        if origin not in ALLOWED_ORIGINS:
            raise ValueError("invalid_rvc_origin")
        self.url = url
        self.connect = connect
        self.origin = origin
        self.timeout = timeout
        self.pacer = Pacer(clock, sleep)
        self.socket = None
        self.ready = False
        self.closed = False
        self.close_error = None
        self.sending = False
        self.reading = False
        self.output_start = 0
        self.metrics = None

    async def __aenter__(self):
        if self.socket is not None or self.closed:
            raise ValueError("rvc_session_already_used")
        try:
            self.socket = await self.connect(
                self.url,
                origin=self.origin,
                max_size=BLOCK_BYTES,
                max_queue=2,
                open_timeout=self.timeout,
                close_timeout=2,
            )
            await asyncio.wait_for(
                self.socket.send(json.dumps(START)), self.timeout
            )
            async with asyncio.timeout(100):
                opening = _control(await self.socket.recv())
                if opening.get("type") == "warming":
                    if opening.get("timeoutSeconds") != 90:
                        raise ValueError("invalid_rvc_warming")
                    opening = _control(await self.socket.recv())
            if not _matches(opening, READY):
                raise ValueError("invalid_rvc_ready")
            self.ready = True
            return self
        except BaseException:
            await finish_cleanup(self.close())
            raise

    async def __aexit__(self, *exc):
        await finish_cleanup(self.close())

    async def close(self):
        if self.closed:
            return
        if self.close_error is not None:
            raise ConnectionError("rvc_close_failed") from self.close_error
        self.ready = False
        if self.socket is None:
            self.closed = True
            return
        with suppress(Exception):
            await asyncio.wait_for(
                self.socket.send(json.dumps({"type": "stop"})), 2
            )
        try:
            await self.socket.close()
        except (Exception, asyncio.CancelledError) as error:
            self.close_error = error
            raise ConnectionError("rvc_close_failed") from error
        self.closed = True

    async def send(self, frame):
        if not self.ready or not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
            raise ValueError("invalid_rvc_frame_or_state")
        if self.sending:
            raise ValueError("rvc_send_backlog")
        self.sending = True
        try:
            await self.pacer.wait()
            if not self.ready:
                raise ValueError("rvc_closed")
            await asyncio.wait_for(self.socket.send(frame), self.timeout)
        except BaseException:
            await finish_cleanup(self.close())
            raise
        finally:
            self.sending = False

    async def outputs(self):
        """Yield only validated converted blocks with contiguous sample indexes."""
        if self.reading or not self.ready:
            raise ValueError("invalid_rvc_output_state")
        self.reading = True
        try:
            while self.ready:
                async with asyncio.timeout(self.timeout):
                    metadata = _control(await self.socket.recv())
                    expected = {
                        "outputStart": self.output_start,
                        "outputSamples": 96000,
                        "consumedSamples": self.output_start + 96000,
                    }
                    if metadata.get("type") != "metrics" or not _matches(
                        metadata, expected
                    ):
                        raise ValueError("invalid_rvc_metrics")
                    processing_ms = metadata.get("processingMs")
                    if (
                        type(processing_ms) not in (int, float)
                        or not math.isfinite(processing_ms)
                        or processing_ms < 0
                    ):
                        raise ValueError("invalid_rvc_metrics")
                    block = await self.socket.recv()
                    if not isinstance(block, bytes) or len(block) != BLOCK_BYTES:
                        raise ValueError("invalid_rvc_block")
                if not self.ready:
                    return
                self.metrics = metadata
                self.output_start += 96000
                yield block
        except (Exception, asyncio.CancelledError):
            await finish_cleanup(self.close())
            raise
        finally:
            self.reading = False
