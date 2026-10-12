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
**Phase:** live Asterisk release deployed; gateway credential handoff and acceptance pending.
**Accepted decisions:** One analog line on FXO1/AOR 701; any authenticated IP phone may dial a full 11-digit number beginning with 7 or 8, normalized to 8 plus ten digits and sent with the `701` selector; inbound PSTN rings only 2015; no RVC on PSTN; preserve internal Go/ARI routes.
**Completed, with evidence:** Downloaded the gateway configuration backup and template through the Web UI, stored them outside the repository with mode 0600 under `/home/oleg/.local/share/voice-changer/backups/nateks-2026-10-12/`, and compared the copies without opening their contents. Added protected SIP-peer generation/activation with hidden secret entry, atomic runtime updates, private-LAN identify/auth, FXO1 AOR 701, endpoint/AOR/firewall/dialplan checks and backup/rollback. Added per-call callgroup 63 for the 2015 PSTN leg and validation against static reuse. Preserved live-only phone endpoints and current Go/ARI routes. Deployed release `20261012T153930Z`; the Asterisk container is healthy, Go `/healthz` and ARI `voice-control` are available, channels are 0, and UDP 5060 accepts only the home LAN and drops other sources. The Nateks include is readable at mode 0600. Inbound dialplan targets only PJSIP/2015 and applies callgroup 63. Outbound PSTN is still disabled until the authenticated Nateks peer is configured. Asterisk currently reports extension 2015 Unavailable. In the Nateks Web UI, only the non-secret draft values (Asterisk address/port, FXO1 registration and account ID) are filled; password boxes remain blank and the form has not been submitted.
**Verification:** Focused SIP pytest selection -> 54 passed; Node suite -> 40 passed; shell syntax, Python compilation, and `git diff --check` -> passed. One initial post-start deployment rolled back because its check assumed endpoint 1983 was present; the runtime-aware endpoint validation was committed and pushed, and the subsequent release passed all deployment gates. The generated inactive release and image layers from that failed attempt were removed while preserving its backup. No Nateks settings have been saved, no SIP credential has been created, and no calls have been made.
**Next concrete action:** Perform the required owner handoff: enter and submit a new 20-character alphanumeric SIP secret in the gateway form, then enter the same value at the hidden Asterisk activation prompt. Verify endpoint registration, outbound route activation, and only then run the physical and two-browser call acceptance.
**Affected paths:** `conference/asterisk/extensions.conf`; SIP and Go runtime/deployment helpers under `deploy/`; Nateks/PJSIP tests; this plan.
**Blockers:** Computer-use policy requires handoff for entry/submission of a new authentication credential and action-time confirmation before creating security-sensitive SIP access. Do not enter or submit the new credential through browser automation. Extension 2015 is currently Unavailable, so inbound ringing cannot yet be accepted. Project completion also requires independent-browser calls, both physical PSTN directions, pickup rejection from another phone, two-way audio and cleared channels; none has been run. Carrier acceptance of the normalized 8-prefixed digits still requires a live call.
