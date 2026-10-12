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
**Phase:** verification; gateway credential setup and call acceptance pending.
**Accepted decisions:** One analog line on FXO1/AOR 701; any authenticated IP phone may dial a full 11-digit number beginning with 7 or 8, normalized to 8 plus ten digits and sent with the `701` selector; inbound PSTN rings only 2015; no RVC on PSTN; preserve internal Go/ARI routes.
**Completed, with evidence:** Downloaded a complete gateway configuration backup and template through the Web UI, stored outside the repository with mode 0600 under `/home/oleg/.local/share/voice-changer/backups/nateks-2026-10-12/`, and compared the two copies without opening their contents. Live preflight: Asterisk and Go healthy, 0 active channels, active release `/opt/voice-go/releases/20261010T124849Z`, PJSIP runtime owned by UID/GID 10001 at mode 0600, `/opt` free space 4243 MB, and UDP 5060 `DOCKER-USER` hook absent. Added protected SIP-peer generation and activation with hidden password entry, atomic runtime updates, private-LAN identify/auth, FXO1 AOR 701, endpoint/AOR/firewall/dialplan checks, and backup/rollback on failure. Added a per-call pre-dial handler assigning pickup callgroup 63 to the 2015 PSTN leg, while rejecting static reuse of 63. SIP and Go release inputs now carry the runtime and activation helpers. Outbound routes remain disabled until the protected peer exists; existing Go/ARI routing is preserved.
**Verification:** Exact SIP release pytest selection -> 54 passed; Node suite -> 40 passed; shell syntax, Python compilation, and `git diff --check` -> passed. Asterisk release exited during post-start validation and its automatic rollback restored the prior release: live container mounts still point to `20261010T124849Z`, Asterisk is healthy with 0 channels, and the UDP 5060 firewall hook is absent. Root cause: the script required endpoint 1983 although the live runtime declares 2016, 1987, 1988, 2014, 2015 and 1234. The guard now checks endpoints declared by runtime PJSIP and requires the selected inbound endpoint 2015. Removed only this run's inactive candidate release and image/build layer; retained its backup snapshot. `/opt` has 4244 MB free. No SIP peer, gateway settings, or test calls have been applied.
**Next concrete action:** Commit/push the runtime-aware endpoint guard and exact 20-character secret validation, then retry the SIP release. Verify Asterisk, Go ARI, dialplan, readability, and firewall before preparing the gateway form. Before any new SIP credential is entered, hand control of the gateway form to the owner; the owner must type, confirm, and submit it, then enter that same value at the hidden Asterisk prompt.
**Affected paths:** `conference/asterisk/extensions.conf`; SIP and Go runtime/deployment helpers under `deploy/`; Nateks/PJSIP tests; this plan.
**Blockers:** Computer-use policy requires handoff for entry and submission of a new authentication credential and action-time confirmation before creating security-sensitive SIP access. Do not enter or submit the new credential through browser automation. Project completion also requires two independent browser sessions, physical PSTN call directions, pickup rejection from another phone, two-way audio, and cleared channels; none has been run. Carrier acceptance of the normalized 8-prefixed digits still requires a live call.
