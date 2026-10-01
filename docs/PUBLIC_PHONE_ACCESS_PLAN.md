# Personal access to the browser phone from the internet

> **Implementation update (2026-09-30):** this plan's proposed Teleport/MFA
> route was replaced by the owner's explicit request to bypass Teleport. The
> live route uses VPS Caddy's phone-only path allowlist, a branded Go login
> with a persistent HttpOnly cookie, and
> authenticated TURN. Current state and checks: [LIVE_STATUS.md](LIVE_STATUS.md)
> and [OPERATIONS.md](OPERATIONS.md).

Status (2026-09-30): public phone access is deployed. The admin remains private.

## Existing private access

Enroll the owner's phone or laptop as its own revocable WireGuard peer. Route
`lan.awesomeio.ru`, `192.168.20.70`, and the voice VM's RTP range through the
VPN; use `10.19.87.1` as the private DNS resolver. Open
`https://voice-phone.lan.awesomeio.ru/phone/` in the device browser. This gives
access while away from home without exposing the phone, signaling, RTP, or the
passwordless admin to unauthenticated internet clients. A separate peer per
device lets the owner revoke one device without changing the others.

Before treating this as available, verify from a cellular network that the
device resolves the private names, loads the phone page, reaches the configured
`wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling` endpoint, registers,
completes a call in each direction, passes audible audio in both directions,
and clears Asterisk channels after hangup. Check the VM firewall and VPN
routes for UDP `10000-10019` as well as HTTPS. A successful page load alone
does not prove that WebRTC media works.

## Approved public route

The Go app authenticates the owner with a password and persistent signed cookie.
Keep the admin private. Public signaling and TURN credentials are restricted to
this signed-in phone origin; verify a logged-out client cannot claim an
extension, register SIP, or reach media ports.

The implementation plan and security gates are tracked in
[`superpowers/plans/2026-09-30-public-browser-phone-access.md`](superpowers/plans/2026-09-30-public-browser-phone-access.md).
