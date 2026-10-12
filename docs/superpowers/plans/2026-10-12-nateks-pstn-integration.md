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
**Phase:** Asterisk routes are deployed but PSTN remains disabled; owner credential handoff is prepared.
**Accepted decisions:** One analog line on FXO1/AOR 701; any authenticated IP phone may dial a full 11-digit number beginning with 7 or 8, normalized to 8 plus ten digits and sent with the `701` selector; inbound PSTN rings only 2015; no RVC on PSTN; preserve internal Go/ARI routes.
**Completed, with current evidence:** Protected gateway backup and template remain outside the repository with mode 0600 under `/home/oleg/.local/share/voice-changer/backups/nateks-2026-10-12/`. The protected configurator and activation script use hidden secret input, atomically update the runtime include, back up current files, validate the dedicated authenticated endpoint, FXO1 AOR 701, identify/auth, inbound/outbound routes, and roll back on a failed check. Per-call callgroup 63 keeps PSTN pickup scoped to the 2015 leg; live-only phone endpoints and Go/ARI routes are preserved. Active release `20261012T153930Z` is healthy. Current Asterisk has 0 active channels; AOR 701 and endpoint `nateks-fxo` are absent, so PSTN is not yet active. The inbound context still dials only PJSIP/2015. The live firewall accepts UDP 5060 and RTP only from `192.168.20.0/24` (plus the configured private relay range for RTP); TCP/5061 remains restricted to the home gateway and SIP edge. The Yealink 2015 Web UI currently says **Registered**; Asterisk has its dynamic TLS contact and `qualify_frequency=0`, so `NonQual` is not evidence of a failed registration. Nateks Current Status shows FXO1/701 `Idle`, zero calls, WAN up, and SIP proxy registration disabled in saved settings. Its current WAN address is `192.168.20.236`, MAC `00:0f:d9:06:40:0d`; DHCP reservation is not present in the saved network notes. The router currently requires its local password, so no reservation was made. In the Nateks Web UI, an unsaved draft now has SIP proxy support enabled, FXO1 registration enabled, account ID `nateks-fxo`, and proxy `192.168.20.70:5060`; FXO2 remains disabled. No password was entered and the form was not submitted. The Asterisk activation command is staged at its hidden password prompt for the owner.
**Verification:** Current SSH checks -> Asterisk healthy on `20261012T153930Z`; 0 active channels; AOR 701 and `nateks-fxo` absent; inbound dialplan target is PJSIP/2015; UDP 5060 and RTP firewall scopes include the gateway's LAN subnet. Current Yealink account page -> Registered. Current Nateks Current Status -> FXO1/701 Idle, 0 calls, proxy registration disabled. Local ARP lookup -> gateway MAC `00:0f:d9:06:40:0d`. Earlier implementation checks remain recorded above: focused SIP pytest 54 passed, Node suite 40 passed, and shell/Python/diff checks passed. No PSTN calls have been made.
**Next concrete action:** Owner generates a new 20-character alphanumeric SIP secret, enters it directly in both FXO1 password fields on the staged Nateks form and submits the form, then enters the same secret twice at the hidden Asterisk terminal prompt. Never send the secret in chat. After activation, verify the gateway contact and exact routing before placing the user-authorized outbound test call.
**Affected paths:** `conference/asterisk/extensions.conf`; SIP and Go runtime/deployment helpers under `deploy/`; Nateks/PJSIP tests; this plan.
**Blockers:** The owner must submit the new SIP credential directly in the gateway UI and enter it directly at the hidden Asterisk prompt; this handoff creates the authenticated gateway access, and the secret must not be sent in chat or entered by browser automation. DHCP reservation still needs the router's local login; the gateway is currently at dynamic address `192.168.20.236`. Full acceptance remains pending: outbound carrier number format and two-way audio, inbound PSTN delivery exclusively to 2015, rejection of pickup elsewhere, internal-call regression, and clean hangup. The required browser gate needs two independent browser sessions; only the In-App Browser is currently available. Inbound PSTN acceptance also needs an originating call to the attached city line. No call has been placed yet.
