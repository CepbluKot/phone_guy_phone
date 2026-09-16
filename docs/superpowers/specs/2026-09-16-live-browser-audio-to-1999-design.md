# Browser `/live/` audio mirrored to SIP 1999

## Goal

A caller who dials `1999` hears the **same processed audio currently rendered by
the open `/live/` page**, including its additional-delay setting. The caller's
microphone is not an input to this feature. The browser continues to play the
audio locally while the call listens. There is one GPT v2 RVC inference session,
owned by `/live/`; mirroring must never create another one.

If a caller dials before `/live/` starts, the call stays connected and hears
silence. When `/live/` starts, processed audio appears in that existing call.
When `/live/` stops, reloads, or loses its network connection, the call returns
to silence and remains connected. Hanging up does not stop the browser session.

## Existing behavior and scope

Today `/live/` sends browser microphone PCM to `/ws/rvc-v2`, receives GPT v2
output, and renders it through `web/live/audio-worklet.js`. The worklet applies
the selected 0–10 second delay. The current `voice-selfmonitor.service` instead
snoops the SIP caller's microphone and starts its own RVC session. That
`1999` behavior is replaced, not kept as a fallback.

This change is limited to the private Phone Guy VM, the `/live/` page, the
`1999` service, and their deployment routes. It does not alter `1983`, `1987`,
`2014`, the conference, Frigate, or the RVC model implementation. Existing
`/call/` behavior and the persistent Yealink contact must survive deployment.

## Chosen architecture

```text
browser mic -> existing /ws/rvc-v2 -> /live/ AudioWorklet -> browser speakers
                                             |
                                             +-> 20 ms rendered PCM frames
                                                 -> private WSS publisher
                                                 -> voice-selfmonitor.service
                                                 -> chan_websocket injection
                                                 -> 1999 caller
```

The mirror point is **after** the worklet's jitter hold and additional delay.
It emits 48 kHz mono signed 16-bit PCM in 960-sample (20 ms) frames that are
copies of the samples assigned to the browser output. The browser sends these
frames over a dedicated WSS endpoint; it does not send raw microphone frames
to the mirror. The relay validates frame sizes and accepts only one current
publisher. A second publisher is rejected without disrupting the first.

The relay is private and enforces the expected HTTPS `Origin` on WebSocket
upgrade. The endpoint is exposed only through the existing VPN-bound Caddy
vhost; the relay's own listener remains on loopback. No credentials or audio
are written to logs or recordings.

`1999` answers into an ARI mixing bridge containing only the SIP caller and a
chan_websocket injection channel. There is no snoop or listener channel and
no `RvcStream` in this path. A 20 ms pacer sends the freshest mirrored frame,
or a zero PCM frame when there is none. The pacer continues silence before
the page starts and through publisher outages. The queue is small and bounded;
late frames are discarded instead of replayed. The browser's own playback
must continue if the mirror WebSocket fails, and the page attempts a bounded
reconnect while its `/live/` session remains active.

The phone may lag the browser by network/telephony buffering; sample-perfect
device synchronization is not promised. The content and selected browser
delay are mirrored. SIP may reduce the audible bandwidth to its configured
codec, but may not inject raw caller audio into the caller's playback.

## Alternatives considered

1. **Chosen: mirror rendered worklet output.** Preserves what the browser
   actually plays, including the delay slider, without another model session.
   Adds one local WSS relay and a small 20 ms output copy.
2. **Fan out model output at `/ws/rvc-v2`.** Avoids browser upload but bypasses
   the browser's jitter hold and delay, so the phone would not hear precisely
   what the page plays.
3. **Keep the existing SIP self-monitor.** Requires a second model session and
   listens to the caller's microphone; it does not meet the request.

## Failure and lifecycle rules

- No publisher, publisher stops, or publisher disconnects: existing 1999 call
  remains connected and gets silence. New 1999 calls also connect to silence.
- A new publisher takes ownership only after the previous publisher has fully
  disconnected. Old/late publisher frames cannot reappear after ownership
  changes.
- SIP caller hangs up: its bridge/injection are released; `/live/` continues.
- Relay or Asterisk send backlog: discard stale frames and resume from the
  current frame, never play delayed speech. If the injection fails, cleanly
  release that SIP call; do not route raw audio as a fallback.
- Model unavailable: `/live/` retains its existing error behavior; 1999 stays
  connected with silence because the publisher stops providing audio.
- At most one 1999 listener is supported initially, matching the current
  service's one-call limit. A second caller gets a busy response.

## Delivery and verification

Implement from an isolated branch based on the current `main`, without
resetting other worktrees. Update the worklet/page, the isolated
`voice-selfmonitor.service`, its tests, private Caddy route, deployment script,
and operator documentation. Keep the 1999 route durable across conference
deployment, and preserve existing `/call/` and phone-registration fixes.

Tests must prove: rendered samples are the mirrored samples after delay;
silence before start and after stop; audio appears in a call already in
progress; no raw caller audio path or RVC session in the 1999 service; bounded
backlogs and ownership cleanup; browser playback survives relay failure;
second publisher/caller behavior. Live acceptance must verify the private
page and WSS route, 1999 dialplan, service health, a synthetic mirror stream
heard through a SIP test call, silence-to-audio-to-silence without redial,
and zero leftover channels after hangup. Do not claim the user's microphone
or speaker quality was measured without an explicit user-side listening test.
