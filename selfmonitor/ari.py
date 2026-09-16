"""Minimal ARI client for the self-monitor echo line.

Adapts the proven patterns of conference/asterisk.py (chan_websocket media
channels with MEDIA_XOFF/XON flow control, event dispatch, owned cleanup)
down to exactly what one echo call needs. The application name is the fixed
``selfmonitor`` because the dialplan routes the 1999 extension through
Stasis(selfmonitor); the conference controller's app is separate and never
sees these channels.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
import uuid

import httpx
from websockets.asyncio.client import connect as websocket_connect

from conference.media import FRAME_BYTES


APP_NAME = "selfmonitor"
PASSIVE_CONTROL_EVENTS = {
    "DTMF_END",
    "QUEUE_DRAINED",
    "STATUS",
    "MEDIA_BUFFERING_COMPLETED",
    "MEDIA_MARK_PROCESSED",
}


def _websocket_url(url):
    parts = urlsplit(url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def parse_control(message):
    """Validate one chan_websocket control message and return its fields.

    Controls arrive either as JSON objects or as plain ``EVENT key:value``
    text; both forms are part of the chan_websocket wire format (mirrors
    conference/asterisk.py's _media_control).
    """
    if not isinstance(message, str):
        raise ValueError("invalid_asterisk_control")
    if message.startswith("{"):
        try:
            import json

            value = json.loads(message)
        except (TypeError, ValueError):
            raise ValueError("invalid_asterisk_control") from None
        if not isinstance(value, dict):
            raise ValueError("invalid_asterisk_control")
        return value

    fields = message.split()
    if not fields:
        raise ValueError("invalid_asterisk_control")
    value = {"event": fields[0]}
    for field in fields[1:]:
        key, separator, item = field.partition(":")
        if not separator or not key or key in value:
            raise ValueError("invalid_asterisk_control")
        value[key] = item
    return value


class MediaSide:
    """One owned chan_websocket channel: listener (receive) or injection."""

    def __init__(self, ari, channel_id, receive):
        self.ari = ari
        self.channel_id = channel_id
        self.receive = receive
        self.received = asyncio.Queue(maxsize=200)
        self.can_send = asyncio.Event()
        self.can_send.set()
        self.error = None
        self.closed = False
        self.sending = False
        self.reader = None

    def fail(self, error):
        self.error = error
        self.can_send.set()
        while not self.received.empty():
            self.received.get_nowait()
        self.received.put_nowait(error)

    async def read(self):
        try:
            while True:
                message = await self.ari.recv_media(self)
                if isinstance(message, bytes):
                    if len(message) != FRAME_BYTES:
                        raise ValueError("invalid_asterisk_frame")
                    if self.receive:
                        try:
                            self.received.put_nowait(message)
                        except asyncio.QueueFull:
                            raise ValueError("asterisk_receive_backlog") from None
                    continue
                event = parse_control(message).get("event")
                if event == "MEDIA_XOFF":
                    self.can_send.clear()
                elif event == "MEDIA_XON":
                    self.can_send.set()
                elif event not in PASSIVE_CONTROL_EVENTS:
                    raise ValueError("unexpected_asterisk_control")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.fail(error)

    async def recv_pcm(self):
        if not self.receive or self.closed:
            raise ValueError("invalid_asterisk_receive_state")
        if self.error:
            raise self.error
        message = await self.received.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_pcm(self, frame):
        if self.receive or self.closed or self.error:
            raise ValueError("invalid_asterisk_send_state")
        if not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
            raise ValueError("invalid_asterisk_frame")
        if self.sending:
            raise ValueError("asterisk_send_backlog")
        self.sending = True
        try:
            async with asyncio.timeout(10):
                await self.can_send.wait()
                if self.error:
                    raise self.error
                await self.ari.send_media(self, frame)
        except BaseException as error:
            self.fail(error)
            raise
        finally:
            self.sending = False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.fail(ConnectionError("asterisk_channel_closed"))
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.ari.close_media(self.channel_id)


class SelfMonitorAri:
    """Events socket, HTTP control, and chan_websocket media channels."""

    def __init__(
        self,
        ari_url,
        username,
        password,
        *,
        connect=websocket_connect,
        stasis_handler,
        destroyed_handler,
        spy_direction="in",
        timeout=10,
    ):
        if spy_direction not in {"in", "out"}:
            raise ValueError("invalid_spy_direction")
        self.url = ari_url.rstrip("/")
        self.auth = (username, password)
        self.connect = connect
        self.stasis_handler = stasis_handler
        self.destroyed_handler = destroyed_handler
        self.spy_direction = spy_direction
        self.timeout = timeout
        self.http = None
        self.events = None
        self.events_task = None
        self.handler_tasks = set()
        self.media = {}
        self.media_sockets = {}
        self.sip_channels = {}
        self.error = None
        self.closed = False

    async def __aenter__(self):
        if self.http is not None or self.closed:
            raise ValueError("selfmonitor_ari_already_used")
        self.http = httpx.AsyncClient(
            base_url=self.url + "/", auth=self.auth, timeout=self.timeout
        )
        token = base64.b64encode(":".join(self.auth).encode()).decode()
        events_url = self.url + "/events?" + urlencode({"app": APP_NAME})
        self.events = await self.connect(
            _websocket_url(events_url),
            additional_headers={"Authorization": "Basic " + token},
            max_size=65536,
            max_queue=16,
            open_timeout=self.timeout,
            close_timeout=2,
        )
        self.events_task = asyncio.create_task(self._read_events())
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        error = self.error
        for media in list(self.media.values()):
            with suppress(Exception):
                await media.close()
        if self.events_task is not None:
            self.events_task.cancel()
            with suppress(BaseException):
                await self.events_task
        if self.events is not None:
            with suppress(Exception):
                await self.events.close()
        for socket in self.media_sockets.values():
            with suppress(Exception):
                await socket.close()
        if self.http is not None:
            with suppress(Exception):
                await self.http.aclose()
        if error is not None:
            raise error

    async def request(self, method, path, **kwargs):
        response = await self.http.request(method, path.lstrip("/"), **kwargs)
        response.raise_for_status()
        return response

    async def _read_events(self):
        import json

        try:
            while True:
                event = json.loads(await self.events.recv())
                if not isinstance(event, dict):
                    raise ValueError("invalid_ari_event")
                channel = event.get("channel") if isinstance(event.get("channel"), dict) else {}
                channel_id = channel.get("id")
                if event.get("type") == "StasisStart" and event.get("application") == APP_NAME:
                    name = channel.get("name")
                    if not isinstance(channel_id, str) or not isinstance(name, str):
                        continue
                    # Real callers arrive as PJSIP channels. Local/...;2 is the
                    # dialplan half used by the offline live check (live_check.py).
                    if name.startswith("PJSIP/"):
                        endpoint = name[len("PJSIP/"):].partition("-")[0]
                    elif name.startswith("Local/") and name.endswith(";2"):
                        endpoint = "local-check"
                    else:
                        continue
                    if not endpoint:
                        continue
                    print(f"selfmonitor: stasis start {name} ({endpoint})", flush=True)
                    self.sip_channels[channel_id] = endpoint
                    self._dispatch(self.stasis_handler, channel_id, endpoint)
                if (
                    event.get("type") in {"StasisEnd", "ChannelDestroyed"}
                    and channel_id in self.sip_channels
                ):
                    self.sip_channels.pop(channel_id, None)
                    print(f"selfmonitor: channel gone {channel_id}", flush=True)
                    self._dispatch(self.destroyed_handler, channel_id)
                if event.get("type") == "ChannelDestroyed" and channel_id in self.media:
                    self.media[channel_id].fail(ConnectionError("asterisk_channel_destroyed"))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"selfmonitor: events loop failed: {error!r}", flush=True)
            self.error = error
            for media in self.media.values():
                media.fail(error)

    def _dispatch(self, handler, *args):
        task = asyncio.create_task(handler(*args), name="selfmonitor-event")
        self.handler_tasks.add(task)
        task.add_done_callback(self.handler_tasks.discard)

    async def answer(self, channel_id):
        await self.request("POST", f"/channels/{channel_id}/answer")

    async def hangup_busy(self, channel_id):
        with suppress(Exception):
            await self.request("POST", f"/channels/{channel_id}/hangup",
                               params={"cause": "17"})

    async def hangup(self, channel_id):
        with suppress(Exception):
            await self.request("DELETE", "/channels/" + quote(channel_id, safe=""))

    async def create_bridge(self, bridge_id):
        await self.request("POST", "/bridges",
                           params={"bridgeId": bridge_id, "type": "mixing"})

    async def add_to_bridge(self, bridge_id, channel_id):
        await self.request(
            "POST",
            "/bridges/" + quote(bridge_id, safe="") + "/addChannel",
            params={"channel": channel_id},
        )

    async def delete_bridge(self, bridge_id):
        with suppress(Exception):
            await self.request("DELETE", "/bridges/" + quote(bridge_id, safe=""))

    async def snoop(self, target_channel_id, snoop_id):
        """Copy the caller's speech without joining any bridge."""
        await self.request(
            "POST",
            f"/channels/{target_channel_id}/snoop",
            params={
                "spy": self.spy_direction,
                "whisper": "none",
                "app": APP_NAME,
                "snoopId": snoop_id,
            },
        )
        return snoop_id

    async def open_media(self, name, *, receive):
        if self.closed or self.error:
            raise ConnectionError("selfmonitor_ari_unavailable")
        channel_id = f"{APP_NAME}-{name}-{uuid.uuid4().hex}"
        await self.request(
            "POST",
            "/channels/create",
            params={
                "endpoint": "WebSocket/INCOMING/c(slin48)n",
                "app": APP_NAME,
                "channelId": channel_id,
                "formats": "slin48",
            },
        )
        try:
            response = await self.request(
                "GET",
                f"/channels/{channel_id}/variable",
                params={"variable": "MEDIA_WEBSOCKET_CONNECTION_ID"},
            )
            connection_id = response.json().get("value")
            if not isinstance(connection_id, str) or not connection_id.strip():
                raise ValueError("missing_media_connection_id")

            ari_parts = urlsplit(self.url)
            media_url = urlunsplit(
                (
                    ari_parts.scheme,
                    ari_parts.netloc,
                    "/media/" + quote(connection_id, safe=""),
                    "",
                    "",
                )
            )
            socket = await self.connect(
                _websocket_url(media_url),
                subprotocols=["media"],
                max_size=65500,
                max_queue=4,
                open_timeout=self.timeout,
                close_timeout=2,
            )
            start = parse_control(await socket.recv())
            if (
                start.get("event") != "MEDIA_START"
                or start.get("connection_id") != connection_id
                or start.get("channel_id") != channel_id
                or start.get("format") != "slin48"
                or str(start.get("optimal_frame_size")) != str(FRAME_BYTES)
                or str(start.get("ptime")) != "20"
            ):
                raise ValueError("invalid_media_start")

            side = MediaSide(self, channel_id, receive)
            self.media[channel_id] = side
            self.media_sockets[channel_id] = socket
            side.reader = asyncio.create_task(side.read())
            await self.request("POST", f"/channels/{channel_id}/dial",
                               params={"timeout": 10})
            await socket.send("ANSWER")
        except BaseException:
            with suppress(Exception):
                await socket.close()
            with suppress(Exception):
                await self._delete_channel(channel_id)
            self.media.pop(channel_id, None)
            self.media_sockets.pop(channel_id, None)
            raise
        return side

    async def recv_media(self, side):
        return await self.media_sockets[side.channel_id].recv()

    async def send_media(self, side, frame):
        await self.media_sockets[side.channel_id].send(frame)

    async def close_media(self, channel_id):
        socket = self.media_sockets.pop(channel_id, None)
        side = self.media.pop(channel_id, None)
        if socket is not None:
            with suppress(Exception):
                await socket.close()
        if side is not None:
            side.fail(ConnectionError("asterisk_channel_closed"))
        with suppress(Exception):
            await self._delete_channel(channel_id)

    async def _delete_channel(self, channel_id):
        await self.request("DELETE", "/channels/" + quote(channel_id, safe=""))
