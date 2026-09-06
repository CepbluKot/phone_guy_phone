# Phone Guy Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax.

**Goal:** Use the approved PhoneGuyfnaf1V1 model for continuous browser microphone conversion on VM 209.
**Architecture:** Persistent native GPU service behind VM Caddy, existing DSP container preserved. Session-local contextual chunk assembly and bounded browser playback; no microphone files.
**Tech Stack:** Python 3.12, FastAPI/WebSocket, NumPy/SciPy, pinned RVC/PyTorch CUDA 11.8, Web AudioWorklet, systemd/Caddy.
**Spec:** `docs/superpowers/specs/2026-09-06-rvc-streaming-design.md`

## Global constraints

- VM 209 only; GTX 1050 Ti FP32. No Frigate changes.
- Input 48000 Hz mono PCM16, 960 samples/frame; 2-second inference hop.
- Context 0.5 seconds; look-ahead 0.1 seconds; output exact 96000 samples/hop.
- One GPU inference in flight, one waiting chunk; overload fails explicitly.
- Default additional playback delay 5 seconds, adjustable 0–10 seconds.
- No microphone recordings/logs, no cloud calls, allowed HTTPS origins only.
- No main-branch implementation without explicit consent; preserve the existing uncommitted research.

## Task 1: Contextual conversion and persistent model

Files: create `rvc_service/chunks.py`, `rvc_service/engine.py`, `rvc_service/__init__.py`; test `tests/test_rvc_chunks.py`.
Interface: `Chunker.push(frame: bytes) -> np.ndarray | None` returns one window; `Chunker.render(converted: np.ndarray) -> bytes` crops/aligned-crossfades at 48 kHz. `Engine.convert(window: np.ndarray) -> np.ndarray` returns normalized 48 kHz float audio, using in-memory upstream pipeline, not its file CLI.

- [ ] Write failing tests for exact output duration, prior context, reset isolation, malformed frames and finite output. Identity conversion must preserve a ramp at chunk boundaries:
  ```python
  assert len(output) == 192000
  assert np.isfinite(np.frombuffer(output, dtype='<i2')).all()
  ```
- [ ] Run `.venv/bin/pytest tests/test_rvc_chunks.py -q` and confirm missing behavior.
- [ ] Implement fixed-window assembly, 40 ms tail overlap with bounded waveform alignment, exact crop/pad validation and PCM clipping. Input window retains sufficient look-ahead to support alignment without shrinking output.
- [ ] Implement `Engine` using the pinned upstream `VC.pipeline.pipeline` directly, loading HuBERT/model once, calling on arrays; safe tensor loading forced. Warm the same window shape as production before readiness.
- [ ] Run new tests plus existing Python tests, commit only task files.

## Task 2: Bounded WebSocket service

Files: create `rvc_service/server.py`, `tests/test_rvc_service.py`, `deploy/voice-rvc.service`, `rvc_service/requirements.txt`.
Interface: `create_app(engine_factory)` exposes `/healthz` and `/ws/rvc`; factory initialization/warm-up runs during lifespan. Conversion runs in a single-worker executor.

- [ ] Write failing route tests with a fake array converter: valid start/ready, actual converted PCM, bad origin/start/frame, second-client busy and recovery after Stop. Add a blocking converter fixture to verify disconnect cannot admit a new session before the GPU call ends.
- [ ] Run `.venv/bin/pytest tests/test_rvc_service.py -q` and confirm failures.
- [ ] Implement independent receiver/consumer tasks; queue capacity 1, single inference worker. Metadata has monotonically increasing `outputStart`, `outputSamples`, `consumedSamples`, `processingMs`; emit metadata immediately before matching PCM.
- [ ] On any failure, cancel receiver/consumer, discard pending output, await in-flight executor future before releasing global session ownership. Validate 1920-byte inputs and finite arrays. Deadlines: 90 seconds startup, 10 seconds stalled session.
- [ ] Add native unit: dedicated non-root account, loopback 8090, MemoryMax=2700M, MemorySwapMax=0, CPUQuota=250%, read-only source/models, private tmpfs scratch, safe/offline environment. No automatic model download at runtime.
- [ ] Run new lifecycle tests and Python regressions, commit only task files.

## Task 3: Browser profile and burst playback

Files: modify `web/index.html`, `web/app.js`, `web/audio-worklet.js`, `tests/client.test.cjs`, `tests/audio-worklet.test.cjs`.
Interface: worklet accepts `configure` with mode `rvc`/`dsp` before playback; RVC packets may contain 96000 samples. Capture remains 960 samples. Existing DSP behavior remains covered.

- [ ] Add failing tests: a two-second PCM packet reaches output without truncation; subsequent packets preserve count; bounded overflow reports an error; RVC start selects `/ws/rvc` and version 1; multiple capture packets can precede a reply; Stop and stale replies cannot resurrect playback.
- [ ] Run `node --test tests/*.test.cjs` and confirm new failures.
- [ ] Implement bounded RVC queue and chunk-sized prebuffer. Retain DSP queue behavior. Track acknowledgements by sample positions instead of 1:1 send/receive packets; reject malformed metadata and binary lengths.
- [ ] Default profile to Phone Guy AI; show DSP sliders only in fallback profile, disable profile changes while running. Line-test button explicitly uses DSP. Show warming/ready/busy/overload errors and distinguish extra delay from approximate total.
- [ ] Run all Node and Python tests and commit task files.

## Task 4: Isolated deployment and live acceptance

Files: modify `deploy/Caddyfile`; create `deploy/deploy-rvc.sh`, `tests/live-rvc.py`; update `docs/OPERATIONS.md` and add `docs/RVC_ACCEPTANCE_2026-09-06.md`.

- [ ] Add a paced WebSocket test using approved synthetic samples, not a user's microphone. Use a monotonic clock to send 960-sample frames every 20 ms, record output sample continuity/timing/finite PCM and queue statistics. Validate malformed frames and reconnect separately.
- [ ] Create recoverable release staging and backup of existing image, UI/source and Caddy config only (do not duplicate the multi-GB experiment venv). Reuse exact pinned model assets read-only and install a separate live venv from resolved requirements.
- [ ] Start/warm the new service and test loopback before changing UI defaults. Validate Caddy config before reload; route `/ws/rvc` only to loopback service, retain DSP reverse proxy.
- [ ] Build/deploy UI only after unit checks, verify both private HTTPS health endpoints and new GPU route. On failure restore prior image/UI/Caddy config and retain logs with no audio content.
- [ ] Run five-minute paced synthetic test; record actual durations, RTF, gaps, bounded memory and no cumulative lag. Compare seam regions and output speech samples. Test worker restart and reconnection.
- [ ] Inspect UI in browser: profiles, microphone request/error handling, Stop, delay adjustment and test playback. Report separately what requires physical microphone/user listening.
- [ ] Record acceptance evidence, rollback instructions, final limitations and git status; commit scoped implementation changes only.

## Review

All spec areas map to tasks above. Cold-start timing is distinct from steady-state audio timeout. DSP protocol is unchanged. Model inference never runs on laptop. Physical listening and exact end-to-end latency cannot be inferred from synthetic throughput.
