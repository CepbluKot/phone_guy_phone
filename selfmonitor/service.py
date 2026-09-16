"""SIP 1999 hears the browser's rendered `/live/` audio, never SIP input."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import os

from websockets.asyncio.server import serve

from conference.media import FRAME_BYTES, Pacer
from .ari import SelfMonitorAri


ORIGIN = "https://vm-voice-1.lan.awesomeio.ru"
SILENCE_FRAME = bytes(FRAME_BYTES)


class MirrorRelay:
    """One publisher and a small FIFO jitter buffer for rendered speech."""

    def __init__(self, *, prebuffer_frames=8, max_frames=50):
        if not 1 <= prebuffer_frames <= max_frames:
            raise ValueError("invalid_mirror_buffer")
        self.publisher = None
        self.prebuffer_frames = prebuffer_frames
        self.frames = deque(maxlen=max_frames)
        self.playing = False

    def claim(self, publisher):
        if self.publisher is not None:
            return False
        self.publisher = publisher
        self.frames.clear()
        self.playing = False
        return True

    def publish(self, publisher, frame):
        if self.publisher is not publisher or not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
            return False
        self.frames.append(frame)
        return True

    def release(self, publisher):
        if self.publisher is publisher:
            self.publisher = None
            self.frames.clear()
            self.playing = False

    def take_frame(self):
        if not self.playing:
            if len(self.frames) < self.prebuffer_frames:
                return SILENCE_FRAME
            self.playing = True
        if self.frames:
            return self.frames.popleft()
        self.playing = False
        return SILENCE_FRAME


async def handle_publisher(socket, relay):
    if socket.request.path != "/ws/live-mirror":
        await socket.close(code=1008)
        return
    owner = object()
    if not relay.claim(owner):
        await socket.close(code=1013)
        return
    try:
        async for frame in socket:
            if not relay.publish(owner, frame):
                await socket.close(code=1003)
                return
    finally:
        relay.release(owner)


class MirrorSession:
    """Answer a caller and pace browser-rendered PCM into one injection channel."""

    def __init__(self, service, channel_id):
        self.service = service
        self.channel_id = channel_id
        self.bridge_id = "selfmonitor-mirror-" + channel_id
        self.injection = None
        self.task = None
        self.closed = False

    async def run(self):
        if self.closed:
            return
        self.task = asyncio.current_task()
        ari = self.service.ari
        try:
            await ari.answer(self.channel_id)
            self.injection = await ari.open_media("mirror-" + self.channel_id, receive=False)
            await ari.create_bridge(self.bridge_id)
            await ari.add_to_bridge(self.bridge_id, self.channel_id)
            await ari.add_to_bridge(self.bridge_id, self.injection.channel_id)
            pacer = Pacer()
            while True:
                await pacer.wait()
                await self.injection.send_pcm(self.service.relay.take_frame())
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def close(self):
        if self.task is not None and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        else:
            await self._cleanup()

    async def _cleanup(self):
        if self.closed:
            return
        self.closed = True
        if self.injection is not None:
            with suppress(Exception):
                await self.injection.close()
        await self.service.ari.delete_bridge(self.bridge_id)
        with suppress(Exception):
            await self.service.ari.hangup(self.channel_id)


class MirrorService:
    """One SIP listener at a time; it does not own or start the RVC model."""

    def __init__(self, ari, relay, *, on_error=None):
        self.ari = ari
        self.relay = relay
        self.on_error = on_error
        self.session = None
        self.lock = asyncio.Lock()

    async def handle_stasis_start(self, channel_id, endpoint):
        async with self.lock:
            if self.session is not None and not self.session.closed:
                await self.ari.hangup_busy(channel_id)
                return
            session = MirrorSession(self, channel_id)
            self.session = session
        try:
            await session.run()
        except Exception as error:
            if self.on_error is not None:
                with suppress(Exception):
                    self.on_error(channel_id, error)
        finally:
            async with self.lock:
                if self.session is session:
                    self.session = None

    async def handle_destroyed(self, channel_id):
        async with self.lock:
            session = self.session
            if session is not None and session.channel_id == channel_id:
                self.session = None
            else:
                session = None
        if session is not None:
            await session.close()

    async def close(self):
        async with self.lock:
            session = self.session
            self.session = None
        if session is not None:
            await session.close()


async def main():
    ready = asyncio.get_running_loop().create_future()
    relay = MirrorRelay()

    async def on_stasis(channel_id, endpoint):
        await service.handle_stasis_start(channel_id, endpoint)

    async def on_destroyed(channel_id):
        await service.handle_destroyed(channel_id)

    ari = SelfMonitorAri(
        os.environ.get("SELFMONITOR_ARI_URL", "http://127.0.0.1:8092/ari"),
        os.environ.get("SELFMONITOR_ARI_USERNAME", "phoneguy"),
        os.environ.get("SELFMONITOR_ARI_PASSWORD", ""),
        stasis_handler=on_stasis,
        destroyed_handler=on_destroyed,
    )
    def on_error(channel_id, error):
        print(f"selfmonitor: session {channel_id} failed: {error!r}", flush=True)

    service = MirrorService(ari, relay, on_error=on_error)

    health_port = int(os.environ.get("SELFMONITOR_HEALTH_PORT", "8096"))
    mirror_port = int(os.environ.get("SELFMONITOR_MIRROR_PORT", "8097"))
    health = await asyncio.start_server(_health_handler(ready), "127.0.0.1", health_port)
    async with ari:
        async with health, serve(
            lambda socket: handle_publisher(socket, relay), "127.0.0.1", mirror_port,
            origins=[ORIGIN], max_size=FRAME_BYTES, max_queue=4, compression=None,
        ):
            ready.set_result(True)
            print("selfmonitor: listening for browser mirror and SIP 1999", flush=True)
            await health.serve_forever()


def _health_handler(ready):
    async def handle(reader, writer):
        try:
            await reader.read(4096)
            body = (
                '{"status":"ready"}' if ready.done() and not ready.exception()
                else '{"status":"starting"}'
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Cache-Control: no-store\r\nConnection: close\r\n\r\n" + body.encode()
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            with suppress(Exception):
                writer.close()

    return handle


if __name__ == "__main__":
    asyncio.run(main())
