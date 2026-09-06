"""Receive-only browser gateway; this process never loads a voice model."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import httpx

from .asterisk import AsteriskRoom
from .media import FRAME_BYTES, finish_cleanup
from .rvc import ALLOWED_ORIGINS, RvcStream
from .session import DemoSession, Listener, SessionError, public_error


READY = dict(type="ready", version=1, sampleRate=48000, channels=1,
             sampleFormat="s16le", frameBytes=FRAME_BYTES)


def default_session():
    return DemoSession(
        lambda: AsteriskRoom(
            os.environ.get("CONFERENCE_ARI_URL", "http://127.0.0.1:8088/ari"),
            os.environ.get("CONFERENCE_ARI_USERNAME", "phoneguy"),
            os.environ.get("CONFERENCE_ARI_PASSWORD", ""),
        ),
        lambda: RvcStream(os.environ.get("CONFERENCE_RVC_URL", "ws://127.0.0.1:8090/ws/rvc")),
    )


async def asterisk_available():
    password = os.environ.get("CONFERENCE_ARI_PASSWORD")
    if not password:
        return False
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        response = await client.get(
            os.environ.get("CONFERENCE_ARI_URL", "http://127.0.0.1:8088/ari").rstrip("/")
            + "/asterisk/info",
            auth=(os.environ.get("CONFERENCE_ARI_USERNAME", "phoneguy"), password),
        )
        return response.status_code == 200


async def control(socket, *, first=False):
    message = await socket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise SessionError("invalid_control")
    try:
        value = json.loads(raw)
    except ValueError:
        raise SessionError("invalid_control") from None
    expected = {"type": "listen", "version": 1} if first else {"type": "stop"}
    if value != expected or (first and type(value.get("version")) is not int):
        raise SessionError("invalid_control")


async def send_status(socket, status, timeout):
    async with asyncio.timeout(timeout):
        await socket.send_json(status)


async def send_audio(socket, listener, timeout):
    terminal = asyncio.create_task(listener.done.wait(), name="conference-browser-terminal")
    frame_task = None
    try:
        while True:
            frame_task = asyncio.create_task(listener.queue.get(), name="conference-browser-frame")
            done, _ = await asyncio.wait((terminal, frame_task), return_when=asyncio.FIRST_COMPLETED)
            if terminal in done:
                return listener.terminal
            frame = frame_task.result()
            if not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
                raise SessionError("upstream_unavailable")
            async with asyncio.timeout(timeout):
                await socket.send_bytes(frame)
    finally:
        tasks = [t for t in (terminal, frame_task) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(session_factory=default_session, *, health_check=asterisk_available,
               control_timeout=10, send_timeout=10):
    session = session_factory()

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await finish_cleanup(session.close())

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session = session

    @app.get("/healthz")
    async def health():
        try:
            async with asyncio.timeout(3):
                available = await health_check()
        except Exception:
            available = False
        return JSONResponse(
            {"status": session.state if available else "asterisk_unavailable"},
            status_code=200 if available and session.state != "unavailable" else 503,
        )

    @app.websocket("/ws/conference")
    async def conference(socket: WebSocket):
        if socket.headers.get("origin") not in ALLOWED_ORIGINS:
            await socket.close(code=1008)
            return
        await socket.accept()
        joining = incoming = outgoing = None
        listener = None
        error = None
        disconnected = False
        try:
            async with asyncio.timeout(control_timeout):
                await control(socket, first=True)
            await send_status(socket, {"type": "preparing"}, send_timeout)
            joining = asyncio.create_task(session.join(), name="conference-browser-join")
            incoming = asyncio.create_task(control(socket), name="conference-browser-control")
            done, _ = await asyncio.wait((joining, incoming), return_when=asyncio.FIRST_COMPLETED)
            if incoming in done:
                incoming.result()
            else:
                listener = joining.result()
                await send_status(socket, READY, send_timeout)
                outgoing = asyncio.create_task(send_audio(socket, listener, send_timeout),
                                               name="conference-browser-audio")
                done, _ = await asyncio.wait((outgoing, incoming), return_when=asyncio.FIRST_COMPLETED)
                if incoming in done:
                    incoming.result()
                else:
                    terminal = outgoing.result()
                    error = terminal.get("code")
        except WebSocketDisconnect:
            disconnected = True
        except Exception as exc:
            error = public_error(exc)
        finally:
            async def cleanup():
                nonlocal listener
                tasks = [t for t in (joining, incoming, outgoing) if t is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                # A simultaneous stop and successful join still owns a listener.
                if joining is not None and not joining.cancelled() and joining.exception() is None:
                    listener = joining.result()
                if isinstance(listener, Listener):
                    await session.leave(listener)
            await finish_cleanup(cleanup())
        if not disconnected:
            with suppress(Exception):
                if error:
                    await send_status(socket, {"type": "error", "code": error}, send_timeout)
                await send_status(socket, {"type": "stopped"}, send_timeout)
                async with asyncio.timeout(send_timeout):
                    await socket.close(code=1000 if not error else 1008)

    return app


app = create_app()
