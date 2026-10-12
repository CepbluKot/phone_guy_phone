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

**Goal:** Implement the approved PSTN behavior in this brief.
**Phase:** implementation; device configuration, deployment, and live acceptance remain blocked.
**Accepted decisions:** One analog line on FXO1/AOR 701; allow full Russian 11-digit input beginning with 7 or 8 and send `701` plus the normalized 8-prefixed number to select FXO1; inbound exclusively to 2015; preserve internal Go/ARI and endpoint policies; no RVC on PSTN.
**Completed, with evidence:** Confirmed the implementation checkout began at `f15237c` on `codex/nateks-pstn-spec`. Added a comments-only runtime PSTN include created only if absent, an inbound context dialing only extension 2015, and authenticated phone routes accepting full 11-digit input beginning with 7 or 8; both normalize to the 8-prefixed form and prepend FXO1 selector `701` on the SIP call. SIP and Go releases merge only managed routes into the live dialplan, preserving live `1900 -> 2016`; the SIP release also installs the required IP identifier module list while preserving file owner/mode. Fresh read-only Asterisk evidence: 0 active channels, endpoint 2015 is registered but `NonQual`, and Nateks endpoint is absent. The TP-Link router already reserves `192.168.20.236` for Nateks; port forwarding and port triggering have no entries, DMZ is disabled, and UPnP is enabled with existing mappings for other LAN devices. No router settings were changed. The gateway UI shows both FXO ports enabled, hotline routing disabled, no incoming destination, both line registrations disabled, its SIP server at the factory address, trunk extension selection enabled, and one-way media selected. Attempts to export its configuration through the browser produced no download event and no local backup file. No gateway, router, or Asterisk runtime settings were changed.
**Verification:** The latest focused selection passed 31 tests covering PSTN/runtime merge, PJSIP merge, managed phone routes, release preservation, firewall policy, and browser config. Shell syntax, Python compilation, and `git diff --check` passed. An earlier broader selected run had 68 passed and 2 unrelated stale assertions: one expects recording extensions absent from the checked-in base dialplan, and one expects an obsolete Go smoke-test message. The Asterisk image build previously stopped at Debian repository signature validation; do not disable signature checks. No deployment, restart, physical call, browser call, or rollback run.
**Next concrete action:** Save and verify a gateway configuration backup, then configure its authenticated FXO1 SIP account and two-way media. Deploy through the existing SIP release path only after reviewing the live merge and source-restricted firewall impact.
**Affected paths:** `conference/asterisk/extensions.conf`, `conference/asterisk/nateks-pstn.conf.template`, `deploy/update_pjsip_wss_transport.py`, `deploy/sync_asterisk_pstn_runtime.py`, `deploy/sync_asterisk_extensions_runtime.py`, SIP/Go release scripts, `deploy/compose.conference.yaml`, focused `tests/*`, this plan.
**Verification:** focused PSTN/PJSIP and release selection -> 31 passed; previous broader selected run -> 68 passed, 2 pre-existing stale assertions; `py_compile`, `bash -n`, and `git diff --check` -> passed. Compose rendering had passed before the latest number-format edits. No deployment, restart, physical call, browser call, or rollback run.
**Ruling:** Accept both full 7- and 8-prefixed inputs and send `701` plus an 8-prefixed ten-digit number to the FXO line. The owner explicitly chose both inputs; the current gateway UI has "Capture line by dialing number Extension" enabled, and the [VoiceCom 100 manual](https://kombitel.ru/profiles/2/01.pdf) describes dialing the FXO extension number to select a line. Its Moscow long-distance example uses prefix 8. Cost if wrong: the carrier or device firmware may expect a different number sequence, so outbound calls can fail until physical acceptance confirms it.
**Blocker or decision needed:** The gateway configuration export could not be downloaded through the browser; obtain and verify a backup before the first gateway change. Then confirm the action-time credential write immediately before entering the generated SIP secret in the Nateks UI and matching Asterisk runtime. Endpoint 2015 must register and pass physical ringing/audio. Built-in `*8` pickup is enabled globally, but the observed 2015 call/pickup groups are empty; preserve that state and require a physical rejection of pickup from another extension during acceptance. Mandatory two-browser acceptance and PSTN acceptance are incomplete. The router's current reservation removes the need to add another DHCP reservation; current no-forwarding/DMZ state is verified, while UPnP remains enabled and unchanged. The user-authorized mobile test number remains absent from files/logs/chat.
