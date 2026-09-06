import asyncio
import json

import httpx
import pytest


class Socket:
    def __init__(self, *messages):
        self.incoming = asyncio.Queue()
        for message in messages:
            self.incoming.put_nowait(message)
        self.sent = []
        self.closed = False

    async def recv(self):
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send(self, value):
        self.sent.append(value)

    async def close(self):
        self.closed = True


READY = dict(type="ready", version=1, sampleRate=48000, channels=1,
             sampleFormat="s16le", frameBytes=1920, outputSamples=96000)


def metrics(start=0, **changes):
    return json.dumps(dict(type="metrics", outputStart=start, outputSamples=96000,
                           consumedSamples=start + 96000, processingMs=120.5) | changes)


def test_rvc_handshake_pacing_output_and_stop():
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(json.dumps(READY), metrics(), bytes(192000), metrics(96000), bytes(192000))
        connections = []
        now = [10.0]
        sleeps = []

        async def connect(url, **kwargs):
            connections.append((url, kwargs))
            return socket

        async def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        async with RvcStream("ws://rvc/ws/rvc", connect=connect, clock=lambda: now[0], sleep=sleep) as stream:
            for _ in range(3):
                await stream.send(bytes(1920))
            output = stream.outputs()
            assert len(await anext(output)) == 192000
            assert len(await anext(output)) == 192000
            assert stream.metrics["outputStart"] == 96000
        assert len(connections) == 1
        assert connections[0][1]["origin"] == "https://voice.lan.awesomeio.ru"
        assert json.loads(socket.sent[0]) == dict(type="start", version=1, sampleRate=48000, channels=1, sampleFormat="s16le")
        assert sleeps == pytest.approx([.02, .02])
        assert json.loads(socket.sent[-1]) == {"type": "stop"}
        assert socket.closed
    asyncio.run(check())


@pytest.mark.parametrize("opening", [json.dumps({"type": "error", "code": "busy"}),
    "{}", "[]", "not-json", json.dumps(READY | {"frameBytes": 160}),
    json.dumps(READY | {"version": True})])
def test_rvc_rejects_busy_or_malformed_ready_and_closes(opening):
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(opening)
        async def connect(*args, **kwargs):
            return socket
        with pytest.raises(ValueError):
            async with RvcStream("ws://rvc/ws/rvc", connect=connect):
                pytest.fail("invalid session accepted")
        assert socket.closed
        assert not any(isinstance(v, bytes) for v in socket.sent)
    asyncio.run(check())


@pytest.mark.parametrize("messages", [
    [metrics(1), bytes(192000)], [metrics(), bytes(1920)], [bytes(192000)],
    [metrics(outputSamples=100), bytes(192000)], [metrics(consumedSamples=0), bytes(192000)],
    [metrics(processingMs=float("nan")), bytes(192000)], ["{}"], ["[]"], ["bad"],
    [metrics(), metrics()], [ConnectionError("disconnect")],
])
def test_rvc_invalid_output_fails_closed(messages):
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(json.dumps(READY), *messages)
        async def connect(*args, **kwargs):
            return socket
        async with RvcStream("ws://rvc/ws/rvc", connect=connect) as stream:
            with pytest.raises((ValueError, ConnectionError)):
                await anext(stream.outputs())
            assert socket.closed
            with pytest.raises(ValueError):
                await stream.send(bytes(1920))
    asyncio.run(check())


def test_rvc_warming_then_ready_cancellation_sends_stop():
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(json.dumps({"type": "warming", "timeoutSeconds": 90}), json.dumps(READY))
        async def connect(*args, **kwargs):
            return socket
        entered = asyncio.Event()
        async def task():
            async with RvcStream("ws://rvc/ws/rvc", connect=connect):
                entered.set()
                await asyncio.Future()
        runner = asyncio.create_task(task())
        try:
            await asyncio.wait_for(entered.wait(), 1)
        except BaseException:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            raise
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert json.loads(socket.sent[-1]) == {"type": "stop"}
        assert socket.closed
    asyncio.run(check())


@pytest.mark.parametrize("origin", [
    "https://voice.lan.awesomeio.ru",
    "https://vm-voice-1.lan.awesomeio.ru",
])
def test_rvc_uses_allowed_native_origin(origin):
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(json.dumps(READY))
        connections = []

        async def connect(url, **kwargs):
            connections.append((url, kwargs))
            return socket

        async with RvcStream("ws://rvc/ws/rvc", connect=connect, origin=origin):
            pass
        assert connections[0][1]["origin"] == origin

    asyncio.run(check())


