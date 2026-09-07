import asyncio
import itertools
import json

import pytest


class Channel:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def send_pcm(self, frame):
        self.sent.append(frame)

    async def receive_pcm(self):
        return await self.incoming.get()


class Room:
    def __init__(self):
        self.channels = {}
        self.closed = False
        self.close_started = asyncio.Event()
        self.close_gate = None

    async def __aenter__(self):
        return self

    async def open_channel(self, role):
        self.channels[role] = Channel()
        return self.channels[role]

    async def close(self):
        self.close_started.set()
        if self.close_gate:
            await self.close_gate.wait()
        self.closed = True


class Model:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.entered = asyncio.Event()
        self.start_gate = None
        self.error = None
        self.closed = False

    async def __aenter__(self):
        self.entered.set()
        if self.start_gate:
            await self.start_gate.wait()
        if self.error:
            raise self.error
        return self

    async def send(self, frame):
        self.sent.append(frame)

    async def outputs(self):
        while True:
            value = await self.incoming.get()
            if isinstance(value, Exception):
                raise value
            yield value

    async def close(self):
        self.closed = True


def setup_session(**kwargs):
    from conference.session import DemoSession
    rooms, models = [], []

    def room_factory():
        room = Room()
        rooms.append(room)
        return room

    def model_factory():
        model = Model()
        models.append(model)
        return model

    session = DemoSession(room_factory, model_factory,
                          sources=lambda role: itertools.repeat(role.encode() * 1920),
                          **kwargs)
    return session, rooms, models


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(.001)


def test_two_listeners_share_one_model_and_last_leave_waits_cleanup():
    async def check():
        session, rooms, models = setup_session()
        a, b = await asyncio.gather(session.join(), session.join())
        assert len(models) == len(rooms) == 1
        await session.leave(a)
        assert not session.closed and not models[0].closed
        rooms[0].close_gate = asyncio.Event()
        leaving = asyncio.create_task(session.leave(b))
        await rooms[0].close_started.wait()
        assert not leaving.done()
        with pytest.raises(ValueError, match="busy"):
            await session.join()
        rooms[0].close_gate.set()
        await leaving
        assert session.closed and rooms[0].closed and models[0].closed
    asyncio.run(check())


def test_fifth_listener_rejected_without_extra_resources():
    async def check():
        session, rooms, models = setup_session()
        listeners = await asyncio.gather(*(session.join() for _ in range(4)))
        with pytest.raises(ValueError, match="listener_limit"):
            await session.join()
        assert len(models) == len(rooms) == 1
        await asyncio.gather(*(session.leave(item) for item in listeners))
        assert session.closed
    asyncio.run(check())


def test_only_converted_c_reaches_asterisk_and_mix_fans_out():
    async def check():
        session, rooms, models = setup_session()
        a, b = await asyncio.gather(session.join(), session.join())
        await until(lambda: models[0].sent)
        assert models[0].sent[0] == b"C" * 1920
        assert rooms[0].channels["C"].sent == []
        models[0].incoming.put_nowait(b"V" * 96000)
        await until(lambda: rooms[0].channels["C"].sent)
        assert rooms[0].channels["C"].sent[0] == b"V" * 1920
        rooms[0].channels["listener"].incoming.put_nowait(b"M" * 1920)
        assert await asyncio.wait_for(a.queue.get(), 1) == b"M" * 1920
        assert await asyncio.wait_for(b.queue.get(), 1) == b"M" * 1920
        await session.close()
    asyncio.run(check())


def test_model_busy_and_cancelled_start_release_partial_room():
    async def check(cancel):
        from conference.session import DemoSession
        room, model = Room(), Model()
        model.start_gate = asyncio.Event()
        if not cancel:
            model.error = ValueError("busy")
        session = DemoSession(lambda: room, lambda: model)
        joining = asyncio.create_task(session.join())
        await model.entered.wait()
        if cancel:
            joining.cancel()
        else:
            model.start_gate.set()
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            await joining
        assert session.closed and room.closed and model.closed
    asyncio.run(check(False))
    asyncio.run(check(True))


