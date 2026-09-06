"""One shared, bounded listening run with explicit ownership of every task."""

import asyncio
from dataclasses import dataclass, field
import time

from .media import BLOCK_BYTES, FRAME_BYTES, Pacer, finish_cleanup, split_pcm
from .scenario import source_frames


class SessionError(ValueError):
    pass


def public_error(error):
    if isinstance(error, SessionError):
        return str(error)
    if isinstance(error, TimeoutError):
        return "stalled"
    if isinstance(error, ValueError) and str(error) in {
        "busy", "model_unavailable", "overloaded", "stalled",
    }:
        return str(error)
    return "upstream_unavailable"


@dataclass(eq=False)
class Listener:
    run: object
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    done: asyncio.Event = field(default_factory=asyncio.Event)
    terminal: dict | None = None

    def finish(self, code=None):
        if self.terminal is None:
            self.terminal = ({"type": "error", "code": code} if code
                             else {"type": "stopped"})
            while not self.queue.empty():
                self.queue.get_nowait()
            self.done.set()


@dataclass(eq=False)
class _Run:
    listeners: set = field(default_factory=set)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list = field(default_factory=list)
    converted: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    channels: dict = field(default_factory=dict)
    owner: asyncio.Task | None = None
    room: object = None
    model: object = None
    cleaning: bool = False
    error: str | None = None


class DemoSession:
    def __init__(self, room_factory, model_factory, *, sources=source_frames,
                 clock=time.monotonic, cooldown=5, ttl=600, output_timeout=10,
                 startup_timeout=90, cleanup_timeout=30):
        self.room_factory = room_factory
        self.model_factory = model_factory
        self.sources = sources
        self.clock = clock
        self.cooldown = cooldown
        self.ttl = ttl
        self.output_timeout = output_timeout
        self.startup_timeout = startup_timeout
        self.cleanup_timeout = cleanup_timeout
        self.lock = asyncio.Lock()
        self.run = None
        self.last_start = float("-inf")

    @property
    def closed(self):
        return self.run is None

    @property
    def state(self):
        if self.run is None:
            return "idle"
        if self.run.error == "cleanup_failed":
            return "unavailable"
        if self.run.cleaning:
            return "cleaning"
        return "active" if self.run.ready.is_set() else "preparing"

    async def join(self):
        async with self.lock:
            run = self.run
            if run is not None and run.cleaning:
                raise SessionError("busy")
            if run is None:
                now = self.clock()
                if now - self.last_start < self.cooldown:
                    raise SessionError("cooldown")
                run = self.run = _Run()
                self.last_start = now
                run.owner = asyncio.create_task(self._own(run), name="conference-owner")
            if len(run.listeners) >= 4:
                raise SessionError("listener_limit")
            listener = Listener(run)
            run.listeners.add(listener)
        try:
            await run.ready.wait()
            if listener.terminal is not None:
                await asyncio.shield(run.owner)
                raise SessionError(listener.terminal.get("code", "stopped"))
            return listener
        except BaseException:
            await finish_cleanup(self.leave(listener))
            raise

    async def leave(self, listener):
        run = listener.run
        async with self.lock:
            listener.finish()
            run.listeners.discard(listener)
            if not run.listeners:
                run.cleaning = True
                run.stop.set()
            owner = run.owner if run.cleaning else None
        if owner is not None:
            await finish_cleanup(self._wait_owner(owner))

    async def close(self):
        async with self.lock:
            run = self.run
            if run is None:
                return
            run.cleaning = True
            run.stop.set()
        await finish_cleanup(self._wait_owner(run.owner))

    async def _wait_owner(self, owner):
        await asyncio.shield(owner)

    def _task(self, run, coroutine, name):
        task = asyncio.create_task(coroutine, name="conference-" + name)
        run.tasks.append(task)
        return task

    async def _startup(self, run):
        async with asyncio.timeout(self.startup_timeout):
            run.room = self.room_factory()
            await run.room.__aenter__()
            run.model = self.model_factory()
            await run.model.__aenter__()
            for role in ("A", "B", "C", "listener"):
                run.channels[role] = await run.room.open_channel(role)

    async def _own(self, run):
        try:
            startup = self._task(run, self._startup(run), "startup")
            stop = self._task(run, run.stop.wait(), "stop")
            expiry = self._task(run, asyncio.sleep(self.ttl), "expiry")
            done, _ = await asyncio.wait((startup, stop, expiry),
                                         return_when=asyncio.FIRST_COMPLETED)
            if expiry in done:
                raise SessionError("expired")
            if stop in done:
                return
            startup.result()
            for role in ("A", "B"):
                self._task(run, self._source(run, role), "sender-" + role)
            self._task(run, self._source(run, "C"), "model-input")
            self._task(run, self._outputs(run), "model-receiver")
            self._task(run, self._converted(run), "sender-C")
            self._task(run, self._fanout(run), "mix-fanout")
            # Asterisk MediaChannel.reader owns continuous return drains for A/B/C.
            run.ready.set()
            done, _ = await asyncio.wait([t for t in run.tasks if t is not startup],
                                         return_when=asyncio.FIRST_COMPLETED)
            if expiry in done:
                raise SessionError("expired")
            if stop not in done:
                for task in done:
                    task.result()
                raise SessionError("upstream_unavailable")
        except Exception as error:
            run.error = public_error(error)
        finally:
            run.cleaning = True
            for listener in tuple(run.listeners):
                listener.finish(run.error)
            run.ready.set()
            await finish_cleanup(self._cleanup(run))

    async def _cleanup(self, run):
        for task in run.tasks:
            task.cancel()
        await asyncio.gather(*run.tasks, return_exceptions=True)
        for resource in (run.model, run.room):
            if resource is not None:
                try:
                    async with asyncio.timeout(self.cleanup_timeout):
                        await resource.close()
                except Exception:
                    # Attempt both resources; upstream details never reach clients.
                    run.error = "cleanup_failed"
        async with self.lock:
            if self.run is run and run.error != "cleanup_failed":
                self.run = None

    async def _source(self, run, role):
        pacer = Pacer()
        for frame in self.sources(role):
            await pacer.wait()
            async with asyncio.timeout(self.output_timeout):
                if role == "C":
                    await run.model.send(frame)
                else:
                    await run.channels[role].send_pcm(frame)

    async def _outputs(self, run):
        output = run.model.outputs()
        try:
            while True:
                async with asyncio.timeout(self.output_timeout):
                    block = await anext(output)
                if not isinstance(block, bytes) or len(block) != BLOCK_BYTES:
                    raise ValueError("invalid_converted_block")
                for frame in split_pcm(block):
                    try:
                        run.converted.put_nowait(frame)
                    except asyncio.QueueFull:
                        raise SessionError("overloaded") from None
        finally:
            await output.aclose()

    async def _converted(self, run):
        pacer = Pacer()
        while True:
            async with asyncio.timeout(self.output_timeout):
                frame = await run.converted.get()
                await pacer.wait()
                await run.channels["C"].send_pcm(frame)

    async def _fanout(self, run):
        while True:
            async with asyncio.timeout(self.output_timeout):
                frame = await run.channels["listener"].receive_pcm()
            if not isinstance(frame, bytes) or len(frame) != FRAME_BYTES:
                raise ValueError("invalid_mix_frame")
            async with self.lock:
                for listener in tuple(run.listeners):
                    try:
                        listener.queue.put_nowait(frame)
                    except asyncio.QueueFull:
                        listener.finish("slow_listener")
                        run.listeners.discard(listener)
                if not run.listeners:
                    run.cleaning = True
                    run.stop.set()
