# Live Browser Audio to 1999 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dialing 1999 plays the rendered `/live/` audio, or silence while the page is not broadcasting, without using a second RVC session.

**Architecture:** The `/live/` AudioWorklet copies its final post-delay output into 20 ms PCM16 frames. A private WebSocket publisher sends those frames to the existing selfmonitor service; that service feeds one Asterisk injection channel and substitutes silence when no fresh frame exists.

**Tech Stack:** JavaScript AudioWorklet + browser WebSocket, Python 3.12 asyncio/websockets, Asterisk ARI/chan_websocket, Caddy, systemd, Node test runner and pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-live-browser-audio-to-1999-design.md`

## Global Constraints

- The mirror source is the final browser playback sample, after jitter hold and 0–10 s additional delay.
- Never feed raw SIP caller audio or start a second RVC session on 1999.
- The 1999 call remains connected and hears silence before `/live/` starts and after it stops.
- The private relay listens only on VM209 loopback; Caddy exposes it only on the VPN-bound vhost.
- A mirror outage does not stop browser playback; a SIP outage cannot make stale audio replay.
- Preserve the deployed `/call/` route, `1983` persistent contact, and other Asterisk dialplan routes; do not deploy stale main-branch conference files over them.

---

### Task 1: Browser output mirror

**Files:** Modify `web/live/audio-worklet.js`, `web/live/app.js`, `tests/live-worklet.test.cjs`, and `tests/live-client.test.cjs`.

**Interfaces:** The worklet posts `{type: 'mirror', pcm: ArrayBuffer}` with 960 little-endian signed 16-bit samples. The page sends that buffer to `wss://vm-voice-1.lan.awesomeio.ru/ws/live-mirror` only while its mirror socket is open and its buffered bytes remain below four frames.

- [ ] Add a failing worklet test that configures a 1 s delay, plays nonzero PCM, and compares each 960-sample `mirror` frame with the final `output` array for that process call. Also verify the initial frame is silence and that there are no partial frames.
- [ ] Run `node --test tests/live-worklet.test.cjs`; confirm the new test fails because no `mirror` messages exist.
- [ ] Add a 960-sample PCM16 accumulator to `PhoneAudio`; after each output sample is finalized, quantize it and post one transferable frame every 960 samples. Do not change capture or playback behavior.
- [ ] Add a failing page test with two fake sockets: the RVC socket still receives capture; a `mirror` message reaches only the relay socket, and a relay error/close does not end the RVC session.
- [ ] Run `node --test tests/live-client.test.cjs`; confirm the new test fails because there is no relay socket.
- [ ] Open the relay socket on `/live/` start, close it on Stop/pagehide, drop frames when `bufferedAmount + 1920 > 7680`, and retry a closed relay at bounded intervals while the RVC session remains active. Reconnection must not create another RVC socket.
- [ ] Run both Node tests and commit the browser change.

### Task 2: Private relay and SIP listener

**Files:** Modify `selfmonitor/service.py`, `selfmonitor/ari.py` only if needed, `tests/test_selfmonitor.py`, and replace the obsolete `selfmonitor/live_check.py` with a mirror-path check.

**Interfaces:** A relay object owns one publisher and at most one queued/current 1920-byte frame. `take_frame()` returns that frame or `b'\0' * 1920`. Its `publish` path validates exact frame length and publisher identity. `MirrorSession` attaches one injection channel with the caller to a mixing bridge and sends `take_frame()` at 20 ms intervals; it never opens a model, snoop, or source listener.

- [ ] Replace the old echo behavior tests with failing tests for: silence before publisher, nonzero mirror audio in an already active call, silence after publisher disconnect, stale frame replacement, duplicate publisher rejection, second SIP caller busy, and cleanup without raw passthrough.
- [ ] Run `python -m pytest -q tests/test_selfmonitor.py`; confirm the new tests fail against `EchoService`.
- [ ] Implement the relay and mirror-only SIP session in `selfmonitor/service.py`. Keep ARI creation/cleanup owned and release the bridge/channel on failure or hangup. Limit WebSocket frame size to 1920 bytes and enforce `Origin: https://vm-voice-1.lan.awesomeio.ru`.
- [ ] Serve the relay WebSocket on loopback port 8096 alongside `/healthz`; keep one publisher and one SIP listener. On no publisher, emit zero PCM continuously at 20 ms pacing.
- [ ] Run the focused pytest suite; commit the relay and SIP change.

### Task 3: Durable private deployment and acceptance

**Files:** Modify `deploy/Caddyfile`, `deploy/deploy-selfmonitor.sh`, `deploy/voice-selfmonitor.service`, `docs/SELFMONITOR_2026-09-16.md`, deployment tests, and a live mirror check.

**Interfaces:** Caddy routes only `/ws/live-mirror` to `127.0.0.1:8096`; `/live/`, `/call/`, `/ws/rvc-v2`, and `/ws/conference` remain unchanged. The deploy script updates only the selfmonitor app/service, its private route, and 1999 dialplan entry, with backups and rollback.

- [ ] Add failing deployment tests that verify a private `/ws/live-mirror` route, a durable 1999 dialplan route, no Asterisk/RVC restart, and preservation of the active `/call/` route and phone contact.
- [ ] Run those tests; confirm they fail on the current deployment files.
- [ ] Update the source Caddy template and deployment script; validate the candidate Caddyfile before reload. Do not replace the active runtime Caddyfile with the stale main-branch template. Back up changed live files and use only a scoped selfmonitor restart/dialplan reload.
- [ ] Rewrite the live check to publish deterministic nonzero PCM, dial 1999 through a synthetic SIP/Local caller, assert silence → mirrored audio → silence without redial, then assert no leftover ARI channels or bridge.
- [ ] Run all Python and Node tests, `git diff --check`, and the live check. Verify service, dialplan, RVC readiness, Caddy route, Yealink contact, and `/call/` afterwards.
- [ ] Update the operator document to state that 1999 mirrors `/live/` rather than the caller's voice, including silence and recovery behavior. Commit and push the feature branch.
