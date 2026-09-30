# Personal access to the browser phone from the internet

Status (2026-09-30): public phone access is approved but still gated. The public
`phone.awesomeio.ru` A record already points to the existing VPS; however, there
is no public Caddy route or Teleport Application Service for it, so the phone
is not available from the internet. The Go release contains the public WSS and
TURN contracts, but its public phone config returns 503 while the TURN secret
is absent. The public Caddy route remains disabled until the owner-only
Teleport role, mandatory MFA enrollment, app registration, TURN, and external
acceptance are verified. The admin remains private.

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

Use `phone.awesomeio.ru` for the phone only. Authenticate the owner at the edge
with a revocable Teleport identity and MFA before serving `/phone/` or its API.
The current nickname and extension form is not authentication. Keep the admin
private. Replace the hardcoded private signaling URL and SIP domain with a
publicly reachable, authenticated WSS route. Add a TURN relay for WebRTC media
and configure ICE to use it; the current Asterisk host candidates and RTP
ports are private. Restrict both signaling and TURN credentials to the signed-in
owner, then verify that a logged-out client cannot claim an extension, register
SIP, or reach media ports. Make the public route only after these gates pass.

The implementation plan and security gates are tracked in
[`superpowers/plans/2026-09-30-public-browser-phone-access.md`](superpowers/plans/2026-09-30-public-browser-phone-access.md).
