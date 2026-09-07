"""Model-free integration tests of real WebSocket session ownership."""

import asyncio
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rvc_service.server import create_app


ORIGIN = {"origin": "https://voice.lan.awesomeio.ru"}
START = {"type": "start", "version": 1, "sampleRate": 48000,
         "channels": 1, "sampleFormat": "s16le"}
FRAME = b"\x00\x10" * 960


class Converter:
    def convert(self, window):
        return window * 2


class BlockingConverter(Converter):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def convert(self, window):
        self.entered.set()
        assert self.release.wait(5), "test did not release conversion"
        return super().convert(window)


def start(socket):
    socket.send_json(START)
    message = socket.receive_json()
    if message["type"] == "warming":
        message = socket.receive_json()
    assert message["type"] == "ready"


def frames(socket, count):
    for _ in range(count):
        socket.send_bytes(FRAME)


def wait_idle(client):
    deadline = time.monotonic() + 2
    while client.get("/healthz").json()["active"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_converted_pcm_has_fixed_timeline_and_stop_allows_restart():
    with TestClient(create_app(Converter)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            start(socket)
            for count, offset in [(105, 0), (100, 96000)]:
                frames(socket, count)
                metadata = socket.receive_json()
                assert metadata["type"] == "metrics"
                assert metadata["outputStart"] == offset
                assert metadata["outputSamples"] == 96000
                assert metadata["consumedSamples"] == offset + 96000
                assert 0 <= metadata["processingMs"] < 10000
                pcm = np.frombuffer(socket.receive_bytes(), dtype="<i2")
                assert pcm.shape == (96000,)
                assert np.all(pcm == 8192)
            socket.send_json({"type": "stop"})
            assert socket.receive_json()["type"] == "stopped"
        wait_idle(client)
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            start(socket)


@pytest.mark.parametrize("origin", [None, "https://evil.test", "null"])
def test_rejects_untrusted_or_missing_origin(origin):
    with TestClient(create_app(Converter)) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws/rvc", headers={} if origin is None else {"origin": origin}):
                pass
        assert error.value.code == 1008


@pytest.mark.parametrize("opening", [dict(START, version=2), dict(START, sampleRate=16000),
                                    dict(START, channels=2), dict(START, sampleFormat="f32le"), []])
def test_rejects_invalid_start(opening):
    with TestClient(create_app(Converter)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            socket.send_json(opening)
            assert socket.receive_json()["code"] == "invalid_start"


@pytest.mark.parametrize("frame", [b"bad", b"\0" * 1922, b""])
def test_rejects_invalid_frame(frame):
    with TestClient(create_app(Converter)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            start(socket)
            socket.send_bytes(frame)
            assert socket.receive_json()["code"] == "invalid_frame"


@pytest.mark.parametrize("stop", [False, True])
def test_disconnect_or_stop_retains_ownership_until_worker_really_finishes(stop):
    engine = BlockingConverter()
    with TestClient(create_app(lambda: engine)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            try:
                start(socket)
                frames(socket, 105)
                assert engine.entered.wait(2)
                if stop:
                    socket.send_json({"type": "stop"})
                    assert socket.receive_json()["type"] == "stopped"
                else:
                    socket.close()
                with client.websocket_connect("/ws/rvc", headers=ORIGIN) as other:
                    assert other.receive_json()["code"] == "busy"
                assert client.get("/healthz").json()["active"]
            finally:
                engine.release.set()
        wait_idle(client)
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as fresh:
            start(fresh)
            frames(fresh, 105)
            assert fresh.receive_json()["outputStart"] == 0
            assert len(fresh.receive_bytes()) == 192000


def test_receiver_overload_is_detected_while_conversion_is_blocked():
    engine = BlockingConverter()
    with TestClient(create_app(lambda: engine)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            try:
                start(socket)
                frames(socket, 105)
                assert engine.entered.wait(2)
                frames(socket, 200)
                assert socket.receive_json()["code"] == "overloaded"
                health = client.get("/healthz").json()
                assert health["active"]
                assert health["running"]
                assert 0 <= health["queuedWindows"] <= 1
            finally:
                engine.release.set()


def test_warmup_does_not_block_health_or_socket_and_then_becomes_ready():
    release = threading.Event()

    def factory():
        assert release.wait(5)
        return Converter()

    with TestClient(create_app(factory)) as client:
        try:
            response = client.get("/healthz")
            assert response.status_code == 503
            assert response.json()["status"] == "warming"
            with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
                socket.send_json(START)
                assert socket.receive_json()["type"] == "warming"
                release.set()
                assert socket.receive_json()["type"] == "ready"
                assert client.get("/healthz").status_code == 200
        finally:
            release.set()


def test_model_failure_has_actionable_state_without_exception_details():
    def factory():
        raise RuntimeError("private path or model internals")

    with TestClient(create_app(factory)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            socket.send_json(START)
            response = socket.receive_json()
            if response["type"] == "warming":
                response = socket.receive_json()
            assert response["code"] == "model_unavailable"
            assert "private" not in str(response)
        assert client.get("/healthz").status_code == 503


def test_invalid_converted_samples_are_not_sent():
    class InvalidConverter:
        def convert(self, window):
            return np.full(window.shape, np.nan)

    with TestClient(create_app(InvalidConverter)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            start(socket)
            frames(socket, 105)
            assert socket.receive_json()["code"] == "model_unavailable"


def test_idle_session_has_stalled_deadline(monkeypatch):
    monkeypatch.setattr("rvc_service.server.STALL_SECONDS", 0.1)
    with TestClient(create_app(Converter)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            start(socket)
            assert socket.receive_json()["code"] == "stalled"


def test_model_startup_deadline_keeps_health_unavailable(monkeypatch):
    monkeypatch.setattr("rvc_service.server.STARTUP_SECONDS", 0.1)
    release = threading.Event()

    def factory():
        assert release.wait(5)
        return Converter()

    with TestClient(create_app(factory)) as client:
        try:
            with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
                socket.send_json(START)
                assert socket.receive_json()["type"] == "warming"
                assert socket.receive_json()["code"] == "model_unavailable"
            assert client.get("/healthz").status_code == 503
        finally:
            release.set()


def test_inference_deadline_retains_worker_ownership(monkeypatch):
    monkeypatch.setattr("rvc_service.server.STALL_SECONDS", 0.2)
    engine = BlockingConverter()
    with TestClient(create_app(lambda: engine)) as client:
        with client.websocket_connect("/ws/rvc", headers=ORIGIN) as socket:
            try:
                start(socket)
                frames(socket, 105)
                assert engine.entered.wait(2)
                assert socket.receive_json()["code"] == "stalled"
                with client.websocket_connect("/ws/rvc", headers=ORIGIN) as other:
                    assert other.receive_json()["code"] == "busy"
            finally:
                engine.release.set()


@pytest.mark.parametrize("repeat_interval", [0, 0.02])
def test_asgi_task_cancellation_cannot_release_running_worker(repeat_interval):
    import json

    async def scenario():
        engine = BlockingConverter()
        app = create_app(lambda: engine)
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        scope = {"type": "websocket", "asgi": {"version": "3.0"},
                 "scheme": "ws", "path": "/ws/rvc", "raw_path": b"/ws/rvc",
                 "query_string": b"", "root_path": "",
                 "headers": [(b"origin", ORIGIN["origin"].encode())],
                 "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8090),
                 "subprotocols": []}
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
            try:
                await incoming.put({"type": "websocket.connect"})
                assert (await outgoing.get())["type"] == "websocket.accept"
                await incoming.put({"type": "websocket.receive", "text": json.dumps(START)})
                response = json.loads((await outgoing.get())["text"])
                if response["type"] == "warming":
                    response = json.loads((await outgoing.get())["text"])
                assert response["type"] == "ready"
                for _ in range(105):
                    await incoming.put({"type": "websocket.receive", "bytes": FRAME})
                assert await asyncio.to_thread(engine.entered.wait, 2)
                task.cancel()
                await asyncio.sleep(repeat_interval)
                assert not task.done()
                assert app.state.active
                # A second cancellation must also be unable to skip the drain.
                task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done()
                assert app.state.active
            finally:
                engine.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not app.state.active
            assert outgoing.qsize() == 1  # close only, no stale metrics or PCM

    asyncio.run(scenario())


@pytest.mark.parametrize("held_type", ["metrics", "binary"])
@pytest.mark.parametrize("disconnect", [False, True])
def test_stop_or_disconnect_invalidates_backpressured_output(held_type, disconnect):
    import json

    async def scenario():
        app = create_app(Converter)
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        entered = asyncio.Event()
        release = asyncio.Event()
        events = []
        scope = {"type": "websocket", "asgi": {"version": "3.0"},
                 "scheme": "ws", "path": "/ws/rvc", "raw_path": b"/ws/rvc",
                 "query_string": b"", "root_path": "",
                 "headers": [(b"origin", ORIGIN["origin"].encode())],
                 "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8090),
                 "subprotocols": []}

        async def receive():
            message = await incoming.get()
            if (message["type"] == "websocket.disconnect"
                    or message.get("text") == '{"type":"stop"}'):
                events.append("observed_stop")
            return message

        async def send(message):
            if message["type"] == "websocket.send":
                kind = json.loads(message["text"])["type"] if "text" in message else "binary"
                if kind == held_type:
                    entered.set()
                    await release.wait()
                events.append(kind)
            await outgoing.put(message)

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(scope, receive, send))
            try:
                await incoming.put({"type": "websocket.connect"})
                assert (await outgoing.get())["type"] == "websocket.accept"
                await incoming.put({"type": "websocket.receive", "text": json.dumps(START)})
                while json.loads((await outgoing.get())["text"])["type"] != "ready":
                    pass
                for _ in range(105):
                    await incoming.put({"type": "websocket.receive", "bytes": FRAME})
                await asyncio.wait_for(entered.wait(), 2)
                if disconnect:
                    await incoming.put({"type": "websocket.disconnect", "code": 1000})
                else:
                    await incoming.put({"type": "websocket.receive", "text": '{"type":"stop"}'})
                # Receiver and held sender become runnable in the same loop turn.
                release.set()
                await asyncio.wait_for(task, 2)
                after_stop = events[events.index("observed_stop") + 1:]
                assert "metrics" not in after_stop
                assert "binary" not in after_stop
                if not disconnect:
                    assert after_stop == ["stopped"]
                assert not app.state.active
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_v2_canary_route_streams_one_second_hops_and_rejects_version_one():
    with TestClient(create_app(Converter)) as client:
        with client.websocket_connect("/ws/rvc-v2", headers=ORIGIN) as socket:
            socket.send_json({**START, "version": 2})
            message = socket.receive_json()
            if message["type"] == "warming":
                message = socket.receive_json()
            assert message["type"] == "ready"
            assert message["version"] == 2
            assert message["outputSamples"] == 48_000
            frames(socket, 55)
            metadata = socket.receive_json()
            assert metadata["outputStart"] == 0
            assert metadata["outputSamples"] == 48_000
            assert metadata["consumedSamples"] == 48_000
            pcm = np.frombuffer(socket.receive_bytes(), dtype="<i2")
            assert pcm.shape == (48_000,)
            assert np.all(pcm == 8192)
            socket.send_json({"type": "stop"})
            assert socket.receive_json()["type"] == "stopped"

        with client.websocket_connect("/ws/rvc-v2", headers=ORIGIN) as socket:
            socket.send_json(START)  # version 1 on the v2 route is invalid
            error = socket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "invalid_start"
