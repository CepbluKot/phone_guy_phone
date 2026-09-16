"""Model-free tests of the self-monitor echo line wiring."""

import asyncio

import pytest

from conference.media import BLOCK_BYTES, FRAME_BYTES
from selfmonitor.ari import APP_NAME, parse_control
from selfmonitor.service import EchoService, EchoSession


class FakeAri:
    def __init__(self):
        self.calls = []
        self.bridges = set()
        self.bridge_channels = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        for name in list(self.bridges):
            await self.delete_bridge(name)

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path.lstrip("/"), kwargs.get("params")))

    async def answer(self, channel_id):
        self.calls.append(("POST", f"channels/{channel_id}/answer", None))

    async def hangup_busy(self, channel_id):
        self.calls.append(("POST", f"channels/{channel_id}/hangup", {"cause": "17"}))

    async def create_bridge(self, bridge_id):
        self.calls.append(("POST", "bridges", {"bridgeId": bridge_id, "type": "mixing"}))
        self.bridges.add(bridge_id)
        self.bridge_channels[bridge_id] = []

    async def add_to_bridge(self, bridge_id, channel_id):
        assert bridge_id in self.bridges, bridge_id
        self.calls.append(("POST", f"bridges/{bridge_id}/addChannel", {"channel": channel_id}))
        self.bridge_channels[bridge_id].append(channel_id)

    async def delete_bridge(self, bridge_id):
        self.bridges.discard(bridge_id)
        self.bridge_channels.pop(bridge_id, None)

    async def snoop(self, target, snoop_id):
        self.calls.append(("POST", f"channels/{target}/snoop",
                           {"spy": "in", "whisper": "none", "app": APP_NAME, "snoopId": snoop_id}))
        return snoop_id

    async def open_media(self, name, *, receive):
        return FakeMediaSide(f"media-{name}", receive)


class FakeMediaSide:
    def __init__(self, channel_id, receive):
        self.channel_id = channel_id
        self.receive = receive
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def recv_pcm(self):
        message = await self.incoming.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_pcm(self, frame):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(frame)

    async def close(self):
        self.closed = True


class FakeModel:
    def __init__(self, fail_enter=None):
        self.fail_enter = fail_enter
        self.sent = []
        self.blocks = asyncio.Queue()
        self.closed = False

    async def send(self, frame):
        self.sent.append(frame)

    async def outputs(self):
        while True:
            block = await self.blocks.get()
            if isinstance(block, BaseException):
                raise block
            yield block

    async def close(self):
        self.closed = True


def _service(ari, model):
    return EchoService(ari, model_factory=lambda: _ready(model))


def _ready(value):
    future = asyncio.get_running_loop().create_future()
    future.set_result(value)
    return future


def test_parse_control_accepts_media_flow_events():
    assert parse_control('{"event": "MEDIA_XON"}') == {"event": "MEDIA_XON"}
    # chan_websocket also emits plain "EVENT key:value" text controls.
    assert parse_control("MEDIA_START connection_id:abc channel_id:def") == {
        "event": "MEDIA_START", "connection_id": "abc", "channel_id": "def"
    }
    assert parse_control("MEDIA_XOFF") == {"event": "MEDIA_XOFF"}
    with pytest.raises(ValueError):
        parse_control(b"\x00\x01")
    with pytest.raises(ValueError):
        parse_control("broken duplicate:1 duplicate:2")
    with pytest.raises(ValueError):
        parse_control("")


def test_stasis_start_builds_snoop_and_two_bridges_in_order():
    async def scenario():
        ari = FakeAri()
        model = FakeModel()
        service = _service(ari, model)
        session = EchoSession(service, "chan-1")

        pump = asyncio.create_task(session.run())
        await asyncio.sleep(0.05)
        # Enough setup has happened for the wiring assertions.
        kinds = [(method, path) for method, path, _ in ari.calls]
        assert ("POST", "channels/chan-1/answer") in kinds
        assert ("POST", "channels/chan-1/snoop") in kinds
        assert sorted(ari.bridges) == [
            "selfmonitor-echo-chan-1",
            "selfmonitor-source-chan-1",
        ]
        # The caller shares the echo bridge only with the injection channel;
        # the source bridge never contains the caller.
        assert "chan-1" in ari.bridge_channels["selfmonitor-echo-chan-1"]
        assert "chan-1" not in ari.bridge_channels["selfmonitor-source-chan-1"]
        snoop_params = next(
            params for method, path, params in ari.calls
            if method == "POST" and path.endswith("/snoop")
        )
        assert snoop_params["spy"] == "in"
        assert snoop_params["whisper"] == "none"

        # Speech flows listener -> model, converted blocks flow back in 20 ms
        # frames towards the caller.
        await session.listener.incoming.put(b"\x01" + b"a" * (FRAME_BYTES - 1))
        await session.listener.incoming.put(b"\x02" + b"b" * (FRAME_BYTES - 1))
        await model.blocks.put(b"P" * BLOCK_BYTES)
        expected_frames = BLOCK_BYTES // FRAME_BYTES
        for _ in range(60):
            if len(session.injection.sent) >= expected_frames:
                break
            await asyncio.sleep(0.05)
        assert model.sent == [
            b"\x01" + b"a" * (FRAME_BYTES - 1),
            b"\x02" + b"b" * (FRAME_BYTES - 1),
        ]
        assert all(len(frame) == FRAME_BYTES for frame in session.injection.sent)
        assert len(session.injection.sent) == expected_frames

        await model.blocks.put(ConnectionError("rvc_down"))
        with pytest.raises(BaseException):
            await asyncio.wait_for(pump, 2)
        assert model.closed
        assert ari.bridges == set()

    asyncio.run(scenario())


def test_second_caller_gets_busy_while_echo_is_active():
    async def scenario():
        ari = FakeAri()
        model = FakeModel()
        service = _service(ari, model)
        first_call = asyncio.create_task(service.handle_stasis_start("chan-1", "1983"))
        await asyncio.sleep(0.05)

        await service.handle_stasis_start("chan-2", "2014")
        hangups = [path for method, path, _ in ari.calls
                   if method == "POST" and path.endswith("hangup")]
        assert "channels/chan-2/hangup" in hangups

        await service.handle_destroyed("chan-1")
        with pytest.raises(BaseException):
            await asyncio.wait_for(first_call, 2)
        # After teardown the next caller is accepted again.
        second_call = asyncio.create_task(service.handle_stasis_start("chan-3", "2014"))
        await asyncio.sleep(0.05)
        assert any(b == "selfmonitor-echo-chan-3" for b in ari.bridges)
        await service.handle_destroyed("chan-3")
        with pytest.raises(BaseException):
            await asyncio.wait_for(second_call, 2)

    asyncio.run(scenario())


def test_model_failure_tears_down_without_passthrough():
    async def scenario():
        ari = FakeAri()

        async def failing_factory():
            raise ConnectionError("busy")

        service = EchoService(ari, model_factory=failing_factory)
        session = EchoSession(service, "chan-9")
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(session.run(), 2)
        assert session.closed
        assert ari.bridges == set()

    asyncio.run(scenario())
