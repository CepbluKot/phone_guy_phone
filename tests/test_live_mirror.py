"""The phone mirrors the browser's rendered frames, not the SIP microphone."""

import asyncio

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from selfmonitor.service import MirrorRelay, MirrorService, MirrorSession, ORIGIN, handle_publisher
from selfmonitor.ari import SelfMonitorAri, parse_control
from selfmonitor.live_check import add_when_stasis, rms


FRAME = b"\x20\x03" * 960
SILENCE = b"\0" * 1920


class Media:
    def __init__(self):
        self.channel_id = "mirror-injection"
        self.sent = []
        self.closed = False

    async def send_pcm(self, frame):
        self.sent.append(frame)

    async def close(self):
        self.closed = True


class Ari:
    def __init__(self):
        self.actions = []
        self.media = Media()

    async def answer(self, channel):
        self.actions.append(("answer", channel))

    async def open_media(self, name, *, receive):
        assert receive is False
        self.actions.append(("open_media", name))
        return self.media

    async def create_bridge(self, bridge):
        self.actions.append(("create_bridge", bridge))

    async def add_to_bridge(self, bridge, channel):
        self.actions.append(("add", bridge, channel))

    async def delete_bridge(self, bridge):
        self.actions.append(("delete_bridge", bridge))

    async def hangup_busy(self, channel):
        self.actions.append(("hangup", channel))

    async def hangup(self, channel):
        self.actions.append(("hangup", channel))


def test_relay_buffers_short_network_bursts_in_order_and_rejects_duplicate_publisher():
    relay = MirrorRelay()
    first, second = object(), object()
    assert relay.take_frame() == SILENCE
    assert relay.claim(first)
    assert not relay.claim(second)
    assert not relay.publish(second, FRAME)
    assert not relay.publish(first, b"bad")
    frames = [(index.to_bytes(2, "little") * 960) for index in range(1, 11)]
    for frame in frames:
        assert relay.publish(first, frame)
    assert [relay.take_frame() for _ in frames] == frames
    assert relay.take_frame() == SILENCE
    relay.release(first)
    assert relay.take_frame() == SILENCE
    assert relay.claim(second)


def test_call_stays_up_through_silence_audio_silence_without_raw_path():
    async def scenario():
        ari, relay = Ari(), MirrorRelay()
        service = MirrorService(ari, relay)
        call = asyncio.create_task(service.handle_stasis_start("sip-1", "1983"))
        for _ in range(50):
            if len(ari.media.sent) >= 2:
                break
            await asyncio.sleep(.02)
        assert ari.media.sent[:2] == [SILENCE, SILENCE]
        owner = object()
        assert relay.claim(owner)
        for _ in range(relay.prebuffer_frames):
            assert relay.publish(owner, FRAME)
        for _ in range(50):
            if FRAME in ari.media.sent:
                break
            await asyncio.sleep(.02)
        assert FRAME in ari.media.sent
        relay.release(owner)
        for _ in range(50):
            if ari.media.sent[-2:] == [SILENCE, SILENCE]:
                break
            await asyncio.sleep(.02)
        assert ari.media.sent[-2:] == [SILENCE, SILENCE]
        assert ("add", "selfmonitor-mirror-sip-1", "sip-1") in ari.actions
        assert not any(action[0] == "snoop" for action in ari.actions)
        await service.handle_destroyed("sip-1")
        await asyncio.wait_for(call, 2)
        assert ari.media.closed
        assert ("delete_bridge", "selfmonitor-mirror-sip-1") in ari.actions
        assert ("hangup", "sip-1") in ari.actions

    asyncio.run(scenario())


def test_second_caller_is_busy_without_disrupting_first():
    async def scenario():
        ari, relay = Ari(), MirrorRelay()
        service = MirrorService(ari, relay)
        first = asyncio.create_task(service.handle_stasis_start("sip-1", "1983"))
        await asyncio.sleep(.05)
        await service.handle_stasis_start("sip-2", "2014")
        assert ("hangup", "sip-2") in ari.actions
        assert service.session.channel_id == "sip-1"
        await service.handle_destroyed("sip-1")
        await asyncio.wait_for(first, 2)

    asyncio.run(scenario())


def test_call_destroyed_before_session_starts_never_reopens_bridge():
    async def scenario():
        ari, relay = Ari(), MirrorRelay()
        session = MirrorSession(MirrorService(ari, relay), "gone")
        await session.close()
        await session.run()
        assert not any(action[0] in {"answer", "open_media", "create_bridge", "add"}
                       for action in ari.actions)

    asyncio.run(scenario())


def test_ari_hangup_targets_only_the_owned_call_channel():
    async def scenario():
        ari = SelfMonitorAri("http://127.0.0.1:8092/ari", "phoneguy", "unused",
                             stasis_handler=None, destroyed_handler=None)
        calls = []

        async def request(method, path, **kwargs):
            calls.append((method, path))

        ari.request = request
        await ari.hangup("sip-1")
        assert calls == [("DELETE", "/channels/sip-1")]

    asyncio.run(scenario())


def test_asterisk_control_parser_keeps_flow_control_compatibility():
    assert parse_control("MEDIA_XOFF") == {"event": "MEDIA_XOFF"}
    assert parse_control("MEDIA_START connection_id:abc channel_id:def") == {
        "event": "MEDIA_START", "connection_id": "abc", "channel_id": "def"
    }


def test_publisher_websocket_requires_private_origin_and_releases_audio_on_close():
    async def scenario():
        relay = MirrorRelay()
        async with serve(lambda socket: handle_publisher(socket, relay),
                         "127.0.0.1", 0, origins=[ORIGIN], max_size=1920) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/ws/live-mirror"
            with pytest.raises(InvalidStatus):
                async with connect(url, origin="https://wrong.example"):
                    pass
            async with connect(url, origin=ORIGIN) as socket:
                for _ in range(relay.prebuffer_frames):
                    await socket.send(FRAME)
                for _ in range(50):
                    if len(relay.frames) == relay.prebuffer_frames:
                        break
                    await asyncio.sleep(.01)
                assert relay.take_frame() == FRAME
            for _ in range(50):
                if relay.publisher is None:
                    break
                await asyncio.sleep(.01)
            assert relay.publisher is None
            assert relay.take_frame() == SILENCE

    asyncio.run(scenario())


def test_live_check_distinguishes_synthetic_speech_from_silence():
    assert rms(SILENCE) == 0
    assert rms(FRAME) > .02


def test_live_check_waits_for_local_caller_to_enter_stasis():
    import httpx

    class Room:
        def __init__(self):
            self.attempts = 0

        async def add_to_bridge(self, bridge, channel):
            self.attempts += 1
            if self.attempts == 1:
                request = httpx.Request("POST", "http://ari/bridges/test/addChannel")
                response = httpx.Response(422, text='{"message":"Channel not in Stasis application"}', request=request)
                raise httpx.HTTPStatusError("not ready", request=request, response=response)

    async def scenario():
        room = Room()
        await add_when_stasis(room, "bridge", "channel")
        assert room.attempts == 2

    asyncio.run(scenario())
