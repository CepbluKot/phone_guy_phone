# RVC streaming acceptance — 2026-09-06

Status: AI-only deployed and live technical gates passed. Current authoritative
release/results: [LIVE_STATUS.md](LIVE_STATUS.md). Sections below preserve the
chronological implementation evidence, including earlier pre-deployment states.

## Isolation and baseline

Source worktree: `/home/oleg/Documents/voice-changer/.worktrees/rvc-streaming`,
branch `feature/rvc-streaming`. Original main checkout/research retained.
Runtime VM 209, `192.168.20.70`, 4 GiB RAM, GTX 1050 Ti 4 GB. No VM resource
changes and no Frigate configuration changes made.

Baseline tests: 21 Python, 7 Node passed. Two pre-existing FastAPI/Starlette
TestClient deprecation warnings. Original browser DSP test on each private
domain: 444 sent / 444 received, 0 underruns, 0 drops, ~5.27 s estimated delay
with additional delay set to 5 s. The first main-domain AudioWorklet fetch
failed once; a retry succeeded without code/server changes. Do not claim this
transient observation was a reproduced and fixed product defect.

## Persistent engine smoke

Commits `ed9e2d8`, `7a57a63`: contextual framing and engine adapter. Ten initial
new behavioral tests; an eleventh tests cwd restoration after initialization.
Total 32 Python tests passed and 7 Node tests passed before service work.

First actual VM smoke identified upstream's relative locale file dependency;
the adapter now scopes upstream initialization to the correct directory and
restores cwd on success and failure. Scoped review approved the correction.

Second smoke under the `voice-rvc` account with read-only system, private tmp,
2700 MiB memory limit and CPU quota 250%:

- Warm-up: 30.847 s.
- Subsequent synthetic-tone window conversion: 0.657 s.
- Window: 124800 samples, 48 kHz, output finite; peak 0.0122.
- Cgroup peak memory: 1.2 GiB.
- Service process exited successfully; GPU released.

These are engine-window measurements, not speech-quality acceptance and not
microphone-to-speaker latency. The upstream pitch extractor prints a caught
empty-F0 interpolation traceback during all-zero warm-up; inference succeeds.
Weight-norm deprecation also comes from upstream. Both are tracked for review.

## Installation facts

### Real speech through contextual windows

Ran the existing 8-second RU/EN Piper references through `Chunker` and the
real warmed `Engine` on VM209, appending only the necessary 0.1-second
synthetic lookahead. Both outputs contain exactly 384000 samples at 48 kHz
(8 seconds), non-silent, with no clipped peaks. Russian hop processing:
0.642/0.690/0.674/0.695 s. English: 0.651/0.674/0.679/0.683 s, each for a
2-second emitted hop. Peaks .839/.783 respectively. Files retained on VM
`/opt/voice-rvc/smoke-output/` and laptop
`experiments/phoneguy/outputs/streaming-core/` for listening. This is an array
pipeline check, not a paced WebSocket or browser test. Resemblance/seam
quality is not certified by these numeric checks alone.

Separate live Python environment installed at `/opt/voice-rvc/venv` from the
experiment's resolved model dependency versions plus FastAPI 0.141.1,
Starlette 1.6.0, uvicorn 0.34.2, websockets 15.0.1. `pip check` passed.
Dedicated non-login `voice-rvc` account, video/render supplemental groups.
Verified model, HuBERT, RMVPE and venv are readable by that account.
The existing venv is owned by `ubuntu:ubuntu` with mode775; rollout therefore
verifies exact pins read-only and fails closed on mismatch. It does not chown,
write a marker or install into the shared existing environment.

## Remaining acceptance gates

### Native WebSocket lifecycle

Service implementation `19d8307` and cancellation fix `4ba8ab9` reviewed.
59 Python tests pass, including 27 service lifecycle tests. Two targeted
review reproductions caught repeated-cancellation and Stop/backpressure races;
both now have passing regressions and scoped re-review approval.

Native systemd unit syntax verified on VM and started (loopback only, no Caddy
route/UI change yet). Actual GPU WebSocket tests passed before and after fix:
first response 96000 samples, processing 686.6 / 752.6 ms, speech RMS .119/.121.
Second-client busy, immediate Stop, reconnect and malformed-frame error passed.
Health returned ready/active=false/running=false/queuedWindows=0 afterward.
Listener verified as 127.0.0.1:8090 only. Actual account and restrictions match
unit: voice-rvc, strict read-only system, 2700 MiB memory cap, CPU quota 250%.
Post-test memory current ~1.48 GB; GPU resident 1047 MiB; zero automatic restarts.

### Five-minute paced server test (loopback only)

Sent synthetic Piper speech at 20 ms frame pace through the real native
WebSocket/GPU service for 300 seconds, plus 0.1-second lookahead. Received all
150 ordered output hops (300 seconds, 14400000 samples), finite/non-silent.
Processing p95 709.0 ms per2-second output hop. Arrival lag relative to each
input hop end ranged .765–.815 s; first15 mean .78120 s, final15 mean .78476 s
(+3.56 ms, no accumulating backlog). During load queue0, workeractive,
memory current1.485GB/peak1.504GB, GPU1581MiB during inference, NRestarts0.
This measures the server at capture pace, NOT the private-network browser
playback path; that still needs separate validation.

Browser profile/burst buffering tests and scoped review completed at f7f1579.
Latest rerun: 59 Python passed (two known dependency deprecation warnings),
19 Node passed. Real transferable-buffer and connecting-timeout regressions
are covered.

Task4 rollout tooling is now implemented but has not yet been run against the
VM: scoped `deploy/deploy-rvc.sh`, exact native Caddy `/ws/rvc` route and
`tests/live-rvc.py`. Focused tests exercise exact20ms pacing/sample continuity,
runtime-stat parsing, scoped staging and automatic rollback on a failed health
gate. Final local verification after rollback hardening:73 Python passed with the same two dependency
warnings;19 Node passed. This is source evidence only. New UI/Caddy route remain
undeployed until the controller runs and records the gates below.

After this tooling was prepared, the user removed the selectable legacy DSP
profile from the desired final UI. The bounded frontend cleanup now leaves only
Phone Guy, Start/Stop, input/output levels and additional delay. This is source
evidence only and is not a claim of deployment or physical listening. The
underlying HTTP/static service and Caddy catch-all remain for transport and
rollback; they are not a required UI fallback.

Pre-deployment numeric seam inspection of the existing8s core outputs found no
clipping. Boundary jumps were RU .00018/.00140/.00113 versus step p99 .1044;
EN .01273/.00180/.01337 versus step p99 .0878. This does not certify subjective
seam quality or voice resemblance.

At this earlier checkpoint still pending were: actual scoped deployment, private-WSS5min result, real browser
UI, recovery after the deployed worker restart, exercised rollback and physical
user listening. Subsequent actual results are recorded in LIVE_STATUS.md;
subjective listening and physical acoustic latency remain distinct from technical tests.

Continuation entry point: [HANDOFF.md](HANDOFF.md). It includes source/runtime
separation, protocol, exact diagnostic commands, remaining gates and safety
constraints. Main checkout also has START_HERE.md pointing into this worktree.
