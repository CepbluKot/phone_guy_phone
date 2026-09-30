# Public Browser Phone Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser phone usable from the public internet by the owner only, with mandatory MFA and no public SIP/RTP/ARI/RVC access.

**Architecture:** Publish a phone-only Teleport Application Access endpoint on `phone.awesomeio.ru`; a dedicated phone-role account requires password plus TOTP. The voice VM joins Teleport outbound and proxies authenticated WSS to Asterisk loopback. A VPS TURN service relays browser media with short-lived credentials and an Asterisk-only peer allowlist.

**Tech Stack:** Go, React/SIP.js, Teleport Community, Caddy, coturn, REG.RU DNS, WireGuard.

**Spec:** [2026-09-30-public-browser-phone-access-design.md](../specs/2026-09-30-public-browser-phone-access-design.md)

## Global Constraints

- Keep the admin UI private.
- Do not expose SIP, ARI, RVC, provisioning credentials, or direct Asterisk RTP ports.
- Public access requires a dedicated Teleport role limited to `voice-phone` and mandatory TOTP.
- TURN relays only to the Asterisk host and configured RTP range; credentials are time-limited.
- Back up each live config before changing it and retain an exact rollback path.

## Review Focus

- Unauthenticated API/WebSocket access: verify claim/register attempts fail before reaching Asterisk.
- Teleport role leakage: verify the new user cannot reach admin or existing apps.
- TURN abuse: reject missing/expired credentials and peer destinations outside the Asterisk RTP range.
- Direct network bypass: externally probe VM HTTP, SIP, ARI, RVC, and RTP ports.
- NAT/media failure: run external-network calls both directions and confirm audio and hangup cleanup.

---

### Task 1: Public signaling and TURN contracts

**Files:**
- Modify: `internal/webphone/session.go`, `internal/webphone/handlers.go`, `cmd/voice-web/main.go`
- Modify: `admin-ui/src/phone/api.ts`, `admin-ui/src/phone/sipSession.ts`
- Test: `internal/webphone/*_test.go`, `cmd/voice-web/main_test.go`, `admin-ui/src/phone/*.test.tsx`

- [x] Add tests for public-origin signaling URL, public SIP domain, and bounded TURN credential response.
- [x] Add a test proving the Go-origin WebSocket route only upgrades `/ws/phone-signaling` and proxies to loopback Asterisk.
- [x] Implement exact-origin-aware phone config and session identity; preserve the existing private origin behavior.
- [x] Add test coverage that admin and unrelated paths are unavailable through the public phone origin.
- [x] Run Go and React focused tests, then full project checks.

### Task 2: Authenticated media relay

**Files:**
- Create: `deploy/coturn/turnserver.conf.template`, `deploy/coturn/voice-turn.service`
- Modify: `deploy/compose.goweb.yaml`, `deploy/deploy-goweb.sh`, `deploy/rollback-goweb-production.sh`
- Test: `tests/test_public_phone_access.py`

- [ ] Pin/install coturn and validate the live service configuration: `use-auth-secret`, bounded quotas, TLS listener policy, explicit relay range, and Asterisk-only peer IP/ports. (Templates are present; the package and relay are not installed.)
- [x] Add contract tests rejecting unauthenticated TURN, broad peer ranges, or public direct RTP.
- [x] Issue expiring TURN REST credentials from the private Go config using a secret mounted read-only; never log the secret.
- [ ] Validate coturn configuration and authenticated relay behavior in isolation before deployment.

### Task 3: Owner-only Teleport and public ingress

**Files:**
- Modify: `/home/oleg/Documents/homelab/dns-awesomeio/caddy/Caddyfile`, `/home/oleg/Documents/homelab/dns-awesomeio/README.md`
- Modify: `/home/oleg/Documents/homelab/new-vpn/KUBERNETES_REMOTE_ACCESS.md`, `/home/oleg/Documents/homelab/new-vpn/OPERATIONS.md`
- Add: dedicated Teleport phone-only role/app resource and owner-only enrollment notes.
- Test: `tests/test_public_phone_access.py`, Caddy validation, Teleport role/app review.

- [ ] Create a role with only the `voice-phone` app label and mandatory OTP; create a separate owner login/invite. (Blocked: no authenticated Teleport admin session; owner enrollment is incomplete.)
- [ ] Enroll the voice VM Application Service using an expiring app-only join token and a loopback phone origin.
- [ ] Back up live VPS Caddy and DNS state; add one explicit `phone.awesomeio.ru` public A record and a Caddy route to Teleport.
- [ ] Keep `/admin`, private-zone names, and unapproved paths unavailable on the public hostname.
- [ ] Verify anonymous redirects, authenticated app authorization, WebSocket upgrade, and TOTP enrollment before opening the route.

### Task 4: Gated rollout and external acceptance

- [ ] Back up the voice VM release, Caddy, Teleport agent config, VPS Caddy, coturn config, firewall, and DNS zone state.
- [x] Deploy Go/WSS changes privately while the public edge route remains absent; public signaling fails closed without TURN credentials.
- [x] Run source-level and private-network checks; confirm the deployment rollback path.
- [ ] Verify actual TURN relay target/credentials in an isolated live relay.
- [ ] Activate DNS/edge/firewall only after account, MFA, WSS, and TURN gates pass.
- [ ] From a non-VPN network, test owner login, denied anonymous registration, calls in both directions, audible media, voice processing, and call cleanup.
- [ ] Probe public ports and prove only intended HTTPS and TURN services are exposed; revert if any other listener answers.
- [ ] Update `docs/PUBLIC_PHONE_ACCESS_PLAN.md`, `docs/OPERATIONS.md`, homelab operations docs, and `docs/LIVE_STATUS.md` with evidence and rollback path.
