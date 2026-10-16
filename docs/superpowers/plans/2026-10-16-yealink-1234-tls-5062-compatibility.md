# Yealink 1234 TLS/5062 Compatibility Implementation Plan

> **For agentic workers:** execute this plan task by task; preserve all unrelated working-tree changes.

**Goal:** Restore the legacy public TCP/5062 route for Yealink extension 1234 while keeping every other public phone on TCP/5061.

**Architecture:** Asterisk assigns endpoint 1234 a TLS 1.0 transport on VM TCP/5063. Docker publishes that port, the VM firewall accepts it only from the SIP edge/WireGuard peer, and the edge source rules forward public TCP/5062 to VM TCP/5063. PJSIP does not enforce that transport as inbound-only for endpoint 1234; another valid SIP credential could use the listener. The user explicitly authorized operating the existing external edge VPS for this task; install and enable the scoped edge service there, then verify phone registration and call acceptance.

**Tech Stack:** Asterisk PJSIP, Docker Compose, VM iptables, edge nftables.

**Spec:** User-approved request in this conversation; historical working configuration is retained in VM release `20261015T200914Z`.

## Global Constraints

- The user explicitly authorized deployment of the scoped SIP edge configuration on VPS `evita` (`94.102.89.13`) for this task. Preserve existing unrelated firewall rules and use the installer's ruleset backup.
- Keep 5061/TLS 1.2+ unchanged for all other endpoints.
- Keep VM TCP/5063 restricted to the SIP edge/WireGuard peer; document that the SIP listener is not inbound-isolated by extension.
- Preserve unrelated dirty Nateks changes and do not print credentials.
- Do not run product tests/builds on this workstation; build Asterisk only on VM209 through the remote release process.

## Review Focus

- Endpoint 1234 must keep the legacy transport across deploy and rollback.
- Other public endpoints must continue using transport 5061.
- VM 5063 must not be exposed beyond the edge/WireGuard source allowlist.
- Edge 5062 rules must include SYN rate/connection limits and survive edge reboot when installed.
- Do not claim phone registration until the edge route is live and Asterisk has a contact for 1234.

## File Map

- `conference/asterisk/pjsip.conf.template`: define the legacy TLS 1.0 listener assigned to 1234; do not describe it as inbound-isolated by extension.
- `deploy/update_pjsip_wss_transport.py`: persist endpoint 1234 on that transport.
- `deploy/compose.conference.yaml`, `deploy/firewall.sh`: publish and restrict VM TCP/5063.
- `deploy/sip-edge/voice-sip-edge.nft`: persist public TCP/5062 to VM TCP/5063 forwarding and limits.
- `deploy/remote-deploy-asterisk-sip.sh`, `deploy/rollback-asterisk-sip.sh`: verify the intended endpoint and make rollback preserve it.
- `docs/OPERATIONS.md`, `docs/YEALINK_1234_REGISTRATION_FIX.md`: record the route, reboot behavior, TLS 1.0 limitation, and live-edge blocker.

## Steps

1. Restore the dedicated PJSIP TLS 1.0 transport on VM container TCP/5063 and keep endpoint 1234 assigned to it during release synchronization and rollback.
2. Publish VM TCP/5063 and limit forwarded traffic to the existing SIP edge and WireGuard peer; preserve 5061 unchanged.
3. Add public edge TCP/5062 DNAT/SNAT to VM TCP/5063 with the existing TLS handshake rate and connection limits.
4. Update operational and Yealink documentation to distinguish source readiness from live registration.
5. Deploy on VM209 and VPS `evita`; verify container health, both TLS listener handshakes, active PJSIP transport, VM port publication, VM firewall allowlist, enabled edge service, public TCP/5062 forwarding, and zero active calls. Then verify Yealink `1234` registration and complete the required call acceptance before reporting completion.

## Verification

- `git diff --check` -> no whitespace errors.
- Remote VM status -> healthy Asterisk, endpoint 1234 on legacy transport, VM TCP/5063 published and source-restricted, zero active calls.
- VPS `evita` SSH alias and dedicated key -> passed, including login after a real reboot.
- Edge installer backup -> `/var/backups/voice-sip-edge-20261016T104931Z`; unit `enabled/active` after reboot at `2026-10-16 10:49:51 UTC`; public TCP/5062 TLS 1.0/AES128-SHA handshake -> passed.
- VM209 Asterisk health, 5061 and 5063 listeners, firewall allowlist, and zero active calls -> passed; AOR `1234` still has no contact and Yealink `.133` still shows `Register Failed`.
- The handset account page has registrar 5061, outbound proxy 5062, TLS, and auth name 1234. The browser accessibility snapshot does not expose username/password values; no handset credentials were changed. Owner must resubmit the account once, then verify contact and complete call acceptance.
