"""Owned ARI channels and chan_websocket media for Asterisk 22.11."""

import asyncio
import base64
import json
from contextlib import suppress
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
import uuid

import httpx
from websockets.asyncio.client import connect as websocket_connect

from .media import FRAME_BYTES, finish_cleanup


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


def _media_control(message):
    if not isinstance(message, str):
        raise ValueError("invalid_asterisk_control")
    if message.startswith("{"):
        try:
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


class MediaChannel:
    def __init__(self, room, channel_id, role, socket):
        self.room = room
        self.channel_id = channel_id
        self.role = role
        self.socket = socket
        self.received = asyncio.Queue(maxsize=room.receive_queue_frames)
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
                message = await self.socket.recv()
                if isinstance(message, bytes):
                    if len(message) != FRAME_BYTES:
                        raise ValueError("invalid_asterisk_frame")
                    if self.role == "listener":
                        try:
                            self.received.put_nowait(message)
                        except asyncio.QueueFull:
                            raise ValueError("asterisk_receive_backlog") from None
                    continue

                event = _media_control(message).get("event")
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
            with suppress(Exception):
                await self.socket.close()

    async def send_pcm(self, frame):
        if self.error:
            raise self.error
        if (
            self.closed
            or self.role == "listener"
            or not isinstance(frame, bytes)
            or len(frame) != FRAME_BYTES
        ):
            raise ValueError("invalid_asterisk_frame_or_state")
        if self.sending:
            raise ValueError("asterisk_send_backlog")

        self.sending = True
        try:
            async with asyncio.timeout(self.room.timeout):
                await self.can_send.wait()
                if self.error:
                    raise self.error
                await self.socket.send(frame)
        except BaseException as error:
            self.fail(error)
            with suppress(Exception):
                await self.socket.close()
            raise
        finally:
            self.sending = False

    async def receive_pcm(self):
        if self.role != "listener" or self.closed:
            raise ValueError("invalid_asterisk_receive_role_or_state")
        if self.error:
            raise self.error
        message = await self.received.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.fail(ConnectionError("asterisk_channel_closed"))
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        try:
            await self.socket.close()
        finally:
            await self.room.delete(self.channel_id)