def test_rvc_rejects_unapproved_origin_before_connecting():
    from conference.rvc import RvcStream

    async def check():
        connected = False

        async def connect(*args, **kwargs):
            nonlocal connected
            connected = True

        with pytest.raises(ValueError, match="origin"):
            RvcStream("ws://rvc/ws/rvc", connect=connect, origin="https://example.test")
        assert not connected

    asyncio.run(check())


def test_rvc_rejects_wrong_input_frame_without_sending_audio():
    from conference.rvc import RvcStream

    async def check():
        socket = Socket(json.dumps(READY))

        async def connect(*args, **kwargs):
            return socket

        async with RvcStream("ws://rvc/ws/rvc", connect=connect) as stream:
            with pytest.raises(ValueError, match="frame"):
                await stream.send(bytes(1919))
        assert not any(isinstance(message, bytes) for message in socket.sent)

    asyncio.run(check())


class Ari:
    def __init__(self, *, fail=None, start_changes=None, missing_id=False):
        self.events = Socket()
        self.sockets = []
        self.requests = []
        self.fail = fail
        self.start_changes = start_changes or {}
        self.missing_id = missing_id
        self.channel_id = None
        self.connections = []
        self.actions = []

    async def request(self, method, path, **kwargs):
        params = kwargs.get("params", {})
        self.requests.append((method, path, params))
        self.actions.append(f"{method} {path}")
        status = 200
        data = {}
        if path == "/channels/create":
            self.channel_id = params["channelId"]
            data = {"id": self.channel_id}
        if path.endswith("/variable"):
            data = {} if self.missing_id else {"value": "connection-1"}
        if self.fail and path.endswith(self.fail[0]):
            status = self.fail[1]
        return httpx.Response(status, json=data, request=httpx.Request(method, "http://asterisk/ari" + path))

    async def connect(self, url, **kwargs):
        self.connections.append((url, kwargs))
        if "/events?" in url:
            self.actions.append("CONNECT events")
            return self.events
        self.actions.append("CONNECT media")
        start = dict(event="MEDIA_START", connection_id="connection-1", channel="WebSocket/connection-1",
                     channel_id=self.channel_id, format="slin48", optimal_frame_size=1920, ptime=20)
        start.update(self.start_changes)
        socket = Socket("MEDIA_START " + " ".join(f"{k}:{v}" for k, v in start.items() if k != "event"))
        original_send = socket.send
        async def send(value):
            await original_send(value)
            self.actions.append(f"MEDIA {value}" if isinstance(value, str) else "MEDIA binary")
            if value == "ANSWER":
                self.events.incoming.put_nowait(json.dumps({"type": "ChannelStateChange", "channel": {"id": self.channel_id, "state": "Up"}}))
        socket.send = send
        self.sockets.append(socket)
        return socket


def room(ari, **kwargs):
    from conference.asterisk import AsteriskRoom
    return AsteriskRoom("http://asterisk:8088/ari", "demo", "test-secret", http=ari, connect=ari.connect, **kwargs)


def test_ari_create_connect_dial_answer_continue_and_owned_cleanup():
    async def check():
        ari = Ari()
        async with room(ari) as instance:
            a = await instance.open_channel("A")
            assert a.channel_id.startswith("phoneguy-demo-")
            await a.send_pcm(bytes(1920))
            creates = [p for m, path, p in ari.requests if path == "/channels/create"]
            assert creates[0] == dict(endpoint="WebSocket/INCOMING/c(slin48)n", app=instance.app, channelId=a.channel_id, formats="slin48")
            assert ("GET", f"/channels/{a.channel_id}/variable", {"variable": "MEDIA_WEBSOCKET_CONNECTION_ID"}) in ari.requests
            assert ("POST", f"/channels/{a.channel_id}/dial", {"timeout": 10}) in ari.requests
            assert ("POST", f"/channels/{a.channel_id}/continue", {"context": "phoneguy", "extension": "demo", "priority": 1}) in ari.requests
            assert ari.sockets[0].sent == ["ANSWER", bytes(1920)]
            assert ari.connections[1][0] == "ws://asterisk:8088/media/connection-1"
            assert ari.actions[1:8] == [
                "POST /channels/create",
                f"GET /channels/{a.channel_id}/variable",
                "CONNECT media",
                f"POST /channels/{a.channel_id}/dial",
                "MEDIA ANSWER",
                f"POST /channels/{a.channel_id}/continue",
                "MEDIA binary",
            ]
            b = await instance.open_channel("B")
            assert a.channel_id != b.channel_id
        deleted = [path for method, path, _ in ari.requests if method == "DELETE"]
        assert set(deleted) == {f"/channels/{a.channel_id}", f"/channels/{b.channel_id}"}
        assert len(deleted) == 2
        assert all(socket.closed for socket in [ari.events, *ari.sockets])
    asyncio.run(check())


