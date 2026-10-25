# Nateks VC-110-1 PSTN integration

## Implementation Brief

### Goal

Route one analog line on Nateks FXO1 through Asterisk. Authenticated owner IP phones enter Russian 11-digit numbers with either leading 7 or 8; Asterisk sends `701` plus the normalized 8-prefixed number so Nateks selects FXO1. Inbound city calls ring and can be answered only on extension 2015. Existing internal Go/ARI calls remain functional.

### Non-goals

- RVC/Phone Guy processing on city calls.
- A second FXO line, emergency, international, premium, or short-code routes.
- Internet exposure of Nateks SIP, RTP, or management.

### Accepted decisions and constraints

- One analog line is connected to FXO1 (line ID/AOR 701); inbound target is 2015.
- Accept only full 11-digit numbers beginning with 7 or 8. Normalize by replacing the first digit with 8; prepend line selector `701` on the Asterisk-to-gateway SIP call so Nateks uses FXO1.
- Use a dedicated authenticated Nateks PJSIP endpoint and isolated inbound context. Authorize outbound routing by authenticated endpoint/context, never by Caller ID.
- Accept only the owner's chosen full 11-digit Russian number forms, entered with 7 or 8; normalize to the 8-prefixed form for the analog line. Reject all other external patterns.
- Keep gateway signaling and media on the private network and restricted to Asterisk; preserve every existing phone's transport, authentication, media, and runtime-only settings.
- The live Asterisk configuration differs from repository templates: live endpoints include 1234/2015/2016, and live 1900 currently targets 2016 while source targets 1983. Preserve live behavior and merge narrowly.
- Release with existing SIP deployment and rollback procedures. Do not deploy if runtime drift would be overwritten or required device/line facts remain unknown.

### Steps

1. Inspect current code, the live Asterisk runtime, gateway/lease, physical FXO port and line state, provider number format, and disconnect/Caller-ID/DTMF behavior. Capture redacted comparisons. Make no discovery calls.
2. Add focused config validation for the new endpoint and route. Update `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`, `pjsip.conf.template`, and a narrow live-runtime merge path as needed. Any identifier module must be built, loaded, and health-checked. Preserve current endpoint policies.
3. Add explicit full-number outbound patterns for 7/8-prefixed 11-digit Russian numbers and an inbound context that calls only `PJSIP/2015`; retain `_X.` Go/ARI routing and live internal destinations. Add tests for normalization, disallowed patterns and identity-based authorization.
4. Carry the peer and routes through both SIP and Go release paths and rollback. Restrict gateway SIP/RTP at the host/router, reserve its address, and ensure no WAN/public management forwarding. Back up and compare live files before any release.
5. Run focused tests, existing SIP/Go release checks, static config checks, and `git diff --check`. Deploy only after current live state and rendered merge have been reviewed; verify module, peer, routes, firewall, Go ARI, health, and zero orphaned channels.
6. Complete physical acceptance: internal two-way call; outbound PSTN from each eligible phone; inbound only to 2015 with no pickup from other phones; busy behavior; ordinary two-way audio; hangup, DTMF, Caller ID and disconnect signaling where available. Complete both directions of the mandatory independent-browser acceptance and record truthful results in `docs/BROWSER_CALL_ACCEPTANCE.md`.

### Verification

- Focused tests in `tests/` for endpoint rendering, route authorization/number filtering, guarded merge, and rollback preservation.
- Existing SIP/Go deployment test suites and JavaScript checks named by the release scripts.
- `git diff --check`, runtime `pjsip show endpoints`, `dialplan show`, module/health checks, firewall inspection, and `core show channels count`.
- Live physical and two-browser acceptance; document each result only after observation.

### Risks and escalation conditions

