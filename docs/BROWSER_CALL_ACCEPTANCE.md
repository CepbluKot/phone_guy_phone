# Browser-to-browser call acceptance

## 2026-10-10 RVC recovery after call declined on answer — acceptance pending

| Field | Result |
| --- | --- |
| Symptom | Owner reports the call rings, then fails with `Decline` when the callee answers. |
| Live diagnosis | Four `voice-go` call attempts failed opening the RVC stream (`rvc_model_unavailable`). RVC `/healthz` returned `503 model_unavailable`; CUDA initialization logged error 804, with loaded NVIDIA module `580.173.02` but installed module/libraries `580.178.04`. |
| Recovery | VM209 rebooted at 2026-10-10 11:50 UTC while Asterisk had 0 active channels. After boot, module `580.178.04` loaded, GTX 1050 Ti was visible, RVC returned `ready`, and Go/Asterisk were healthy. |
| Physical call after recovery | **Pending** — the owner has not yet confirmed a repeat call, two-way audio, hangup, and cleared channels after the reboot. |
| Browser-to-browser call | **Not run** — this operational recovery has not passed the repository's separate two-independent-browser acceptance gate. |
| Final result | RVC service recovery is verified; end-to-end phone acceptance remains open. |

## 2026-10-01 physical-number reservation and browser-call acceptance — media path passed; speech quality partial

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-10-01; Go update `20261001T182748Z` |
| Browser registration choices | **Passed** — in the in-app phone page, the selector contained 2014, 444, 1987, 345, and 3454; assigned physical extensions 1983 and 1988 were absent. The call-target selector continued to list physical phones. |
| Direct API protection | **Passed in tests** — Go regression tests reject existing-number and new-number claims for physical assignments without creating a session. A live claim was not attempted. |
| Test registrations and admin view | **Passed** — two temporary in-app browser tabs registered on nonphysical extensions 3454 and 345. Both showed **Готов принимать звонки** and each other online. After a reload, admin profiles listed both active browser numbers with the Phone Guy profile. |
| A-to-B call | **Passed** — 345 called 3454; the recipient showed **Вам звонят**, answering showed **Идёт разговор** on both tabs, and the caller-side hangup returned both pages to idle. |
| B-to-A call | **Passed** — 3454 called 345; the recipient showed **Вам звонят**, answering showed **Идёт разговор** on both tabs, and the recipient-side hangup returned both pages to idle. |
| RTP and RVC path | **Passed at transport/pipeline level** — Asterisk channel statistics showed increasing packet counters in both calls. The server logged nonzero input frames and nonzero RVC output blocks; live route config mapped both test extensions to Phone Guy. This confirms signal and processed frames moved through the route, not that speech sounded intelligible. |
| Audible speech quality | **Not verified** — this run used two tabs in one in-app browser profile and no person spoke a known phrase for listening comparison. Microphone input contained signal, but the logs cannot distinguish speech from background noise. |
| Teardown and cleanup | **Passed with a log caveat** — after each hangup both pages were idle; Asterisk ended at 0 active channels and 0 active calls. The temporary registrations were disconnected, the API returned an empty session list, and only the user's original tabs remained open. Two `ari_resource_not_owned` messages appeared around the tested teardown times; their cause is not isolated, despite clean final call/session state. |
| Final result | Physical-number exclusion and both-direction browser call signaling, RTP flow, RVC frame flow, and hangup are verified live. Human-audible speech quality and the teardown log warning remain unverified/open. The deployed release is `20261001T182748Z`; this acceptance run made no application code or deployment changes. |

## 2026-10-01 unassigned-phone admin page — live acceptance incomplete

| Field | Result |
| --- | --- |
| Date/time and deployed release | 2026-10-01 17:23 UTC; release `20261001T170648Z` |
| Admin phones page | **Passed** — production showed only the known physical phones without an extension; the current inventory has none. Manual add, assigned-phone list, and load cards were absent. |
| Admin browser registration visibility | **Passed** — while test browsers were registered, profiles showed `авпап · 345` and `ыыва · 3454` as connected. |
| Browser A / Browser B | Chrome `345` and in-app browser `3454`; both registrations reached **Готов принимать звонки** and showed each other online. |
| A-to-B incoming modal / answer / two-way audio | **Failed before answer** — caller stayed at **Звоним**; the recipient stayed idle and showed no incoming modal. |
| Microphone permission | Both browser origins reported microphone permission state `prompt`; it was not granted during this run. The call likely stalled before sending its SIP INVITE while WebRTC awaited microphone access; that browser-side wait was not instrumented, so this remains a likely cause rather than a confirmed root cause. |
| Asterisk call state | **0 active channels, 0 calls processed** after the attempt; no call or media was established. |
| B-to-A / hangup | Not attempted because A-to-B did not reach the recipient. No connected call existed to hang up. |
| Cleanup | **Passed** — test pages were disconnected/reloaded; Go status returned `{"sessions":[]}` and Asterisk returned 0 active channels/calls. |
| Final result | The admin UI is deployed and verified, but mandatory call acceptance remains **incomplete** until microphone access is allowed and both call directions, audio, and hangup are tested. |