def test_old_listener_cannot_close_replacement_and_cooldown_uses_clock():
    async def check():
        now = [100.0]
        session, rooms, models = setup_session(clock=lambda: now[0])
        old = await session.join()
        await session.leave(old)
        with pytest.raises(ValueError, match="cooldown"):
            await session.join()
        now[0] = 105.0
        fresh = await session.join()
        await session.leave(old)
        assert not session.closed and not models[1].closed
        await session.leave(fresh)
    asyncio.run(check())


@pytest.mark.parametrize("options,code", [({"ttl": .02}, "expired"),
                                          ({"output_timeout": .02}, "stalled")])
def test_expiry_and_stalled_output_cleanup(options, code):
    async def check():
        session, rooms, models = setup_session(**options)
        listener = await session.join()
        await asyncio.wait_for(listener.done.wait(), 1)
        await session.close()
        assert listener.terminal == {"type": "error", "code": code}
        assert session.closed and rooms[0].closed and models[0].closed
    asyncio.run(check())


def test_slow_listener_is_removed_at_201_frames_without_stopping_peer():
    async def check():
        session, rooms, models = setup_session()
        slow, fast = await asyncio.gather(session.join(), session.join())
        for _ in range(201):
            rooms[0].channels["listener"].incoming.put_nowait(b"M" * 1920)
            assert await asyncio.wait_for(fast.queue.get(), 1) == b"M" * 1920
        await asyncio.wait_for(slow.done.wait(), 1)
        assert slow.terminal == {"type": "error", "code": "slow_listener"}
        assert slow.queue.qsize() <= 200 and not session.closed
        await session.leave(fast)
        assert session.closed
    asyncio.run(check())


def test_invalid_converted_block_never_reaches_c_and_hides_upstream_error():
    async def check(value):
        session, rooms, models = setup_session()
        listener = await session.join()
        models[0].incoming.put_nowait(value)
        await asyncio.wait_for(listener.done.wait(), 1)
        await session.close()
        assert rooms[0].channels["C"].sent == []
        assert listener.terminal == {"type": "error", "code": "upstream_unavailable"}
    asyncio.run(check(b"raw"))
    asyncio.run(check(RuntimeError("password=secret")))


