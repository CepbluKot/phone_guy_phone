# Asterisk Listening Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three virtual participants in a real Asterisk conference, one converted live by RVC, with a microphone-free browser listener.

**Architecture:** ConfBridge owns mixing. A server-side controller owns four WebSocket media channels, sends two raw synthetic sources and one RVC-only source, and forwards the fourth channel's conference mix to browsers. Existing RVC remains a separate, unchanged single-session service.

**Tech Stack:** Asterisk 22.11.0, Python asyncio/FastAPI/httpx/websockets, browser AudioWorklet, native Caddy, isolated Docker Compose deployment.

**Spec:** ../specs/2026-09-06-asterisk-listening-demo-design.md (user approved 2026-09-06).

## Global Constraints

- Execution only VM209 ubuntu@192.168.20.70; code/docs only laptop repository.
- Do not change Frigate/VM208, VM sizes, passthrough, DNS/VPS/firewall.
- Existing RVC: 127.0.0.1:8090; one session, no second model, no venv mutation.
- New runtime combined budget: 512 MiB memory, one CPU. Check actual load.
- Browser receive-only, no getUserMedia, no user recording, no raw C fallback.
- Maximum four listeners, four seconds queued PCM per listener, ten-minute run.
- Model preparation timeout 90 seconds; active media progress timeout 10 seconds.
- Last listener disconnect stops demo and releases all owned channels and RVC.
- Inference and Asterisk compilation never on laptop.
- Existing worktree: /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming.
- Do not use old deploy/deploy.sh or bulk-copy experiment assets.

## File ownership and interfaces

`conference/asterisk/`: Dockerfile, modules.conf, http.conf, ari.conf.template,
asterisk.conf, extensions.conf, confbridge.conf. Only Asterisk configuration.
`conference/media.py`: PCM splitting and bounded fanout, independent of network.
`conference/asterisk.py`: ARI channel creation, WebSocket media, owned-channel cleanup.
`conference/scenario.py`: fixed synthetic fixture timeline and 20 ms pacing.
`conference/rvc.py`: existing RVC protocol adapter, no model imports.
`conference/session.py`: one shared run, cancellation, resource ownership.
`conference/server.py`: listen-only public WebSocket, health, origin/limits.
`conference/Dockerfile`, `conference/requirements.lock`: independent controller image.
`web/conference/`: index.html, app.js, audio-worklet.js, style.css.
`deploy/deploy-conference.sh`, `deploy/compose.conference.yaml`: scoped delivery/rollback.
`tests/test_conference_*.py`, `tests/conference*.test.cjs`: isolated tests.
`tests/live-conference.py`: server/browser-wire integration and acceptance metrics.
`docs/CONFERENCE.md`: operations and next-agent entry point.

## Task 1: Reproducible Asterisk conference endpoint

