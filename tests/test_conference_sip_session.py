import asyncio
from dataclasses import dataclass

import pytest


FRAME = b"\x01" * 1920
BLOCK = FRAME * 50


@dataclass(frozen=True)
class Profile:
    pipeline: str


class Routing:
    def profile_for(self, extension):
        return {"1983": Profile("passthrough"), "1987": Profile("rvc")}[extension]


class Media:
    def __init__(self, role):
        self.role = role
        self.channel_id = "media-" + role
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def receive_pcm(self):
        return await self.incoming.get()

    async def send_pcm(self, frame):
        self.sent.append(frame)

    async def close(self):
        self.closed = True


class Ari:
    def __init__(self):
        self.actions = []
        self.media = []

    async def create_bridge(self, name):
        self.actions.append(("create_bridge", name))
        return name

    async def answer_channel(self, channel):
        self.actions.append(("answer", channel))

    async def add_to_bridge(self, bridge, channel):
        self.actions.append(("add", bridge, channel))

    async def delete_bridge(self, bridge):
        self.actions.append(("delete_bridge", bridge))

    async def open_media(self, role, *, receive=False):
        media = Media(role)
        self.media.append(media)
        self.actions.append(("open_media", role, receive))
        return media

    async def originate(self, endpoint, *, app, caller_id, timeout=30):
        self.actions.append(("originate", endpoint, app, caller_id, timeout))
        return "sip-outbound-" + endpoint

    async def hangup_channel(self, channel):
        self.actions.append(("hangup", channel))


class Model:
    def __init__(self, outputs=(BLOCK,)):
        self.input = []
        self._outputs = outputs
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, frame):
        self.input.append(frame)

    async def outputs(self):
        for block in self._outputs:
            yield block
        await asyncio.Future()


def test_original_sip_endpoint_joins_main_bridge_directly():
    from conference.sip_session import SipSessionManager

    async def check():
        ari = Ari()
        manager = SipSessionManager(ari, Routing(), model_factory=Model)

        await manager.handle_stasis_start("sip-1983", "1983")

        assert ("answer", "sip-1983") in ari.actions
        assert ("add", "phoneguy-main", "sip-1983") in ari.actions
        assert not ari.media
        await manager.close("sip-1983")

    asyncio.run(check())


def test_phone_guy_raw_channel_never_joins_main_and_only_converted_frames_are_injected():
    from conference.sip_session import SipSessionManager

    async def check():
        ari = Ari()
        models = []

        def model_factory():
            model = Model()
            models.append(model)
            return model

        manager = SipSessionManager(ari, Routing(), model_factory=model_factory)
        await manager.handle_stasis_start("sip-1987", "1987")
        source, injection = ari.media

        assert ("answer", "sip-1987") in ari.actions
        raw_main_adds = [item for item in ari.actions if item == ("add", "phoneguy-main", "sip-1987")]
        assert raw_main_adds == []
        assert ("add", "phoneguy-source-sip-1987", "sip-1987") in ari.actions
        assert ("add", "phoneguy-main", "media-injection-sip-1987") in ari.actions

        await source.incoming.put(FRAME)
        for _ in range(50):
            if len(injection.sent) == 50:
                break
            await asyncio.sleep(0)
        assert models[0].input == [FRAME]
        assert injection.sent == [FRAME] * 50
        await manager.close("sip-1987")
        assert source.closed and injection.closed

    asyncio.run(check())


def test_second_phone_guy_and_unknown_extension_fail_without_adding_raw_audio():
    from conference.sip_session import SipSessionError, SipSessionManager

    async def check():
        ari = Ari()
        manager = SipSessionManager(ari, Routing(), model_factory=Model)
        await manager.handle_stasis_start("sip-1987", "1987")
        with pytest.raises(SipSessionError, match="busy"):
            await manager.handle_stasis_start("second", "1987")
        with pytest.raises(SipSessionError, match="unknown"):
            await manager.handle_stasis_start("unknown", "9999")
        assert not [item for item in ari.actions if item == ("add", "phoneguy-main", "sip-1987")]
        assert not [item for item in ari.actions if item == ("add", "phoneguy-main", "second")]
        await manager.close("sip-1987")

    asyncio.run(check())


def test_browser_phone_call_injects_only_converted_microphone_audio_and_hangs_up_phone():
    """The browser is an RVC source, never a raw member of the SIP bridge."""
    from conference.sip_session import SipSessionManager

    async def check():
        ari = Ari()
        models = []

        def model_factory():
            model = Model()
            models.append(model)
            return model

        manager = SipSessionManager(ari, Routing(), model_factory=model_factory)
        call_id = await manager.start_browser_call("1983")
        injection = ari.media[0]

        assert ("originate", "1983", "phoneguy-sip", "Phone Guy Browser", 30) in ari.actions
        assert ("add", "phoneguy-main", injection.channel_id) in ari.actions
        assert not [item for item in ari.actions if item == ("add", "phoneguy-main", call_id)]

        await manager.send_browser_audio(call_id, FRAME)
        for _ in range(50):
            if len(injection.sent) == 50:
                break
            await asyncio.sleep(0)
        assert models[0].input == [FRAME]
        assert injection.sent == [FRAME] * 50

        await manager.close_browser_call(call_id)
        assert injection.closed
        assert ("hangup", "sip-outbound-1983") in ari.actions

    asyncio.run(check())