def test_cancelling_one_preparing_listener_preserves_the_other():
    async def check():
        from conference.session import DemoSession
        room, model = Room(), Model()
        model.start_gate = asyncio.Event()
        session = DemoSession(lambda: room, lambda: model,
                              sources=lambda role: itertools.repeat(bytes(1920)))
        first = asyncio.create_task(session.join())
        await model.entered.wait()
        second = asyncio.create_task(session.join())
        await until(lambda: len(session.run.listeners) == 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not room.closed and not model.closed
        model.start_gate.set()
        listener = await second
        await session.leave(listener)
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(check())


def test_cleanup_failure_blocks_replacement_resources():
    async def check():
        session, rooms, models = setup_session(cooldown=0)
        listener = await session.join()

        async def failed_close():
            raise RuntimeError("ARI credentials and private URL")

        rooms[0].close = failed_close
        await session.leave(listener)
        with pytest.raises(ValueError, match="busy"):
            await session.join()
        assert session.state == "unavailable" and models[0].closed
        assert len(models) == len(rooms) == 1
    asyncio.run(check())


def test_model_overload_cannot_accumulate_unbounded_converted_audio():
    async def check():
        session, rooms, models = setup_session()
        listener = await session.join()
        # 8 blocks * 50 frames/block (96000-byte block / 1920-byte frame) =
        # 400 frames into a maxsize=200 queue -- same 2x-over-capacity
        # margin the original 4*100-frame version had before BLOCK_BYTES
        # halved from a 2s to a 1s RVC hop.
        for _ in range(8):
            models[0].incoming.put_nowait(b"V" * 96000)
        await asyncio.wait_for(listener.done.wait(), 1)
        await session.close()
        assert listener.terminal == {"type": "error", "code": "overloaded"}
        assert listener.run.converted.qsize() <= 200
    asyncio.run(check())


def test_repeated_cancellation_of_last_leave_still_waits_for_owned_cleanup():
    async def check():
        session, rooms, models = setup_session()
        listener = await session.join()
        rooms[0].close_gate = asyncio.Event()
        leaving = asyncio.create_task(session.leave(listener))
        await rooms[0].close_started.wait()
        leaving.cancel()
        await asyncio.sleep(0)
        leaving.cancel()
        await asyncio.sleep(0)
        assert not leaving.done()
        rooms[0].close_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        assert session.closed and rooms[0].closed and models[0].closed
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(check())


def test_default_run_expires_on_600_second_timer(monkeypatch):
    async def check():
        original_sleep = asyncio.sleep
        expiry = asyncio.Event()
        armed = asyncio.Event()

        async def timer(delay):
            if delay == 600:
                armed.set()
                await expiry.wait()
            else:
                await original_sleep(delay)

        monkeypatch.setattr(asyncio, "sleep", timer)
        session, rooms, models = setup_session()
        listener = await session.join()
        await asyncio.wait_for(armed.wait(), 1)
        expiry.set()
        await asyncio.wait_for(listener.done.wait(), 1)
        await session.close()
        assert listener.terminal == {"type": "error", "code": "expired"}
        assert rooms[0].closed and models[0].closed
    asyncio.run(check())


@pytest.mark.parametrize("trigger", ["last_leave", "upstream_failure"])
def test_real_rvc_worker_close_failure_blocks_replacement(trigger):
    from conference.rvc import READY, RvcStream
    from conference.session import DemoSession
    from test_conference_adapters import Socket

    async def check():
        socket = Socket(json.dumps(READY))
        close_attempts = []

        async def failed_close():
            close_attempts.append(True)
            raise RuntimeError("private upstream transport close failure")

        socket.close = failed_close

        async def connect(*args, **kwargs):
            return socket

        room = Room()
        model = RvcStream("ws://fake-rvc/ws/rvc", connect=connect)
        session = DemoSession(lambda: room, lambda: model, cooldown=0,
                              sources=lambda role: itertools.repeat(bytes(1920)))
        listener = await session.join()
        await until(lambda: model.reading)
        if trigger == "upstream_failure":
            socket.incoming.put_nowait("invalid upstream control")
            await asyncio.wait_for(listener.done.wait(), 1)
        await session.leave(listener)
        assert session.state == "unavailable"
        with pytest.raises(ValueError, match="busy"):
            await session.join()
        # Idempotent later close must retain an unconfirmed transport failure.
        with pytest.raises(ConnectionError, match="rvc_close_failed"):
            await model.close()
        assert room.closed and len(close_attempts) == 1
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(check())


def test_default_preparation_expires_after_90_seconds_and_cleans(monkeypatch):
    from conference.session import DemoSession

    async def check():
        original_timeout = asyncio.timeout
        deadlines = []

        def capture_timeout(delay):
            timer = original_timeout(delay)
            if delay in (90, 120):
                deadlines.append((delay, timer))
            return timer

        monkeypatch.setattr(asyncio, "timeout", capture_timeout)
        room, model = Room(), Model()
        model.start_gate = asyncio.Event()
        session = DemoSession(lambda: room, lambda: model)
        joining = asyncio.create_task(session.join())
        await model.entered.wait()
        assert len(deadlines) == 1
        # Advance the real startup timeout without a 90-second wall-clock wait.
        deadlines[0][1].reschedule(asyncio.get_running_loop().time())
        with pytest.raises(ValueError, match="stalled"):
            await joining
        assert room.closed and model.closed and session.closed
        assert deadlines[0][0] == 90
    asyncio.run(check())
