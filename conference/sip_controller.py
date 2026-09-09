"""Lifecycle glue between ARI Stasis events and the protected SIP mixer."""

from .sip_session import SipSessionManager


class SipController:
    """Run one ARI application and clean up every SIP session it owns."""

    def __init__(self, room_factory, routing, *, model_factory):
        self.room_factory = room_factory
        self.routing = routing
        self.model_factory = model_factory
        self.room = None
        self.sessions = None

    async def start(self):
        if self.room is not None:
            raise ValueError("sip_controller_already_started")

        async def started(channel_id, endpoint):
            await self.sessions.handle_stasis_start(channel_id, endpoint)

        async def destroyed(channel_id):
            await self.sessions.close(channel_id)

        room = self.room_factory(
            stasis_handler=started, destroyed_handler=destroyed
        )
        try:
            self.sessions = SipSessionManager(
                room, self.routing, model_factory=self.model_factory
            )
            self.room = await room.__aenter__()
            return self
        except BaseException:
            self.room = room
            await self.close()
            raise

    async def close(self):
        if self.sessions is not None:
            await self.sessions.close_all()
            self.sessions = None
        if self.room is not None:
            room, self.room = self.room, None
            await room.close()
