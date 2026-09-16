import asyncio


class Room:
    def __init__(self, *, stasis_handler, destroyed_handler):
        self.stasis_handler = stasis_handler
        self.destroyed_handler = destroyed_handler
        self.entered = False
        self.closed = False
        self.hung_up = []

    async def __aenter__(self):
        self.entered = True
        return self

    async def close(self):
        self.closed = True

    async def create_bridge(self, name):
        return name

    async def answer_channel(self, channel):
        self.answered = channel

    async def add_to_bridge(self, bridge, channel):
        self.last_add = (bridge, channel)

    async def hangup_channel(self, channel):
        self.hung_up.append(channel)


class Routing:
    class Profile:
        pipeline = "passthrough"

    def profile_for(self, extension):
        assert extension == "1983"
        return self.Profile()


def test_sip_controller_connects_ari_events_to_a_lifecycle_managed_session_manager():
    from conference.sip_controller import SipController

    async def check():
        rooms = []

        def room_factory(**handlers):
            room = Room(**handlers)
            rooms.append(room)
            return room

        controller = SipController(room_factory, Routing(), model_factory=object)
        await controller.start()
        assert rooms[0].entered

        await rooms[0].stasis_handler("sip-1983", "1983")
        assert rooms[0].answered == "sip-1983"
        assert rooms[0].last_add == ("phoneguy-main", "sip-1983")
        await rooms[0].destroyed_handler("sip-1983")
        assert controller.sessions.sessions == {}

        # A rejected Stasis channel must not be left ringing forever.  This is
        # especially important when a previous RVC caller disappeared without
        # sending SIP BYE and the single model slot is still occupied.
        await rooms[0].stasis_handler("sip-unknown", "9999")
        assert rooms[0].hung_up == ["sip-unknown"]

        await controller.close()
        assert rooms[0].closed

    asyncio.run(check())
