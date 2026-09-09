"""ARI ownership for SIP calls, with a strict no-raw-audio route for Phone Guy."""

import asyncio
from dataclasses import dataclass, field

from .media import BLOCK_BYTES, split_pcm


class SipSessionError(ValueError):
    pass


@dataclass
class _Session:
    channel_id: str
    pipeline: str
    source_bridge: str | None = None
    source: object | None = None
    injection: object | None = None
    model: object | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)


class SipSessionManager:
    """Own the bridges and generated media channels for live SIP participants.

    The caller's PJSIP channel is never added to ``phoneguy-main`` for the
    phone-guy profile.  It can enter only the private source bridge; the
    generated injection channel is the sole route back into the main mix.
    """

    main_bridge = "phoneguy-main"

    def __init__(self, ari, routing, *, model_factory):
        self.ari = ari
        self.routing = routing
        self.model_factory = model_factory
        self.sessions = {}
        self._main_ready = False
        self._lock = asyncio.Lock()

    async def _ensure_main_bridge(self):
        if not self._main_ready:
            await self.ari.create_bridge(self.main_bridge)
            self._main_ready = True

    async def handle_stasis_start(self, channel_id, extension):
        if not isinstance(channel_id, str) or not channel_id:
            raise SipSessionError("invalid_channel")
        async with self._lock:
            if channel_id in self.sessions:
                return
            try:
                profile = self.routing.profile_for(extension)
            except Exception as error:
                raise SipSessionError("unknown_extension") from error
            if profile.pipeline == "rvc" and any(
                session.pipeline == "rvc" for session in self.sessions.values()
            ):
                raise SipSessionError("busy")
            await self._ensure_main_bridge()
            session = _Session(channel_id, profile.pipeline)
            self.sessions[channel_id] = session
        try:
            if profile.pipeline == "passthrough":
                await self.ari.add_to_bridge(self.main_bridge, channel_id)
                return
            if profile.pipeline != "rvc":
                raise SipSessionError("unsupported_profile")
            await self._start_phone_guy(session)
        except BaseException:
            await self.close(channel_id)
            raise

    async def _start_phone_guy(self, session):
        channel_id = session.channel_id
        session.source_bridge = "phoneguy-source-" + channel_id
        await self.ari.create_bridge(session.source_bridge)
        session.source = await self.ari.open_media("source-" + channel_id, receive=True)
        session.injection = await self.ari.open_media("injection-" + channel_id)
        # Raw inbound RTP can travel only to the private source bridge.
        await self.ari.add_to_bridge(session.source_bridge, channel_id)
        await self.ari.add_to_bridge(session.source_bridge, session.source.channel_id)
        # The main bridge receives only the controller-owned injection channel.
        await self.ari.add_to_bridge(self.main_bridge, session.injection.channel_id)
        session.model = self.model_factory()
        await session.model.__aenter__()
        session.tasks = [
            asyncio.create_task(self._forward(session), name="sip-rvc-input-" + channel_id),
            asyncio.create_task(self._inject(session), name="sip-rvc-output-" + channel_id),
        ]

    async def _forward(self, session):
        while True:
            await session.model.send(await session.source.receive_pcm())

    async def _inject(self, session):
        async for block in session.model.outputs():
            if not isinstance(block, bytes) or len(block) != BLOCK_BYTES:
                raise SipSessionError("invalid_converted_block")
            for frame in split_pcm(block):
                await session.injection.send_pcm(frame)

    async def close(self, channel_id):
        async with self._lock:
            session = self.sessions.pop(channel_id, None)
        if session is None:
            return
        for task in session.tasks:
            task.cancel()
        await asyncio.gather(*session.tasks, return_exceptions=True)
        if session.model is not None:
            await session.model.__aexit__(None, None, None)
        for media in (session.source, session.injection):
            if media is not None:
                await media.close()
        if session.source_bridge is not None:
            await self.ari.delete_bridge(session.source_bridge)

    async def close_all(self):
        for channel_id in list(self.sessions):
            await self.close(channel_id)