- The owner identified FXO1 and requested both full 7- and 8-prefixed number entry. The VoiceCom 100 manual describes 8 as its Moscow-example long-distance prefix, but this does not establish this carrier line's dial tone/acceptance behavior; verify on an authorized physical call before claiming PSTN acceptance.
- The gateway UI requires the owner's private login; credentials must not be requested in chat, stored in repo, or echoed. Inspect before applying changes.
- Endpoint 2015's last observed contact was `NonQual`; registration does not prove ringing/audio.
- Stop before deployment if the narrow merge cannot preserve runtime-only endpoints, live internal routes, TLS/media policy, or the rollback copy.
- The project requires two independent browser sessions for live call acceptance. If the second session or an external line is unavailable, leave acceptance incomplete and state the exact blocker.

## Execution State

**Goal:** Implement the approved FXO1 PSTN behavior and finish live acceptance where the available devices permit.
**Phase:** Asterisk routes are active; waiting for the owner to save the Nateks SIP credential and for physical call acceptance.
**Accepted decisions:** One analog line on FXO1/AOR 701; any authenticated IP phone may dial a full 11-digit number beginning with 7 or 8, normalized to 8 plus ten digits and sent with the `701` selector; inbound PSTN rings only 2015; no RVC on PSTN; preserve internal Go/ARI routes.
**Completed, with current evidence:** The protected gateway backup/template and generated SIP secret are outside the repository; the secret file is mode 0600 in a mode 0700 directory. The secret was applied to Asterisk through hidden input, but is intentionally not recorded here. Active release `20261012T153930Z` is healthy. Nateks activation added and loaded authenticated endpoint `nateks-fxo`, FXO1 AOR 701 and IP identify, outbound 7/8-prefixed full-number patterns, and inbound routing only to PJSIP/2015. Per-call callgroup 63 scopes PSTN pickup to 2015; live-only phone endpoints and Go/ARI routes are preserved. Firewall checks still restrict SIP/RTP to private ranges. Yealink 2015 reports Registered after Asterisk recreation. Asterisk has 0 active channels. The router UI shows an existing reservation for `192.168.20.236`; its displayed MAC `1E-61-B4-06-40-0D` differs from the earlier ARP observation `00:0F:D9:06:40:0D`, so the reservation was not edited and this identity discrepancy remains to reconcile. The Nateks Web UI has an unsaved draft with proxy registration enabled for FXO1 account `nateks-fxo`, proxy `192.168.20.70:5060`; FXO2 remains disabled. Both password fields are blank; the form has not been submitted. The owner must enter the already-generated secret from the protected local file into both fields and click “Принять” because browser credential entry/submission is handed back to the owner.
**Verification:** Focused PSTN runtime suite -> 15 passed, including positive and negative checks that independently validate both outbound pattern blocks. Shell syntax checks (`bash -n` for the deploy, activation, and remote SIP helpers), Python compile for the route verifier, and `git diff --check` passed. Live Asterisk -> healthy, endpoint/identify and AOR 701 loaded, both outbound patterns present, inbound target PJSIP/2015, 0 active channels. Yealink account page -> Registered. Router DHCP reservation table -> `.236` reservation present with the noted MAC discrepancy. No PSTN calls have been placed. The full SIP release script was not run: its 4 GB free-space preflight exceeds the 2.66 GB available. Instead, the activation helper in the active release was updated from the reviewed source with protected pre-change backups, and Asterisk was recreated from the active Compose release and returned healthy. This deployment limitation remains recorded; no release build was performed.
**Next concrete action:** Owner enters the locally saved SIP secret into both FXO1 password fields in the open Nateks form and clicks “Принять”. Then verify AOR 701 contact and continue call acceptance: internal calls, authorized outbound from eligible phones, inbound to 2015 only, audio, hangup, and DTMF where available.
**Affected paths:** `conference/asterisk/extensions.conf`; SIP and Go runtime/deployment helpers under `deploy/`; Nateks/PJSIP tests; this plan.
**Blockers:** Nateks has not submitted the credential, so AOR 701 has no gateway contact and no city calls can yet be tested. Physical call acceptance remains pending for carrier number format, two-way audio, inbound delivery exclusively to 2015, rejection elsewhere, and hangup behavior. The project requires two independent browser sessions for live browser-call acceptance; only one Codex in-app browser is currently available. The DHCP reservation exists, but its MAC does not match the earlier ARP observation and should be reconciled before relying on address identity.
