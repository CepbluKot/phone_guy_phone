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

1. Open `https://voice-phone.lan.awesomeio.ru/phone/` in browser A, connect it to the first test extension, and wait
   for **Готов принимать звонки**.
2. Open `/phone/` in an independent browser profile/device B, connect it to the
   second test extension, and wait for **Готов принимать звонки**.
3. Open `https://voice-admin.lan.awesomeio.ru/admin/#/profiles` in a browser
   session while A and B remain registered. In **Физические телефоны**, confirm
   that only extensions mapped to inventory devices appear. In **Виртуальные
   номера**, confirm configured extensions without a physical device or active
   browser appear; active browser extensions must stay in **Браузерные
   телефоны** only. Confirm both browser nicknames/extensions appear with
   **Подключён**. Refresh the page once and confirm the separation and entries
   remain. If a placeholder appears under physical phones, or an active browser
   is missing/duplicated as virtual, the display test has failed.
4. Confirm neither phone page offers an unassigned virtual placeholder such as
   1987 or 2014 as a call target. Only extensions assigned to physical devices
   or currently registered browsers should appear. The Go routing tests also
   assert that direct attempts to originate from or call an unassigned
   placeholder are rejected, even if the client bypasses the target list.
5. Confirm both phone pages show the other browser as online in the call target
   list. If either browser is offline or missing, stop: the call test has not
   started.
6. From A, call B. Confirm B shows the incoming-call modal with the right
   caller, then answer. Confirm both pages show the active call and the timer
   advances.
7. Speak a short phrase from A and confirm B hears it; speak a different short
   phrase from B and confirm A hears it. Do not record or save audio.
8. End this call from B. Confirm both call modals close, both pages return to
   idle, both browser registrations remain online, and the call is gone from
   Asterisk.
9. Repeat B-to-A, answer on A, verify two-way audio, then end the call from A.
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

### Latest production run

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 12:27 UTC; Go/Asterisk release `20260929T122734Z`, source commit `2e88fee` |
| Profile categories and live browser records | **Passed** — physical `1983`, `1988`; virtual `1987`, `2014`; active browsers `345` (`авпап`) and `3454` (`ыыва`) were shown in separate sections and remained after admin reload. |
| Virtual call-target and direct-call rejection | **Passed** — phone page offered `1988`, `1983`, and active browser `3454`, but not `1987` or `2014`. A direct SIP attempt to `1987` received `603 Decline`; the call UI returned idle and the active-call metric remained zero. |
| Browser call A-to-B and B-to-A | **Passed** — incoming modals appeared, calls connected, bidirectional RTP counters advanced, and each call produced a fresh RVC processing sample. |
| Hangup and cleanup | **Passed** — both calls ended cleanly; browser registrations stayed online. |
| Audible speech | **Unverified** — this automated run used synthetic silent microphones, so RTP and RVC processing do not prove that a person heard converted speech. |

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

### Latest automated live run after the private-domain split

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 11:31 UTC; Go release `20260929T112129Z` and separate Voice edge hosts |
| Browser A extension and browser/version | 345; isolated Chromium 153.0.8010.36 context with synthetic media at `voice-phone.lan.awesomeio.ru` |
| Browser B extension and browser/version | 3454; second isolated Chromium context at the same host |
| A-to-B incoming modal / answer / two-way audio | Modal and answer passed; ICE connected, RTP sent and received by both browsers, and a fresh RVC processing sample was observed. Audible speech is unverified because synthetic microphone audio was silent. |
| B-to-A incoming modal / answer / two-way audio | Same signaling, ICE, RTP and RVC checks passed. Audible speech remains unverified. |
| Hangup from B, then A; UI and Asterisk cleanup | Both pages returned idle and stayed registered after each hangup; Asterisk reported 0 active channels and 0 active calls. |
| Final result and failure details | The final run passed signaling, media transport, RVC stream and teardown. An immediately preceding run timed out waiting for RTP/RVC progress after answer; its channels cleared. The successful repeat initially showed one ICE peer in `checking` and then connected. Investigate if this delay recurs in real calls. |