**Files:** conference/asterisk/*, tests/test_conference_config.py.
**Consumes:** official source archive and local-only ARI credentials.
**Produces:** HTTP/ARI on 127.0.0.1:8092; incoming WebSocket media channels in
ConfBridge room `phoneguy-demo` via extension `demo` in context `phoneguy`.

- [ ] Write failing static safety tests before configuration:

```python
from pathlib import Path

def test_conference_configuration_is_private_and_wideband():
    root = Path('conference/asterisk')
    assert 'internal_sample_rate=48000' in (root/'confbridge.conf').read_text()
    assert 'autoload=no' in (root/'modules.conf').read_text()
    assert 'chan_pjsip' not in (root/'modules.conf').read_text()
    assert 'ConfBridge(phoneguy-demo' in (root/'extensions.conf').read_text()
```

- [ ] Run `.venv/bin/pytest tests/test_conference_config.py -q`; verify missing-file failure.
- [ ] Pin archive `https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-22.11.0.tar.gz`
  SHA256 `3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94`.
  Download/check in Docker build; compile `make -j1`, no optional sounds download.
  Pin base image digest and explicitly list build/runtime dependencies in Dockerfile.
- [ ] Start from explicit module allowlist: chan_websocket, res_http_websocket,
  res_ari and channel/asterisk ARI resources, pbx_config, app_confbridge,
  bridge_softmix, res_timing_timerfd plus dependency modules reported by this release.
  Fail startup health if required modules cannot load; never enable all SIP modules.
- [ ] Implement dialplan and profile:

```ini
[phoneguy]
exten => demo,1,Answer()
 same => n,ConfBridge(phoneguy-demo,phoneguy_bridge,phoneguy_user)
 same => n,Hangup()

; confbridge.conf
[phoneguy_bridge]
type=bridge
internal_sample_rate=48000
mixing_interval=20
max_members=4
[phoneguy_user]
type=user
quiet=yes
```

- [ ] Render ARI secret at deployment into mode-0600 ignored file; use read-only
  config mount and non-root Asterisk. Disable CDR/CEL/audio recording.
- [ ] Run static tests; commit only source/config/tests, no secret or image.
- [ ] Server build and module verification are deferred to Task 5, not simulated
  as successful here. Source module dependencies must be checked before that build.

## Task 2: Media protocol adapters and synthetic sources

**Files:** conference/{__init__,media,asterisk,rvc,scenario}.py,
tests/test_conference_media.py, tests/test_conference_adapters.py.
**Interfaces:** `split_pcm(block: bytes) -> list[bytes]` returns 1920-byte frames,
rejecting non-aligned input. `RvcStream` async context manager: `send(frame)` and
`outputs()` async iterator of validated 192000-byte blocks. `AsteriskRoom` async
context manager owns `open_channel(role)` returning object with async `send_pcm`,
`receive_pcm`, `close`. `source_frames(role)` yields 1920-byte synthetic frames.

- [ ] Write and run failing protocol tests:

```python
import pytest
from conference.media import split_pcm

def test_rvc_block_is_split_without_changing_audio():
    block = bytes(192000)
    frames = split_pcm(block)
    assert len(frames) == 100
    assert all(len(f) == 1920 for f in frames)
    assert b''.join(frames) == block

def test_partial_frame_rejected():
    with pytest.raises(ValueError):
        split_pcm(bytes(1921))
```

- [ ] Implement validation and splitting:

```python
def split_pcm(block: bytes) -> list[bytes]:
    if not block or len(block) % 1920:
        raise ValueError('invalid_pcm')
    return [block[i:i+1920] for i in range(0, len(block), 1920)]
```

- [ ] Adapter tests use fake HTTP/WS transports: non-2xx ARI, missing connection
  ID, MEDIA_START format != slin48, frame size != 1920, XOFF/XON, disconnect,
  RVC busy, malformed ready/metrics, non-contiguous outputStart, wrong block size.
- [ ] For each role generate a unique owned channel ID prefix `phoneguy-demo-`.
  ARI create with `WebSocket/INCOMING/c(slin48)n`, fetch
  MEDIA_WEBSOCKET_CONNECTION_ID via channel variable API, connect `/media/<id>`,
  then dial into context phoneguy/extension demo. Verify actual create/dial API
  arguments against 22.11 documentation/source before coding. Cleanup on every
  partial failure deletes only IDs created by this room instance.
- [ ] Treat chan_websocket control frames as text and media as binary. For
  slin48 send at most 1920 bytes/message, obey XOFF, reject unbounded backlog.
  Drain/discard A/B/C return media; never echo it. Read fourth channel only.
- [ ] RVC start uses exact existing version-1 handshake and allowed native Origin.
  Connect once per room, preserve 20 ms monotonic pacing and existing metrics
  semantics. Cancellation sends Stop and closes connection; never route input
  directly to Asterisk if conversion fails.
- [ ] Generate three labelled synthetic speech fixtures on server using a local
  speech synthesizer, mono PCM16 48k, store outside Git. Define a fixed 30-second
  looping timeline: A at 0s, B at 7s, C input at 14s; A/B overlap at 24s. Clip each
  synthetic phrase to its slot with trailing silence; no random timing.
- [ ] Run all adapter tests, commit. Fixtures and model weights remain untracked.

## Task 3: Shared room lifecycle and receive-only endpoint

**Files:** conference/{session,server}.py, conference/Dockerfile,
conference/requirements.lock, tests/test_conference_session.py,
tests/test_conference_server.py.
**Interfaces:** `DemoSession.join() -> Listener`, `leave(listener)`, `close()`;
Listener owns a bounded queue and terminal status. `create_app(session_factory)`
allows deterministic tests. GET `/healthz`; WS `/ws/conference`.

- [ ] Write failing concurrent-join test with fake room/model factories:

```python
async def test_two_listeners_share_one_model(session, model_factory):
    a, b = await asyncio.gather(session.join(), session.join())
    assert model_factory.call_count == 1
    await session.leave(a)
    assert not session.closed
    await session.leave(b)
    assert session.closed
```

- [ ] Add failures: fifth listener rejected, unexpected Origin rejected before
  join, model busy cleans partial room, cancellation during startup, old task
  cannot close replacement session, last leave waits owned cleanup, hard600s
  expiry, 10s stalled output and slow-listener queue >200 frames.
- [ ] Implement one lock-serialized shared session; use separate task ownership
  for startup, three senders, return drains, model receiver, mix fanout and expiry.
  Every terminal path invokes idempotent room/model cleanup. No detached tasks.
- [ ] WebSocket client must first send `{"type":"listen","version":1}`.
  Server statuses: `preparing`, `ready` with sampleRate48000/channels1/
  sampleFormat s16le, `error` with stable code, `stopped`. After ready binary
  frames are exactly 1920 bytes. Only incoming stop control is permitted;
  binary input is rejected. Limit control to1024 bytes and 4 listeners.
- [ ] Add monotonic start cooldown5s and run TTL600s; reject new start during
  cleanup as busy. Health distinguishes unavailable Asterisk from idle/active;
  never expose credentials or raw upstream exceptions.
- [ ] Independent image pins only controller dependencies, no torch/numpy/model
  installation. Run controller8091 loopback with memory256MiB/CPU0.5; Asterisk
  receives memory256MiB/CPU0.5. Include `/healthz` Docker health check.
- [ ] Run session/server tests, full Python suite, commit.

## Task 4: Browser-only listening page

**Files:** web/conference/{index.html,app.js,audio-worklet.js,style.css},
web/index.html, app/main.py, tests/conference.test.cjs,
tests/conference-worklet.test.cjs, tests/test_health.py.
**Consumes:** Task3 WebSocket protocol at native WSS host.
**Produces:** `/conference/` page with Listen/Disconnect, room state and roles.

- [ ] Write failing Node test that supplies getUserMedia as a throwing stub and
  exercises Listen/Disconnect; assert zero microphone calls and socket cleanup.
- [ ] Add worklet tests: PCM16 conversion, 48k input resampled for device rate,
  bounded4s queue, initial250ms reserve, underflow silence, stop clears output,
  stale ready/binary after Stop ignored, resume AudioContext via user click.
- [ ] Implement isolated listener worklet; never import legacy DSP worklet.
  Meter/resampling reads buffers before transfer. Use explicit
  `wss://vm-voice-1.lan.awesomeio.ru/ws/conference` and visible error states.
- [ ] Add FastAPI route serving conference index and microphone-denying policy
  for that page without changing existing main-page microphone permission:

```python
@app.get('/conference/')
def conference_index():
    return FileResponse(web / 'conference' / 'index.html',
                        headers={'Permissions-Policy': 'microphone=()'})
```

- [ ] Adjust middleware so it does not overwrite the route's microphone policy;
  test both `/` and `/conference/`. Serve assets via existing `/static` mount.
  Add link only to existing main page, no existing RVC control changes.
- [ ] Run `node --test tests/*.test.cjs` and `.venv/bin/pytest -q`, commit.

## Task 5: Scoped delivery, live proof and handoff

**Files:** deploy/deploy-conference.sh, deploy/compose.conference.yaml,
deploy/Caddyfile, tests/test_deploy_conference.py, tests/live-conference.py,
docs/CONFERENCE.md, docs/HANDOFF.md, docs/LIVE_STATUS.md.
**Consumes:** Tasks1–4; VM209 current health and exact existing image/config state.
**Produces:** tested private URL and documented rollback evidence.

- [ ] Write static deployment tests requiring exact VM target, no firewall/DNS/
  Frigate/venv mutations, source-only manifest, strict error exit and rollback.
  Test rollback failure is nonzero, not a success message.
- [ ] Implement preflight (idle RVC, available memory, free ports8091/8092),
  timestamped stage/backups under `/opt/voice-conference`, pinned build checks,
  local-only credential generation, limited source upload and isolated Compose.
  Use `make -j1`; measure build memory and stop if available memory unsafe.
- [ ] Snapshot exact current HTTP image/config and native Caddy before switching.
  Existing deploy-rvc.sh does not deliver changed app/main.py: build new HTTP
  image from full tracked app+web rather than relying on that script's web-only
  staging. Preserve legacy app behavior with full tests.
- [ ] Add only native `/ws/conference` ->127.0.0.1:8091 Caddy route. Validate
  candidate before reload. Bind ARI8092 only loopback and keep media URL private.
- [ ] Build/start services on VM, inspect version/module status, validate actual
  media sample format. Correct implementation against live protocol before UI
  switch; do not declare configuration tests an integration result.
- [ ] Live probe connects as browser listener, collects only synthetic output,
  checks four ConfBridge channels, metrics progress, timestamps, finite/nonzero
  PCM, resource samples and no growing queues over300s. Include role isolation
  runs; mute C's converted output to prove no raw C leaks into mix.
- [ ] Exercise last-listener cleanup, reconnect, forced test-channel hangup,
  wrong Origin and RVC busy while preserving unrelated sessions. After each
  owned test, assert room absent and model ready/idle/queue0.
- [ ] Execute scoped rollback once, check old HTTP/RVC health, then redeploy
  candidate and repeat short media smoke. Retain recoverable backup.
- [ ] Real browser: open page, click Listen, inspect no mic prompt/error,
  output progress and Stop. Leave no background test running after verification.
- [ ] Write exact deployment/version, acceptance numbers, limitations and
  commands in docs/CONFERENCE.md; link HANDOFF/LIVE_STATUS. Do not mark planned
  tests passed. Invite user to open `/conference/` and click Listen only after
  live verification succeeds. Finish branch review and commit source/docs.

## Plan self-review

Coverage: placement/security ->1/3/5; raw isolation/media ->2/3/5; browser ->4/5;
timeouts/limits ->3/4; rollback/docs ->5. All six component interfaces are named
above. Public protocol is receive-only. Existing single-RVC contention remains
explicit. Deployment must include app/main.py, not just static assets.
Actual implementation and runtime acceptance are still pending.