class AsteriskRoom:
    def __init__(
        self,
        ari_url,
        username,
        password,
        *,
        http=None,
        connect=websocket_connect,
        timeout=10,
        receive_queue_frames=100,
        app_name="phoneguy-demo",
        stasis_handler=None,
        destroyed_handler=None,
    ):
        if receive_queue_frames < 1:
            raise ValueError("invalid_receive_queue_size")
        self.url = ari_url.rstrip("/")
        self.auth = (username, password)
        self.http = http
        self.own_http = http is None
        self.connect = connect
        self.timeout = timeout
        self.receive_queue_frames = receive_queue_frames
        if not isinstance(app_name, str) or not app_name:
            raise ValueError("invalid_asterisk_app")
        self.app = app_name if app_name == "phoneguy-sip" else app_name + "-" + uuid.uuid4().hex
        self.stasis_handler = stasis_handler
        self.destroyed_handler = destroyed_handler
        self.owned = set()
        self.channels = {}
        self.up = {}
        self.events = None
        self.events_task = None
        self.handler_tasks = set()
        self.error = None
        self.closed = False

    async def __aenter__(self):
        if self.events is not None or self.closed:
            raise ValueError("asterisk_room_already_used")
        if self.http is None:
            self.http = httpx.AsyncClient(
                base_url=self.url + "/",
                auth=self.auth,
                timeout=self.timeout,
            )
        token = base64.b64encode(":".join(self.auth).encode()).decode()
        events_url = self.url + "/events?" + urlencode({"app": self.app})
        try:
            self.events = await self.connect(
                _websocket_url(events_url),
                additional_headers={"Authorization": "Basic " + token},
                max_size=65536,
                max_queue=16,
                open_timeout=self.timeout,
                close_timeout=2,
            )
            self.events_task = asyncio.create_task(self.read_events())
            return self
        except BaseException:
            await finish_cleanup(self.close())
            raise

    async def __aexit__(self, *exc):
        await finish_cleanup(self.close())

    async def request(self, method, path, **kwargs):
        request_path = path.lstrip("/") if self.own_http else path
        response = await self.http.request(method, request_path, **kwargs)
        response.raise_for_status()
        return response

    async def read_events(self):
        try:
            while True:
                event = json.loads(await self.events.recv())
                if not isinstance(event, dict):
                    raise ValueError("invalid_ari_event")
                channel = event.get("channel")
                if not isinstance(channel, dict):
                    channel = {}
                channel_id = channel.get("id")
                if channel.get("state") == "Up" and channel_id in self.up:
                    self.up[channel_id].set()
                if event.get("type") == "StasisStart":
                    self._dispatch_stasis(event, channel)
                if (
                    event.get("type") == "ChannelDestroyed"
                    and channel_id in self.channels
                ):
                    self.channels[channel_id].fail(
                        ConnectionError("asterisk_channel_destroyed")
                    )
                if event.get("type") == "ChannelDestroyed" and self.destroyed_handler:
                    self._dispatch(self.destroyed_handler, channel_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = error
            for ready in self.up.values():
                ready.set()
            for channel in self.channels.values():
                channel.fail(error)

    def _dispatch_stasis(self, event, channel):
        if event.get("application") != self.app or self.stasis_handler is None:
            return
        channel_id = channel.get("id")
        name = channel.get("name")
        if not isinstance(channel_id, str) or not isinstance(name, str):
            return
        prefix = "PJSIP/"
        if not name.startswith(prefix):
            return
        endpoint = name[len(prefix):].partition("-")[0]
        if not endpoint:
            return
        self._dispatch(self.stasis_handler, channel_id, endpoint)

    def _dispatch(self, handler, *args):
        if not args or not isinstance(args[0], str):
            return
        task = asyncio.create_task(handler(*args), name="asterisk-event-handler")
        self.handler_tasks.add(task)
        task.add_done_callback(self.handler_tasks.discard)

    async def open_channel(self, role):
        if role not in {"A", "B", "C", "listener"}:
            raise ValueError("invalid_asterisk_role")
        return await self._open_media(role, receive=role == "listener", continue_to_demo=True)

    async def open_media(self, name, *, receive=False):
        """Create a controller-owned chan_websocket channel.

        Unlike browser-demo channels, this media endpoint remains under ARI
        control and is placed by ``SipSessionManager`` in an explicit bridge.
        It must never fall through to the demo ConfBridge dialplan.
        """
        if not isinstance(name, str) or not name or len(name) > 96:
            raise ValueError("invalid_asterisk_media_name")
        return await self._open_media(name, receive=receive, continue_to_demo=False)

    async def _open_media(self, name, *, receive, continue_to_demo):
        if self.closed or self.events is None or self.error:
            raise ConnectionError("asterisk_room_unavailable")

        channel_id = f"{self.app}-{name}-{uuid.uuid4().hex}"
        path = f"/channels/{channel_id}"
        self.owned.add(channel_id)
        self.up[channel_id] = asyncio.Event()
        channel = None
        media_socket = None
        try:
            try:
                await self.request(
                    "POST",
                    "/channels/create",
                    params={
                        "endpoint": "WebSocket/INCOMING/c(slin48)n",
                        "app": self.app,
                        "channelId": channel_id,
                        "formats": "slin48",
                    },
                )
            except httpx.HTTPStatusError as error:
                if 400 <= error.response.status_code < 500:
                    self.owned.discard(channel_id)
                raise

            response = await self.request(
                "GET",
                path + "/variable",
                params={"variable": "MEDIA_WEBSOCKET_CONNECTION_ID"},
            )
            connection_id = response.json().get("value")
            if (
                not isinstance(connection_id, str)
                or not connection_id.strip()
                or len(connection_id) > 128
            ):
                raise ValueError("missing_media_connection_id")

            ari = urlsplit(self.url)
            media_url = urlunsplit(
                (
                    ari.scheme,
                    ari.netloc,
                    "/media/" + quote(connection_id, safe=""),
                    "",
                    "",
                )
            )
            media_socket = await self.connect(
                _websocket_url(media_url),
                subprotocols=["media"],
                max_size=65500,
                max_queue=4,
                open_timeout=self.timeout,
                close_timeout=2,
            )
            async with asyncio.timeout(self.timeout):
                start = _media_control(await media_socket.recv())
            if (
                start.get("event") != "MEDIA_START"
                or start.get("connection_id") != connection_id
                or start.get("channel_id") != channel_id
                or start.get("format") != "slin48"
                or str(start.get("optimal_frame_size")) != str(FRAME_BYTES)
                or str(start.get("ptime")) != "20"
            ):
                raise ValueError("invalid_media_start")

            channel = MediaChannel(
                self, channel_id, "listener" if receive else "injection", media_socket
            )
            self.channels[channel_id] = channel
            channel.reader = asyncio.create_task(channel.read())
            await self.request("POST", path + "/dial", params={"timeout": 10})
            await asyncio.wait_for(media_socket.send("ANSWER"), self.timeout)
            await asyncio.wait_for(self.up[channel_id].wait(), self.timeout)
            if self.error:
                raise self.error
            if channel.error:
                raise channel.error
            if continue_to_demo:
                await self.request(
                    "POST",
                    path + "/continue",
                    params={"context": "phoneguy", "extension": "demo", "priority": 1},
                )
            return channel
        except BaseException:
            async def cleanup():
                if channel is not None:
                    await channel.close()
                    return
                try:
                    if media_socket is not None:
                        await media_socket.close()
                finally:
                    await self.delete(channel_id)

            await finish_cleanup(cleanup())
            raise

    async def create_bridge(self, bridge_id):
        if not isinstance(bridge_id, str) or not bridge_id:
            raise ValueError("invalid_asterisk_bridge")
        await self.request(
            "POST", "/bridges", params={"bridgeId": bridge_id, "type": "mixing"}
        )
        return bridge_id

    async def answer_channel(self, channel_id):
        if not isinstance(channel_id, str) or not channel_id:
            raise ValueError("invalid_asterisk_channel")
        await self.request(
            "POST", "/channels/" + quote(channel_id, safe="") + "/answer"
        )

    async def hangup_channel(self, channel_id):
        if not isinstance(channel_id, str) or not channel_id:
            return
        try:
            await self.request("DELETE", "/channels/" + quote(channel_id, safe=""))
        except httpx.HTTPStatusError as error:
            # ChannelDestroyed can race a rejection or cleanup.
            if error.response.status_code != 404:
                raise

    async def add_to_bridge(self, bridge_id, channel_id):
        if not isinstance(bridge_id, str) or not isinstance(channel_id, str):
            raise ValueError("invalid_asterisk_bridge_member")
        await self.request(
            "POST",
            "/bridges/" + quote(bridge_id, safe="") + "/addChannel",
            params={"channel": channel_id},
        )

    async def delete_bridge(self, bridge_id):
        if not isinstance(bridge_id, str) or not bridge_id:
            return
        try:
            await self.request("DELETE", "/bridges/" + quote(bridge_id, safe=""))
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise

    async def delete(self, channel_id):
        if channel_id not in self.owned:
            return
        try:
            await self.request("DELETE", f"/channels/{channel_id}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
        self.owned.discard(channel_id)
        self.channels.pop(channel_id, None)
        self.up.pop(channel_id, None)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        errors = []
        for channel in list(self.channels.values()):
            try:
                await channel.close()
            except Exception as error:
                errors.append(error)
        for channel_id in list(self.owned):
            try:
                await self.delete(channel_id)
            except Exception as error:
                errors.append(error)
        if self.events_task is not None:
            self.events_task.cancel()
            await asyncio.gather(self.events_task, return_exceptions=True)
        for task in list(self.handler_tasks):
            task.cancel()
        await asyncio.gather(*self.handler_tasks, return_exceptions=True)
        try:
            if self.events is not None:
                await self.events.close()
        finally:
            if self.own_http and self.http is not None:
                await self.http.aclose()
        if errors:
            raise ExceptionGroup("asterisk_cleanup_failed", errors)
