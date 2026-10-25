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

**Goal:** Keep the FXO1 PSTN route working and finish live acceptance.
**Phase:** live verification; the gateway is registered, but handset 2015 has no active Asterisk contact.
**Accepted decisions:** One analog line on FXO1/AOR 701; any authenticated IP phone may dial a full 11-digit number beginning with 7 or 8, normalized to 8 plus ten digits and sent with the `701` selector; inbound PSTN rings only 2015; no RVC on PSTN; preserve internal Go/ARI routes.
**Completed, with evidence:** The prior implementation added protected SIP-peer generation and activation, FXO1 AOR 701, inbound-only-to-2015 routing, a per-call pre-dial handler assigning pickup callgroup 63, and guarded runtime merging. Asterisk release `20261012T211343Z` is deployed on VM `192.168.20.70`; its container is healthy, the TLS transport is configured with public media address `94.102.89.13` and only Docker subnet `172.19.0.0/16` as local. Router-forwarded TLS from `192.168.20.12` is allowed by the VM firewall. Nateks Web UI shows FXO1 enabled as 701, Proxy Server `192.168.20.70:5060`, registration interval 600 seconds, FXO1 idle with zero calls, and Proxy Register Successful. Asterisk `pjsip show contacts` showed `701/sip:701@192.168.20.236:5060` as Avail; the latest call-count check showed zero active channels. The Yealink 2015 endpoint is currently Unavailable with no registered contact. Existing Nateks logs contain an earlier outbound call reaching Talking, but do not establish two-way audio or clean hangup for the current release. The fixes are committed and pushed to `origin/codex/fix-yealink-rtp`.
**Verification:** Prior focused Python tests -> 55 passed; Node suite -> 40 passed; shell syntax and `git diff --check` passed. Those tests ran before the current workstation execution restriction. Current checks were performed against the designated VM: Asterisk healthy, Nateks HTTP 200, FXO1 registration Available at last PJSIP contact inspection, and zero active channels. Mandatory live browser-to-browser acceptance and physical PSTN audio acceptance remain incomplete.
**Next concrete action:** Restore Yealink 2015 registration, then place a short call to the owner's mobile over FXO1 and confirm two-way audio, remote hangup, and cleared channels. Also complete the required two-session browser acceptance before reporting the project complete. The gateway restart was issued through its UI; it was briefly unreachable on the LAN, then returned. No additional reboot or credential change is planned without a specific need.
**Affected paths:** `conference/asterisk/pjsip.conf.template`; `deploy/firewall.sh`; SIP transport/runtime/deployment helpers; focused SIP/Nateks tests; this plan.
**Blockers:** Yealink 2015 is currently unavailable to Asterisk, so a new physical PSTN call cannot yet be placed from that handset. Current browser-to-browser acceptance, inbound PSTN routing/pickup rejection, two-way audio and hangup checks are also not yet verified.
