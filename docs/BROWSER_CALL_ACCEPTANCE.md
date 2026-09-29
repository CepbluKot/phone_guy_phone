# Browser-to-browser call acceptance

Run this live acceptance stage after deploying any change to browser SIP,
signaling, incoming-call UI, call teardown, or WebRTC audio. A successful build
and unit tests do not replace this check: unit tests mock the SIP session.

## Preconditions

- The deployed `/phone/` page and Go phone API are healthy.
- Two internal numbers can be used for browser clients and are not assigned to
  a physical handset for this test. Use two distinct numbers; one extension can
  have only one active browser session.
- Two independent browser profiles/devices are available, each with microphone
  permission and audio output. Do not use two tabs sharing one browser session.
- Both users can identify which test browser is A and which is B.

## Procedure

1. Open `/phone/` in browser A, connect it to the first test extension, and wait
   for **Готов принимать звонки**.
2. Open `/phone/` in an independent browser profile/device B, connect it to the
   second test extension, and wait for **Готов принимать звонки**.
3. Confirm both pages show the other browser as online in the call target list.
   If either browser is offline or missing, stop: the call test has not started.
4. From A, call B. Confirm B shows the incoming-call modal with the right
   caller, then answer. Confirm both pages show the active call and the timer
   advances.
5. Speak a short phrase from A and confirm B hears it; speak a different short
   phrase from B and confirm A hears it. Do not record or save audio.
6. End this call from B. Confirm both call modals close, both pages return to
   idle, both browser registrations remain online, and the call is gone from
   Asterisk.
7. Repeat B-to-A, answer on A, verify two-way audio, then end the call from A.
   Confirm the same clean return to idle and that both registrations remain
   online.

If the release changes RVC routing or voice profiles, run a separate audio
acceptance with the intended profile and verify the transformed voice at the
receiving browser. Do not count a basic unprocessed call as proof that RVC works.

## Pass criteria

- Incoming modal appears at the called browser for both call directions.
- Each answered call connects and two-way audio is audible.
- Hanging up from either side clears both UIs and the Asterisk call while
  preserving both SIP registrations.
- No unexplained disconnect, stuck modal, or continuing ring occurs.

Any failed criterion is a failed live acceptance; do not mark the release
verified just because signaling or the unit tests passed.

## Evidence record template

Record only the test metadata and outcome, never credentials, SIP payloads, or
audio:

| Field | Result |
| --- | --- |
| Date/time and deployed commit/release | |
| Browser A extension and browser/version | |
| Browser B extension and browser/version | |
| A-to-B incoming modal / answer / two-way audio | |
| B-to-A incoming modal / answer / two-way audio | |
| Hangup from B, then A; UI and Asterisk cleanup | |
| Final result and failure details | |

### Latest automated live run

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 09:27 UTC; release `20260929T092249Z` |
| Browser A extension and browser/version | 345; isolated Chromium 153.0.8010.36 context with synthetic media |
| Browser B extension and browser/version | 3454; separate isolated Chromium 153.0.8010.36 context with synthetic media |
| A-to-B incoming modal / answer / two-way audio | Modal, answer, connected ICE, RTP in both directions, and fresh RVC processing sample verified; synthetic microphone supplied silence, so audible speech is unverified |
| B-to-A incoming modal / answer / two-way audio | Modal, answer, connected ICE, RTP in both directions, and fresh RVC processing sample verified; synthetic microphone supplied silence, so audible speech is unverified |
| Hangup from B, then A; UI and Asterisk cleanup | Both sides returned idle and stayed registered; Asterisk reported 0 active channels and 0 active calls |
| Final result and failure details | Signaling, media transport, RVC stream, consecutive calls, and teardown passed. Human-audible two-way speech and perceptible voice conversion remain unverified because the automated run used silent synthetic audio. |

Use a concise Asterisk channel check after hangup, for example
`asterisk -rx "core show channels concise"`; record only whether the test call
was cleared, not raw channel data.
