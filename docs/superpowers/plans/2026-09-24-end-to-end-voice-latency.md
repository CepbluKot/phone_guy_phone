# End-to-End Voice Latency Implementation Plan

> **For agentic workers:** Work through the numbered tasks in order. Use one fresh, bounded task per phase and record the compact Execution State below after each phase. Do not treat historical model-call timings as current end-to-end latency.

**Goal:** Measure the browser and SIP voice paths, make the existing streaming RVC candidate reproducible, and obtain a safe, evidence-backed route to lower audible latency.

**Architecture:** Keep the chosen GPT v2 service and its `/ws/rvc-v2` clients working. Run the existing `infer/rtrvc.py` code as an isolated FCPE canary, compare it with GPT v2 on the same approved audio and real listening paths, then make a separate production-routing decision. Preserve the 20 ms PCM framing and fail-closed telephony behavior.

**Tech Stack:** Python, FastAPI, PyTorch/RVC, WebSocket, Web Audio, Asterisk `chan_websocket`, Caddy, systemd.

**Spec:** `docs/superpowers/specs/2026-09-08-target-phone-guy-architecture-design.md`. For the canary's detailed implementation steps, use `docs/superpowers/plans/2026-09-08-fcpe-canary-productization.md` after reconciling it with current code and VM state.

## Global Constraints

- The target voice is GPT v2, `/ws/rvc-v2`; FCPE with a 0.3 s block is a separate comparison canary. Changing the target engine requires updating the architecture spec after a listening decision.
- Keep access VPN-only. Do not use cloud inference or store live caller audio or transcripts.
- A model or transport failure must never pass the raw caller voice to listeners.
- One production RVC session at a time. Do not modify VM208/Frigate or allocate its GPU resources.
- Preserve existing dirty Git changes and unrelated worktrees. The files `AGENTS.md` and `docs/AI_EXECUTION_WORKFLOW.md` were untracked at planning time; do not stage or replace them casually.
- Before changing VM209, record a fresh read-only baseline and current GPU memory use. Historical results from 2026-09-07 are comparison data, not current proof.
- Prefer Terra Medium for implementation. Luna Low is suitable only for one small, fully specified file or command task at a time; do not give it the entire migration.

## File and Interface Map

| Path | Responsibility |
| --- | --- |
| `rvc_service/server.py`, `chunks.py`, `engine.py` | Production v1/v2 WebSocket contract, 20 ms PCM input, 1 s v2 output, offline RVC inference. |
| `rvc_service/rt_server.py`, `rt_chunks.py`, `rt_engine.py` | Existing research streaming engine and FCPE variant; currently separate from production. |
| `web/live/app.js`, `audio-worklet.js` | Browser capture, WebSocket, output hold, optional delay, displayed latency estimate. |
| `conference/rvc.py`, `media.py`, `selfmonitor/service.py` | SIP 1999's v2 client, 20 ms pacing, Asterisk injection. |
| `deploy/voice-rvc.service`, `Caddyfile`, `deploy-rvc.sh` | Production process, private routes, scoped rollout and rollback. |
| `docs/superpowers/plans/2026-09-08-fcpe-canary-productization.md` | Detailed isolated canary implementation and deployment plan. |

## Review Focus

1. A second session must receive `busy`; it must not create a second GPU workload or expose raw audio.
2. Disconnect, cancellation, overload, and model failure must release the GPU worker and stay fail-closed.
3. Every converted block must have contiguous sample indexes and exactly the declared output length.
4. A new session must reset pitch and stitch history so it cannot inherit the previous caller's voice.
5. Canary GPU use must leave enough memory and processing margin for production GPT v2 under concurrent demand.

---

### Task 1: Capture the live baseline and investigate the main site's 502

**Files:** Read `docs/OPERATIONS.md`, `docs/SELFMONITOR_2026-09-16.md`, `deploy/Caddyfile`, `deploy/voice-rvc.service`, `rvc_service/chunks.py`. Write a short dated result to `docs/LIVE_STATUS.md` only after confirming the current topology.

**Interface:** No code or protocol change.

- [x] Record `git status --short`, current branch and HEAD. Preserve all pre-existing changes.
- [x] On VM209, read the active `voice-rvc.service` and `voice-selfmonitor.service` states, `/opt/voice-rvc/current` target, RVC `/healthz`, GPU name/memory/use, Caddy route status, and the HTTP container's status and restart policy. Do not print secrets or full environment files.
- [x] Confirm that `voice.lan.awesomeio.ru/healthz` returns 502 while direct `/live/` returns 200 and RVC is ready. Inspect the container logs and Compose ownership. The 2026-09-11 deployment note in the canary worktree says the legacy HTTP container is intentionally stopped with restart policy `no`; a brief health diagnosis was followed by restoring that stopped state. Do not restart or enable it for the latency checks.
- [x] Record the active model, endpoint and sample contract: v2 is 48 kHz mono PCM16, 1920-byte input frames, 48,000 output samples per block; SIP and browser both depend on this.
- [x] Record **observed now**, **historical only**, and **unknown** values in `docs/LIVE_STATUS.md` on the existing `feature/fcpe-canary-productization` worktree. Microphone-to-ear and handset-to-ear latency remain unmeasured.

