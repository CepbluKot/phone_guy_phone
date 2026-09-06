# Phone Guy RVC: continuous microphone integration

Status: approved; AI-only amendment implemented and deployed on 2026-09-06.
Technical live gates passed; subjective user listening remains separate.
Current results: ../../LIVE_STATUS.md.
Date: 2026-09-06.

## Approved amendment: AI-only interface

On 2026-09-06 the user requested removal of the old telephone effect and all
its controls, then explicitly approved: keep only Phone Guy, Start, Stop,
input/output indicators and adjustable additional delay; remove profile
selection, DSP sliders and the old tone test. Preserve the old deployed image
only as an emergency rollback. This amendment supersedes all requirements
below for an exposed DSP fallback, profile selector or tone-test button.
The existing HTTP/static hosting process may remain for this scoped rollout;
do not couple UI cleanup to a backend/infrastructure rewrite. RVC protocol,
model preset, buffer limits, privacy and VM isolation remain unchanged.

## Approved intent and evidence

The user approved the sound of PhoneGuyfnaf1V1 and requested continuous
microphone conversion with the existing browser UI. Keep code/documentation
on the laptop, execution on VM 209, VPN-only access and Frigate unchanged.
The prior isolated test measured approximately 1.35 seconds per 8 seconds
of audio after warming, FP32 on GTX 1050 Ti; this is throughput evidence,
not evidence of streaming latency or seamless chunk boundaries.

## Approach

Recommended: retain the existing UI and DSP service; add a dedicated,
persistent RVC worker/service on the same dedicated VM. This reuses the
verified model, PyTorch and upstream commit without adding GPU dependencies
to the small production container.

Alternatives considered: replace the UI with a third-party voice changer
(more client changes and unverified memory requirements); convert only
after sentence-end detection (simpler, but not continuous speech).
Neither is part of this implementation.

## Audio pipeline

Capture remains 48 kHz mono PCM16, 20 ms per WebSocket input frame.
The RVC service accumulates 2 seconds of new audio per inference, with
0.5 seconds of previous context and 0.1 seconds of look-ahead. The model
is initialized and warmed once before it is declared ready. Each session
has independent context, resampler and output state.

Use the verified model with speaker 0, pitch 0, RMVPE, index rate 0.6,
protect 0.33, RMS mix 1, FP32. No extra telephone filter by default.
Convert output from native 32 kHz to 48 kHz. Maintain output sample counts
with integer sample positions: removing context and overlapping seams must
not progressively shorten the stream. Use short waveform-aligned overlap
and crossfade at boundaries; validate repeated words and sustained vowels.
If this method audibly degrades the approved voice, do not call it accepted.

Browser playback uses a bounded sample queue suitable for burst arrivals,
not the existing 400 ms queue. Initial buffering covers a full output
chunk. Capture, receive and playback remain asynchronous. There is at most
one inference running and one complete waiting chunk; overload ends the
session with a useful message rather than silently increasing delay.

The existing 0–10 second control remains **additional playback delay**,
default 5 seconds. Capture accumulation, inference and initial buffering
add their own latency. Do not label this as exactly 5 seconds end-to-end.
Display additional delay separately from approximate total latency.
Changing extra delay clears delayed output; Stop immediately stops capture,
playback and queued session audio. A running GPU call may finish, but its
result must be discarded and its buffers released before admitting a new
RVC session. No previous session's voice can reach a new session.

## Service and protocol

Native systemd service runs as an unprivileged dedicated account, using a
separate pinned environment and read-only code/model paths. GPU access is
limited to VM 209. Writable scratch space is tmpfs; microphone audio and
transcripts are never persisted or logged. The offline experiment remains
reproducible and separate from live code.

Service binds loopback port 8090. Native VM Caddy routes `/ws/rvc` to it;
existing `/ws/audio` remains the DSP route. Existing allowed HTTPS origins
are enforced. No new public DNS or external listener. No cloud inference.

New route negotiates protocol version 1 in `start`, accepts fixed 1920-byte
input frames, and returns ordered PCM plus metrics identifying consumed
input sample counts and output sample ranges. The client must not assume
one input packet per output packet. Enforce input/output size limits and
validate sample format/rate before accepting audio. `ready`, `busy`,
`overloaded`, `model_unavailable`, `error` and disconnect are explicit states.

One active RVC session is allowed. Startup/warm-up has a distinct readiness
state and deadline (90 seconds); it is not covered by the old six-second
audio timeout. Once ready, ten seconds without useful progress terminates
the session. Disconnect/Stop cancels pending input, not the global model.
Expose readiness and bounded timing/queue metrics without audio contents.

Start with a 2700 MiB service memory limit, no swap, and CPU quota 250%,
matching the successful isolated probe. Verify headroom under sustained
load; do not increase VM resources or alter Frigate as an implicit fix.

## UI

Default profile: `Phone Guy — AI`. Alternative: `Телефон — простой эффект`.
Keep Start, Stop, delay control and levels. DSP sliders are only visible
for the DSP profile. RVC parameters are fixed, not user-facing tuning knobs.
Prevent profile changes during a session. Show connecting, warming, ready,
busy and actionable failure states in Russian. Recommend headphones.

The existing tone-based line test remains a DSP connectivity test and must
be labeled as such; a sine tone is not a quality test for a speech model.
No silent fallback to DSP if RVC fails: preserve the user's chosen voice.

## Verification and deployment gate

1. Regression tests for existing DSP, delay and microphone lifecycle pass.
2. New tests cover chunk assembly, exact sample counts, bounded queues,
   malformed messages, second-session busy state, cancellation, stale
   output rejection and session cleanup. Model-free tests use a fake worker.
3. Real VM test passes Russian/English reference speech through the actual
   WebSocket route at capture pace, including silence and final partial
   chunks. Stop deliberately discards unfinished speech, as existing Stop
   semantics do; this is documented in the UI.
4. Five-minute paced run: bounded memory/queues, no accumulating lag,
   finite non-silent output for speech, no disconnects or dropped audio
   under the measured LAN/VPN conditions. Record results, not just a verdict.
5. Inspect overlap regions and provide a recorded synthetic comparison for
   listening; do not record the user's microphone without a separate request.
6. Browser checks: profile selection, permission errors, playback, Stop,
   restart and delay changes. Clearly distinguish synthetic browser evidence
   from physical microphone acceptance, which requires the user's listening.
7. Verify private HTTPS, readiness, GPU memory and service recovery after
   restarting the new worker. Confirm existing DSP service remains healthy.
8. Deploy only this project's UI, new service and scoped Caddy route. Preserve
   a rollback to the existing image/UI and Caddy configuration. If live
   checks fail, restore DSP as the default and report the failed RVC gate.

No <150 ms requirement, training, virtual OS microphone, external voice
service, Frigate changes or public exposure is included.
