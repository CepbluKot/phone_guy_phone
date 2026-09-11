# SIP Phone Ready Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare VM209 so a Yealink SIP-T21P E2 can register privately as extension 1987 and participate in a Phone Guy call without exposing SIP/RTP publicly.

**Architecture:** Asterisk gets an allowlisted PJSIP endpoint template rendered from runtime-only secrets. `voice-routing.yaml` maps extensions to `original` or `phone-guy`; a controller-owned SIP session keeps a phone-guy caller out of the main bridge until valid GPT v2 PCM is injected. The existing GPT v2 worker remains the only production voice engine; FCPE is not in the call path.

**Tech Stack:** Asterisk 22 / PJSIP / ARI / ConfBridge, Python 3.12, FastAPI, WebSocket PCM16 48 kHz, Docker Compose, systemd, WireGuard.

**Spec:** `docs/superpowers/specs/2026-09-08-target-phone-guy-architecture-design.md`

## Global Constraints

- VM209 only; do not alter VM208/Frigate, Proxmox resources, DNS or public firewall rules.
- SIP/RTP accept VPN/LAN source networks only; never publish 5060 or RTP on all interfaces.
- Secrets are generated under `/run/voice-secrets` or conference runtime and never committed, printed or copied to docs.
- `1987` is the only initial `phone-guy` endpoint; `1983` and `2014` are `original`.
- One AI session maximum; failure is fail-closed and never bridges raw audio.
- No recording, transcription, user PCM persistence, or cloud calls.

---

### Task 1: Define and validate voice routing

**Files:**
- Create: `deploy/voice-routing.yaml`
- Create: `conference/routing.py`
- Create: `tests/test_conference_routing.py`

**Interfaces:**
- `load_routing(path: Path) -> RoutingTable`
- `RoutingTable.profile_for(extension: str) -> VoiceProfile`
- Unknown extension or duplicate mapping raises `RoutingError` before controller health is ready.

- [x] Write tests that accept exactly `1983/2014=original`, `1987=phone-guy`, reject unknown extensions, duplicate keys, unsupported profiles, non-string extensions, and endpoint values other than `ws://127.0.0.1:8090/ws/rvc-v2`.
- [x] Run `pytest -q tests/test_conference_routing.py`; expected failure: module and contract absent.
- [x] Implement immutable dataclasses and `yaml.safe_load`; validate every schema field before returning the table.
- [x] Run the focused tests; expected pass.
- [x] Commit `feat: add strict SIP voice routing contract`.

### Task 2: Build a private PJSIP surface

**Files:**
- Create: `conference/asterisk/pjsip.conf.template`
- Modify: `conference/asterisk/Dockerfile`
- Modify: `conference/asterisk/modules.conf`
- Modify: `conference/asterisk/healthcheck.sh`
- Modify: `conference/asterisk/extensions.conf`
- Modify: `deploy/compose.conference.yaml`
- Modify: `tests/test_conference_config.py`

**Interfaces:**
- Runtime template consumes only `__SIP_1983_PASSWORD__`, `__SIP_1987_PASSWORD__`, `__SIP_2014_PASSWORD__`.
- PJSIP transports listen on the container port 5060/UDP and RTP `10000-10019/UDP`; host publishes only `192.168.20.70` and VPN-gateway source policy permits only WireGuard/LAN traffic.
- Dialing `600` enters `phoneguy-sip` Stasis; no raw PJSIP channel joins `phoneguy-main` directly.

- [x] Write configuration tests for every required PJSIP module, template placeholders, `allow=alaw`, `direct_media=no`, restricted RTP interval and absence of passwords in Git.
- [x] Run focused configuration tests; expected failure.
- [x] Enable `chan_pjsip`, `res_pjsip*`, `res_rtp_asterisk` in the source build and exact allowlist/healthcheck.
- [x] Add a template with three auth/aor/endpoint sections; use `max_contacts=1`, `rewrite_contact=yes`, `rtp_symmetric=yes`, `force_rport=yes`, `direct_media=no`, `context=phoneguy-sip` and `allow=alaw`.
- [x] Add a PJSIP dialplan which sends `600` to `Stasis(phoneguy-sip)` and terminates on any unknown extension.
- [x] Run focused config tests and `docker build` for the Asterisk image; expected pass.
- [x] Commit `feat: add private PJSIP endpoint configuration`.

