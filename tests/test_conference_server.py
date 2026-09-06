import asyncio
import json

import httpx
import pytest

from test_conference_session import setup_session, until


class Browser:
    """Drive the actual ASGI app without network listeners or thread timing."""
    def __init__(self, app, origin="https://voice.lan.awesomeio.ru"):
        self.app = app
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.scope = dict(type="websocket", asgi={"version": "3.0"},
                          scheme="wss", path="/ws/conference", raw_path=b"/ws/conference",
                          query_string=b"", headers=[(b"origin", origin.encode())],
                          client=("127.0.0.1", 1), server=("test", 443), subprotocols=[])

    async def open(self):
        self.incoming.put_nowait({"type": "websocket.connect"})
        self.task = asyncio.create_task(self.app(self.scope, self.incoming.get,
                                                self.outgoing.put))
        return await self.next()

    async def next(self):
        return await asyncio.wait_for(self.outgoing.get(), 1)

    def send(self, value):
        self.incoming.put_nowait({"type": "websocket.receive",
                                 "bytes" if isinstance(value, bytes) else "text": value})

    async def status(self):
        return json.loads((await self.next())["text"])

    async def finish(self):
        await asyncio.wait_for(self.task, 1)


def build(**kwargs):
    from conference.server import create_app
    session, rooms, models = setup_session()
    calls = []

    def factory():
        calls.append(True)
        return session

    async def healthy():
        return True

    return create_app(factory, health_check=kwargs.pop("health_check", healthy), **kwargs), session, rooms, models, calls


def test_default_configuration_targets_approved_loopback_ari(monkeypatch):
    from conference import server

    async def check():
        monkeypatch.delenv("CONFERENCE_ARI_URL", raising=False)
        monkeypatch.delenv("CONFERENCE_ARI_USERNAME", raising=False)
        monkeypatch.setenv("CONFERENCE_ARI_PASSWORD", "test-secret")
        session = server.default_session()
        assert session.room_factory().url == "http://127.0.0.1:8092/ari"

        requests = []

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                pass

            async def get(self, url, **kwargs):
                requests.append((url, kwargs))
                return httpx.Response(200)

        monkeypatch.setattr(server.httpx, "AsyncClient", Client)
        assert await server.asterisk_available()
        assert requests[0][0] == "http://127.0.0.1:8092/ari/asterisk/info"

    asyncio.run(check())


def test_allowed_listen_ready_pcm_stop_and_single_application_session():
    async def check():
        app, session, rooms, models, calls = build()
        browser = Browser(app)
        assert (await browser.open())["type"] == "websocket.accept"
        browser.send(json.dumps({"type": "listen", "version": 1}))
        assert await browser.status() == {"type": "preparing"}
        assert await browser.status() == dict(type="ready", version=1, sampleRate=48000,
                                              channels=1, sampleFormat="s16le", frameBytes=1920)
        rooms[0].channels["listener"].incoming.put_nowait(b"M" * 1920)
        assert (await browser.next())["bytes"] == b"M" * 1920
        browser.send('{"type":"stop"}')
        assert await browser.status() == {"type": "stopped"}
        assert (await browser.next())["type"] == "websocket.close"
        await browser.finish()
        assert session.closed and models[0].closed and rooms[0].closed and len(calls) == 1
    asyncio.run(check())


def test_bad_origin_is_rejected_before_session_join():
    async def check():
        app, session, rooms, models, calls = build()
        browser = Browser(app, "https://attacker.example")
        assert await browser.open() == {"type": "websocket.close", "code": 1008, "reason": ""}
        await browser.finish()
        assert rooms == models == []
    asyncio.run(check())


@pytest.mark.parametrize("control", [b"audio", "x" * 1025, "[]", "{", '{}',
    '{"type":"listen","version":true}', '{"type":"start","version":1}',
    '{"type":"listen","version":1,"sampleRate":16000}'])
def test_invalid_first_control_does_not_start_resources(control):
    async def check():
        app, session, rooms, models, calls = build()
        browser = Browser(app)
        await browser.open()
        browser.send(control)
        assert await browser.status() == {"type": "error", "code": "invalid_control"}
        assert await browser.status() == {"type": "stopped"}
        await browser.finish()
        assert rooms == models == []
    asyncio.run(check())


@pytest.mark.parametrize("control", [b"audio", '{"type":"listen","version":1}',
                                     '{"type":"stop","extra":1}', "x" * 1025])
def test_only_stop_is_accepted_after_ready(control):
    async def check():
        app, session, rooms, models, calls = build()
        browser = Browser(app)
        await browser.open()
        browser.send('{"type":"listen","version":1}')
        await browser.status()
        await browser.status()
        browser.send(control)
        assert await browser.status() == {"type": "error", "code": "invalid_control"}
        assert await browser.status() == {"type": "stopped"}
        await browser.finish()
        assert session.closed and models[0].closed
    asyncio.run(check())


def test_disconnect_during_startup_cleans_partial_resources():
    async def check():
        from conference.server import create_app
        from conference.session import DemoSession
        from test_conference_session import Room, Model
        room, model = Room(), Model()
        model.start_gate = asyncio.Event()
        session = DemoSession(lambda: room, lambda: model)
        browser = Browser(create_app(lambda: session))
        await browser.open()
        browser.send('{"type":"listen","version":1}')
        assert await browser.status() == {"type": "preparing"}
        await model.entered.wait()
        browser.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await browser.finish()
        assert room.closed and model.closed and session.closed
    asyncio.run(check())


def test_health_reports_unavailable_idle_and_active_without_details():
    async def check():
        availability = [True]

        async def probe():
            if not availability[0]:
                raise RuntimeError("http://username:password@private/ari")
            return True

        app, session, rooms, models, calls = build(health_check=probe)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/healthz")
            assert response.status_code == 200 and response.json() == {"status": "idle"}
            listener = await session.join()
            assert (await client.get("/healthz")).json() == {"status": "active"}
            availability[0] = False
            response = await client.get("/healthz")
            assert response.status_code == 503 and response.json() == {"status": "asterisk_unavailable"}
            await session.leave(listener)
    asyncio.run(check())


def test_first_control_timeout_closes_without_join():
    async def check():
        app, session, rooms, models, calls = build(control_timeout=.01)
        browser = Browser(app)
        await browser.open()
        assert await browser.status() == {"type": "error", "code": "stalled"}
        assert await browser.status() == {"type": "stopped"}
        await browser.finish()
        assert not rooms and not models
    asyncio.run(check())


def test_blocked_audio_send_times_out_and_releases_last_listener():
    async def check():
        app, session, rooms, models, calls = build(send_timeout=.01)
        browser = Browser(app)
        original_put = browser.outgoing.put

        async def blocked_audio(message):
            if "bytes" in message:
                await asyncio.Future()
            await original_put(message)

        browser.outgoing.put = blocked_audio
        await browser.open()
        browser.send('{"type":"listen","version":1}')
        await browser.status()
        await browser.status()
        rooms[0].channels["listener"].incoming.put_nowait(b"M" * 1920)
        assert await browser.status() == {"type": "error", "code": "stalled"}
        assert await browser.status() == {"type": "stopped"}
        await browser.finish()
        assert session.closed and rooms[0].closed and models[0].closed
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(check())