**Gate:** Stop this task if VM ownership, Caddy routing, or the stopped HTTP container differs from the repository. Return the exact mismatch and do not guess at a restart command.

### Task 2: Measure the existing v2 path before optimization

**Files:** Read `tests/live-rvc.py`, `web/live/app.js`, `web/live/audio-worklet.js`, `conference/rvc.py`, `selfmonitor/service.py`. Put methodology and results in `docs/LIVE_STATUS.md`; no production code change is required for the initial measurement.

**Interface:** Report separate numbers for block accumulation, `processingMs`, WebSocket/queue time, playback hold, and true audible end-to-end latency. Do not add these into a single number when their clocks or definitions overlap.

- [x] With an idle RVC worker, run a five-minute approved-sample private WSS stream against `/ws/rvc-v2`. `tests/live-rvc.py` hardcodes protocol v1, so use the v2-native `conference.rvc.RvcStream` adapter and the same VM-approved RU/EN synthetic inputs. Captured p50/p95 `processingMs`, output continuity, no overloads, and post-block send-to-output lag. Idle GPU was 1055 MiB before and after; per-run RAM/GPU high-water sampling was not captured. No caller recording was used.
- [ ] In the live browser, set the **additional delay** slider to zero. Note that `web/index.html` defaults to five seconds, while `/live/` defaults to zero. Record `AudioContext.baseLatency`, `outputLatency` when available, playback queue, hold and underruns; label the UI's current total as an estimate.
- [ ] Measure a real microphone-to-speaker loopback and a real 1999 handset-to-ear call if those devices are available. Record the method and repeat trials; if unavailable, mark both as unmeasured and continue with the WSS baseline.
- [x] Confirmed the legacy HTTP container is deliberately stopped; direct live route and audio WSS are separate. Do not restore it to run the comparison.

**Gate:** Do not claim a browser or SIP end-to-end speedup from model-call timing alone.

### Task 3: Make the streaming candidate reproducible without changing production

**Files:** Follow and update, where contradicted by current code, `docs/superpowers/plans/2026-09-08-fcpe-canary-productization.md`. Its implementation scope is `rvc_service/rt_server.py`, `rt_chunks.py`, `rt_engine.py`, canary-only web assets, and new canary-only deploy/unit files. Keep `rvc_service/server.py`, `conference/`, `selfmonitor/` and the production RVC unit untouched.

**Interface:** A private FCPE canary using the same Phone Guy model/index, 48 kHz mono PCM16 20 ms input, 0.3 s blocks, no `/tmp` runtime dependency, own health route, own loopback port and scoped rollback. Production remains `/ws/rvc-v2`.

- [x] Recheck free GPU memory and whether any canary process is already running. The service was installed but disabled/inactive; baseline GPU memory was about 1.0 GiB of 4 GiB. No second model was left running.
- [x] Reconcile the existing canary plan with its clean `feature/fcpe-canary-productization` worktree. Its FCPE-only service, isolated deploy/rollback and tests already exist; the canary was not redeployed for this comparison.
- [x] Temporarily start the installed canary, pass 30 private WSS blocks, and stop it. Production GPT v2 remained active and was not restarted; the canary returned to disabled/inactive.
- [x] The first live canary probe exposed a bad test signal. `source_frame()` was defined but unused; the probe resent a tiny ramp. Add a regression check and send the defined continuous sine. Verified the corrected probe locally against the live service: 30/30 non-silent blocks.
- [x] Capture FCPE p95 processing (140.353 ms), p95 RTF (0.468), send drift (1.276 ms), and exact sample continuity. These are protocol/performance measurements; a speech-quality comparison still requires listening.

**Gate:** If GPU memory or real-time margin is inadequate with production present, leave the canary stopped and report measurements. Never solve this by degrading or restarting production.

### Task 4: Compare complete browser and SIP paths and recommend a route

**Files:** Update `docs/LIVE_STATUS.md` and `docs/OPERATIONS.md` with current measurements and exact rollback steps. No production protocol change in this task.

**Interface:** A comparison table with the same input and conditions for GPT v2 and the canary: model-call p50/p95, block size, first audible output, steady-state microphone-to-ear or handset-to-ear latency, discontinuities, underruns, CPU/RAM/VRAM, and listener preference.