### Task 3: Own SIP sessions and structural raw isolation

**Files:**
- Create: `conference/sip_session.py`
- Modify: `conference/asterisk.py`
- Modify: `conference/server.py`
- Create: `tests/test_conference_sip_session.py`

**Interfaces:**
- `SipSessionManager.handle_stasis_start(channel_id, extension) -> None`
- `original`: add the incoming channel only to main ConfBridge.
- `phone-guy`: create a private source bridge plus media pair, connect `RvcStream`, inject only converted frames into main ConfBridge.
- `close(channel_id)` is idempotent and removes all owned channels/bridges/WebSockets.

- [x] Write fake-ARI tests that prove a 1987 raw channel is never added to the main bridge, valid converted frames are added via the injection channel, and 1983 joins main directly.
- [x] Write tests for unknown extension, second phone-guy session, malformed RVC block, RVC busy and disconnect: each returns a controlled failure and never adds raw audio.
- [x] Run focused tests; expected failure.
- [x] Implement narrow ARI bridge methods and the manager; keep all audio framing through the existing `RvcStream` validator.
- [x] Run focused tests; expected pass.
- [x] Commit `feat: route SIP Phone Guy calls through GPT v2`.

### Task 4: Make deployment reversible and provision phone credentials

**Files:**
- Modify: `deploy/deploy-conference.sh`
- Modify: `deploy/compose.conference.yaml`
- Modify: `tests/test_deploy_conference.py`
- Create: `tests/live-sip-preflight.py`

**Interfaces:**
- Deployment generates three random credentials only on VM, stores them mode 0600 in the release runtime, and renders `pjsip.conf` there.
- Credentials are retrievable locally only by an explicit root-owned `deploy/show-sip-phone-setup.sh 1987` command that prints one extension at a time.
- Rollback restores previous Compose image, Caddy, conference release and no SIP listener if none existed before.

- [x] Write a controlled-root deploy test requiring a backup of PJSIP runtime/config, loopback health, Asterisk module check, and a proof that `voice-rvc.service` was not restarted.
- [x] Run test; expected failure.
- [x] Implement runtime-only secret generation and PJSIP template rendering. Do not log generated values.
- [x] Add live preflight that performs a synthetic authenticated `1983` + `1987` call using credentials only inside the VM runtime.
- [x] Run tests and shell syntax checks; expected pass.
- [x] Commit `feat: provision private SIP phone deployment`.

### Task 5: Deploy and document the T21P E2 setup

**Files:**
- Modify: `docs/IP_PHONE_BUYING_GUIDE.md`
- Create: `docs/SIP_PHONE_T21P_E2_SETUP.md`
- Modify: `docs/LIVE_STATUS.md`

- [x] Run the full test suite and take read-only VM209 baseline: active units, restart counts, free RAM/VRAM, Caddy and existing conference health.
- [x] Run the scoped conference deploy; verify no production RVC restart, only private SIP/RTP listeners and controller/RVC health.
- [x] Document exact manual T21P E2 fields: account 1, SIP server/VPN route, username 1987, generated password, transport UDP, codec preference G.711A, dialing 600, factory-reset procedure and the no-secret diagnostics commands.
- [ ] When the physical phone arrives, register it and run the two-party and three-party calls required by the spec before marking the target architecture complete.
- [x] Commit `docs: prepare Yealink SIP phone onboarding` (final documentation commit in this branch supersedes the suggested message).

## Review checklist

- Tasks 1–4 cover the routing, PJSIP, raw isolation, failure, deployment and privacy requirements of stages C–D.
- Task 5 deliberately does not claim a physical-phone call before the device arrives; it leaves a reproducible acceptance procedure instead.
- No task introduces FCPE into the telephone route or exposes credentials.