### Admin browser-registration visibility check

Keep this check in the same live acceptance run as the calls above: while both
phone pages are still registered, open the admin profiles page and verify each
nickname, extension, and connected state. This specifically catches cross-host
API routing regressions after the admin and phone domains are split.

### Admin visibility fix and post-deploy acceptance — 2026-09-29

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 11:45 UTC; Go release `20260929T114505Z`, commit `60e3351` |
| Browser registrations in admin during both leases | **Passed** — `авпап · 345` and `ыыва · 3454` both showed **Подключён**; both remained visible after reloading the profiles page. |
| A-to-B incoming modal / answer / media / RVC | **Passed** — incoming modal appeared; ICE connected; bidirectional RTP and a fresh RVC processing sample were observed. Synthetic microphone input was silent, so audible speech is unverified. |
| B-to-A incoming modal / answer / media / RVC | **Passed** — same checks passed; synthetic microphone input was silent, so audible speech is unverified. |
| Hangup and cleanup | **Passed** — both calls ended, the browser leases were released, browser status returned `{"sessions":[]}`, and Asterisk reported 0 active channels and 0 active calls. |
| Final result | Registration display, refresh persistence, call signaling/media transport, RVC processing, and cleanup passed. Audible speech remains unverified in this synthetic-media run. |

### Mobile admin UI release and post-deploy acceptance — 2026-09-29

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 15:49 UTC; Go release `20260929T151908Z` |
| Mobile visual check | **Passed** — production admin profiles and phones pages inspected at 375px; navigation drawer, card-style physical phone rows, and single-column phone registration form were visible without clipped content. |
| Browser registrations in admin during both leases | **Passed** — `njbh · 345` and `ыыва · 3454` appeared under **Браузерные телефоны** as **Подключён**. Physical `1983`/`1988` and virtual `1987`/`2014` remained in their own sections. |
| A-to-B incoming modal / answer / media | **Passed** — modal and answer worked; ICE connected; inbound and outbound audio RTP counters advanced in both browser sessions. Call ended from B; both pages returned idle and stayed registered. |
| B-to-A incoming modal / answer / media | **Passed** — modal and answer worked; ICE connected; inbound and outbound audio RTP counters advanced in both browser sessions after the streams settled. Call ended from A; both pages returned idle and stayed registered. |
| Hangup and cleanup | **Passed** — browser leases released, `/phone/api/v1/status` returned `{"sessions":[]}`, and Asterisk reported 0 active channels and 0 active calls. |
| Service recovery after test-data cleanup | **Passed** — Go container healthy; both admin and phone domains returned HTTP 200; the temporary test nickname on `1988` was removed and the original empty display name restored. |
| Final result | Mobile layout, admin visibility, call signaling, RTP transport, hangup, and cleanup passed. Audible speech and RVC voice quality remain unverified because the isolated browsers used silent synthetic microphones. |

### Previous automated live run

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-09-29 09:27 UTC; release `20260929T092249Z` (before domain split) |
| Browser A extension and browser/version | 345; isolated Chromium 153.0.8010.36 context with synthetic media |
| Browser B extension and browser/version | 3454; separate isolated Chromium 153.0.8010.36 context with synthetic media |
| A-to-B incoming modal / answer / two-way audio | Modal, answer, connected ICE, RTP in both directions, and fresh RVC processing sample verified; synthetic microphone supplied silence, so audible speech is unverified |
| B-to-A incoming modal / answer / two-way audio | Modal, answer, connected ICE, RTP in both directions, and fresh RVC processing sample verified; synthetic microphone supplied silence, so audible speech is unverified |
| Hangup from B, then A; UI and Asterisk cleanup | Both sides returned idle and stayed registered; Asterisk reported 0 active channels and 0 active calls |
| Final result and failure details | Signaling, media transport, RVC stream, consecutive calls, and teardown passed. Human-audible two-way speech and perceptible voice conversion remain unverified because the automated run used silent synthetic audio. |

Use a concise Asterisk channel check after hangup, for example
`asterisk -rx "core show channels concise"`; record only whether the test call
was cleared, not raw channel data.