- [x] Compare GPT v2 and FCPE using the same approved 8-second synthetic Russian speech source. WSS p50/p95 processing was 564/588 ms for GPT v2 and 121/143 ms for FCPE. Rendered both to equal 8-second WAVs in `/home/oleg/.codex/visualizations/2026/09/24/01a0d278-518a-75d0-90cb-a94ffd615723/voice-ab/`.
- [x] Record current comparison results, limits, production recommendation, and isolated canary stop guidance in `docs/LIVE_STATUS.md` and `docs/OPERATIONS.md`.
- [ ] Obtain a human listening verdict on voice identity and intelligibility. The historical preference for GPT v2 is the current product decision until explicitly changed.
- [x] Recommend keeping GPT v2 as production and FCPE as a comparison canary until the listening decision changes. The WSS processing speedup is measured; physical microphone-to-speaker and handset latency remain unknown.
- [ ] If proposing a switch, first update the architecture spec and write a separate implementation brief for a new production protocol. That brief must cover browser version negotiation, `conference/rvc.py`'s fixed one-second block contract, selfmonitor's use of that client, Caddy routing, session isolation, busy behavior, fail-closed errors, and scoped rollback. Do not silently point the existing v2 clients at the canary: their control messages and metadata contracts differ.

**Gate:** Do not switch live SIP or browser clients based solely on lower GPU time. The current spec explicitly keeps GPT v2 as the production voice. A human listening verdict and physical device measurements were unavailable in this run, so no production switch is authorized by the evidence.

## Verification and reporting

- Static review: inspect the exact diff and `git diff --check`; do not include unrelated dirty files.
- Candidate: confirm private health, non-silent approved-sample output, contiguous sample indexes, no overload over a sustained run, GPU memory margin, and exact canary rollback.
- Production: confirm GPT v2 health and a real processed-audio path before and after canary work; distinguish a process being active from audio working.
- Report changed files, commands actually run, measured results, checks not run, and remaining product decisions. Do not report live browser or physical phone acceptance unless it was observed.

## Implementation Brief

Goal: Reduce audible browser and SIP latency using a measured, isolated streaming RVC candidate.

Non-goals: Go/Rust rewrite; new GPU; cloud inference; new voice model; automatic FCPE fallback; changing production GPT v2 before a new listening decision and spec update.

Accepted decisions and constraints: The 2026-09-08 architecture spec chooses GPT v2 for production and FCPE as canary. Keep VPN-only access, fail-closed SIP, one production AI speaker, no live audio retention, and VM208 untouched.

Steps: (1) Fresh baseline and site-availability diagnosis; (2) WSS/browser/SIP measurements with explicit unknowns; (3) implement existing FCPE canary plan in isolation; (4) compare quality and end-to-end latency; (5) only then prepare a separate production switch brief if the decision changes.

Verification: Use the checks in each task; record actual p50/p95, RTF, underruns, sample continuity, memory, physical listening and rollback evidence where available.

Risks and escalation conditions: Site topology mismatch, unclear reason for stopped HTTP container, GPU memory contention, differing WebSocket contracts, audio quality regression, overload, raw voice leakage, or insufficient rollback evidence. Stop the affected phase and return the compact state with evidence.

## Initial Execution State

Goal: Measure and safely shorten end-to-end Phone Guy latency.

Phase: implementation.

Accepted decisions: GPT v2 production; FCPE separate canary; no Go/Rust rewrite for latency.

Completed, with evidence: Five-minute v2 WSS baseline passed with 300 contiguous blocks; p50/p95 processing 571/593 ms and post-boundary output lag 728/768 ms. On equal 8-second synthetic speech, GPT v2 p50/p95 was 564/588 ms and FCPE 121/143 ms. 8-second A/B WAVs are ready in the visualization artifact directory. FCPE canary passed a corrected 30-block WSS probe, then was stopped to restore its disabled state. Canary tests passed (232 Python, 30 Node). Legacy HTTP container remains intentionally stopped; direct `/live/` and WSS routes are available.

Next concrete action: get the human listening comparison from the two A/B WAVs and, when equipment is available, browser acoustic-loopback and 1999 handset-to-ear measurements. Keep GPT v2 in production until then.

Affected paths: Start with documentation only. Production and canary paths are listed in the file map above.

Verification: `pytest -q` in canary worktree -> 232 passed, 2 dependency deprecation warnings; `node --test tests/*.test.cjs` -> 30 passed; 300-second v2 WSS probe -> 300/300 contiguous blocks; corrected FCPE WSS probe -> 30/30 non-silent blocks; equal-source GPT/FCPE WSS samples validated as 8-second mono 48 kHz PCM. Full mic-to-ear and handset-to-ear checks not run.

Blocker or decision needed: Human listening verdict and physical device measurements are still needed before recommending a production engine switch. No production route was changed.