@pytest.mark.parametrize("kwargs", [dict(fail=("/create", 500)), dict(fail=("/variable", 403)),
    dict(fail=("/dial", 409)), dict(fail=("/continue", 500)), dict(missing_id=True),
    dict(start_changes={"format": "ulaw"}), dict(start_changes={"optimal_frame_size": 160}),
    dict(start_changes={"channel_id": "unowned"})])
def test_ari_partial_failure_cleans_only_attempted_owned_channel(kwargs):
    async def check():
        ari = Ari(**kwargs)
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            async with room(ari) as instance:
                await instance.open_channel("C")
        deleted = [path for method, path, _ in ari.requests if method == "DELETE"]
        assert deleted == [f"/channels/{ari.channel_id}"]
        assert all(socket.closed for socket in [ari.events, *ari.sockets])
    asyncio.run(check())


def test_ari_create_collision_never_deletes_someone_elses_channel():
    async def check():
        ari = Ari(fail=("/create", 409))
        with pytest.raises(httpx.HTTPStatusError):
            async with room(ari) as instance:
                await instance.open_channel("A")
        assert not [r for r in ari.requests if r[0] == "DELETE"]
    asyncio.run(check())


def test_asterisk_xoff_xon_backlog_disconnect_and_no_echo():
    async def check():
        ari = Ari()
        async with room(ari) as instance:
            channel = await instance.open_channel("A")
            socket = ari.sockets[0]
            socket.incoming.put_nowait(bytes(1920))
            socket.incoming.put_nowait("MEDIA_XOFF")
            await asyncio.sleep(0)
            assert socket.sent == ["ANSWER"]
            sender = asyncio.create_task(channel.send_pcm(bytes(1920)))
            await asyncio.sleep(0)
            assert not sender.done()
            with pytest.raises(ValueError, match="backlog"):
                await channel.send_pcm(bytes(1920))
            socket.incoming.put_nowait("MEDIA_XON")
            await asyncio.wait_for(sender, 1)
            assert socket.sent == ["ANSWER", bytes(1920)]
            with pytest.raises(ValueError):
                await channel.receive_pcm()
            with pytest.raises(ValueError):
                await channel.send_pcm(bytes(192000))
            socket.incoming.put_nowait(ConnectionError("disconnect"))
            await asyncio.sleep(0)
            with pytest.raises(ConnectionError):
                await channel.send_pcm(bytes(1920))
    asyncio.run(check())


def test_listener_receives_only_binary_and_rejects_backlog():
    async def check():
        ari = Ari()
        async with room(ari, receive_queue_frames=2) as instance:
            listener = await instance.open_channel("listener")
            socket = ari.sockets[0]
            socket.incoming.put_nowait("MEDIA_XON")
            socket.incoming.put_nowait(b"\x02" * 1920)
            assert await listener.receive_pcm() == b"\x02" * 1920
            with pytest.raises(ValueError):
                await listener.send_pcm(bytes(1920))
            for _ in range(3):
                socket.incoming.put_nowait(bytes(1920))
            await asyncio.sleep(0)
            with pytest.raises(ValueError, match="backlog"):
                await listener.receive_pcm()
    asyncio.run(check())


def test_asterisk_rejects_wrong_incoming_binary_frame():
    async def check():
        ari = Ari()
        async with room(ari) as instance:
            channel = await instance.open_channel("B")
            socket = ari.sockets[0]
            socket.incoming.put_nowait(bytes(1919))
            await asyncio.sleep(0)
            assert socket.closed
            with pytest.raises(ValueError, match="frame"):
                await channel.send_pcm(bytes(1920))

    asyncio.run(check())