## 2026-10-01 read-only Asterisk dashboard release `20261001T144903Z` — call gate failed

**Registration and admin visibility passed; call acceptance failed.** The Go
service and Asterisk deployed healthy. The private Asterisk page showed ARI
ready, 0 active channels, and the live static PJSIP registrations: `1983`
online; `1987`, `1988`, and `2014` offline. The physical phone inventory page
showed the same online/offline result for its assigned handsets. Admin phone
profiles showed browser leases `345` and `3454` as connected while those test
leases were active.

For call testing, two independent browser contexts registered on the private
phone hostname (`345` in Chrome and `3454` in the in-app browser). Both reached
**Готов принимать звонки**, and the caller's directory listed the recipient as
online. The `345`-to-`3454` attempt displayed **Звоним** at the caller, but the
recipient never showed an incoming modal. Asterisk stayed at 0 active channels
and 0 processed calls. The reverse direction was not attempted after this
failure. No audio, RVC conversion, or in-call hangup was verified. The stuck
caller tab was closed; the other browser was disconnected. Final Go browser
status returned `{"sessions":[]}`, the test nicknames were restored, and
Asterisk returned 0 active channels/calls.

The dashboard, live endpoint states, and deployment health are verified. The
required browser-call gate remains failed/incomplete until call signaling is
diagnosed and both directions are rerun successfully.

## 2026-10-01 Asterisk release `20261001T124305Z` — incomplete

**Live browser-call acceptance did not pass.** On the public page, temporary
browser registration on extension `345` failed with the generic connection
error. On the private phone page, test extensions `345` (`авпап`) and `3454`
(`ыыва`) both reached **Готов принимать звонки** and appeared in each other's
target list. Calling `3454` from `345` left the caller at **Звоним**; no incoming
modal appeared. Asterisk reported zero channels and zero calls processed, so no
INVITE or media path was confirmed by the server. The cause is unknown; do not
attribute this to microphone permission or to the SIP edge without further
evidence. A follow-up attempt in regular Chrome could not load the private phone
hostname (`ERR_HTTP2_PROTOCOL_ERROR`); the public hostname showed its owner-login
form in that profile, so no second authenticated Chrome session was available.
The admin profiles view was not checked while these leases were live.

Both temporary tabs were closed. After their leases expired, `/phone/api/v1/status`
returned `{"sessions":[]}` and Asterisk reported zero active channels and calls.
The test restored the known directory nicknames `авпап` and `ыыва`. No user audio
was recorded. Human speech, two-way audio, RVC, and hangup acceptance were not
verified in this run. Treat the live gate as **failed/incomplete** until the
underlying call signaling is diagnosed and the full check is repeated.

## 2026-10-01 Asterisk Internet SIP deployment — partial acceptance

**Call signaling, incoming UI, RTP, and hangup passed; the full independent-
browser acceptance remains incomplete.** Two test phone clients registered as
`345` and `3454` in separate tabs of one in-app-browser profile on the private
phone domain. Both call directions showed the incoming modal, connected ICE,
and exchanged live audio RTP counters. Each side hung up once; both pages
returned to idle. The Go session status returned zero test sessions and Asterisk
reported zero active channels/calls.

The browser-test source was a generated 440 Hz tone; no microphone permission or
human speech was used. RTP is verified, but human-audible speech and RVC voice
quality are not. The required second independent browser profile could not be
used: Chrome could not open the private phone hostname and returned
`ERR_HTTP2_PROTOCOL_ERROR`. The admin profiles view was not separately checked
during this run. Do not count this as a full checklist pass. The separate
physical-handset test from a non-VPN network is still pending.

## 2026-09-30 public same-origin config fix — release `20260930T203223Z`

**Registration smoke passed; browser-to-browser call acceptance incomplete.**
The authenticated public phone page at `phone.awesomeio.ru` was reloaded and
connected to test extension 1988. It reached **Готов принимать звонки**, proving
the config request without `Origin` and the subsequent claim succeeded. The
test lease was released and its temporary directory nickname removed. No second
browser call, bidirectional audio, RVC, or hangup acceptance was run for this
release; the previous call run below is historical evidence only.

## 2026-09-30 release `20260930T185105Z`

**Browser-to-browser call acceptance incomplete.** The public owner-login flow
passed its HTTPS acceptance (custom page, invalid credentials and Origin
rejected, persistent cookie, protected API and WSS, logout, and public admin/
health isolation). Two independent browser phone sessions were not set up for
this auth-only update, so call UI, media, RVC, and hangup acceptance remain
unverified. The previous 2026-09-29 private call acceptance is historical only
and does not validate this public route.

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
