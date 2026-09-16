"""Echo line service: 1999 -> your live voice as Phone Guy in the earpiece.

Wiring per call:

    caller (PJSIP, Stasis selfmonitor)
      + injection chan_websocket  -> mixing bridge "echo"  (caller hears only
                                     the processed voice; mixing bridges never
                                     feed a channel its own audio back)
    snoop(spy=in) on the caller
      + listener chan_websocket   -> mixing bridge "source" (listener receives
                                     a pure copy of the caller's speech; the
                                     injection audio is never visible here, so
                                     there is no feedback loop)

    listener --20 ms frames--> RvcStream (GPT v2) --1 s blocks--> injection

Fail-closed: on any model or transport failure the call is torn down; the
caller never hears the raw passthrough.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os

from conference.media import Pacer, split_pcm
from conference.rvc import RvcStream

from .ari import SelfMonitorAri


DEFAULT_RVC_URL = "ws://127.0.0.1:8090/ws/rvc-v2"


class EchoSession:
    def __init__(self, service, channel_id):
        self.service = service
        self.channel_id = channel_id
        self.echo_bridge = "selfmonitor-echo-" + channel_id
        self.source_bridge = "selfmonitor-source-" + channel_id
        self.injection = None
        self.listener = None
        self.model = None
        self.tasks = []
        self.closed = False

    async def run(self):
        service = self.service
        ari = service.ari
        try:
            print(f"selfmonitor: session start for {self.channel_id}", flush=True)
            await ari.answer(self.channel_id)
            self.injection = await ari.open_media("echo-" + self.channel_id, receive=False)
            await ari.create_bridge(self.echo_bridge)
            await ari.add_to_bridge(self.echo_bridge, self.channel_id)
            await ari.add_to_bridge(self.echo_bridge, self.injection.channel_id)

            await ari.snoop(self.channel_id, "selfmonitor-snoop-" + self.channel_id)
            self.listener = await ari.open_media("source-" + self.channel_id, receive=True)
            await ari.create_bridge(self.source_bridge)
            await ari.add_to_bridge(self.source_bridge, "selfmonitor-snoop-" + self.channel_id)
            await ari.add_to_bridge(self.source_bridge, self.listener.channel_id)
            print(f"selfmonitor: bridges ready for {self.channel_id}", flush=True)

            self.model = await service.model_factory()
            print(f"selfmonitor: model ready for {self.channel_id}", flush=True)
            self.tasks = [
                asyncio.create_task(self._forward(), name="echo-forward"),
                asyncio.create_task(self._inject(), name="echo-inject"),
            ]
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            import traceback

            traceback.print_exc()
            await self.close()
            raise
        finally:
            await self.close()

    async def _forward(self):
        while True:
            frame = await self.listener.recv_pcm()
            await self.model.send(frame)

    async def _inject(self):
        pacer = Pacer()
        async for block in self.model.outputs():
            for frame in split_pcm(block):
                await pacer.wait()
                await self.injection.send_pcm(frame)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.model is not None:
            with suppress(Exception):
                await self.model.close()
        if self.listener is not None:
            with suppress(Exception):
                await self.listener.close()
        if self.injection is not None:
            with suppress(Exception):
                await self.injection.close()
        ari = self.service.ari
        await ari.delete_bridge(self.source_bridge)
        await ari.delete_bridge(self.echo_bridge)
        with suppress(Exception):
            await ari.request("DELETE", "/channels/selfmonitor-snoop-" + self.channel_id)
        with suppress(Exception):
            await ari.hangup_busy(self.channel_id)


class EchoService:
    """One live echo call at a time: the RVC worker serves one session anyway."""

    def __init__(self, ari, *, model_factory, on_error=None):
        self.ari = ari
        self.model_factory = model_factory
        self.on_error = on_error
        self.session = None
        self.lock = asyncio.Lock()

    async def handle_stasis_start(self, channel_id, endpoint):
        async with self.lock:
            if self.session is not None and not self.session.closed:
                await self.ari.hangup_busy(channel_id)
                return
            self.session = EchoSession(self, channel_id)
            session = self.session
        try:
            await session.run()
        except Exception as error:
            if self.on_error is not None:
                with suppress(Exception):
                    self.on_error(channel_id, error)

    async def handle_destroyed(self, channel_id):
        async with self.lock:
            session = self.session
            if session is not None and session.channel_id == channel_id:
                self.session = None
        if session is not None:
            await session.close()

    async def close(self):
        async with self.lock:
            session = self.session
            self.session = None
        if session is not None:
            await session.close()


async def default_model_factory():
    model = RvcStream(os.environ.get("SELFMONITOR_RVC_URL", DEFAULT_RVC_URL))
    return await model.__aenter__()


async def main():
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

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
        spy_direction=os.environ.get("SELFMONITOR_SPY", "in"),
    )
    service = EchoService(ari, model_factory=default_model_factory)

    health_port = int(os.environ.get("SELFMONITOR_HEALTH_PORT", "8096"))
    health = await asyncio.start_server(_health_handler(ready), "127.0.0.1", health_port)

    async with ari:
        if not ready.done():
            ready.set_result(True)
        print("selfmonitor: connected to ARI, listening for 1999", flush=True)
        async with health:
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
