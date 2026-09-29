# Personal access to the browser phone from the internet

Status: design only. The browser phone and admin remain private. Do not add a
public DNS record or Caddy route as part of the private-domain split.

## Recommended first step: a personal VPN peer

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

## If a public HTTPS hostname is later required

Use a separate public hostname for the phone only. Authenticate the owner at
the edge with a revocable identity and MFA before serving `/phone/` or its API.
The current nickname and extension form is not authentication. Keep the admin
private. Replace the hardcoded private signaling URL and SIP domain with a
publicly reachable, authenticated WSS route. Add a TURN relay for WebRTC media
and configure ICE to use it; the current Asterisk host candidates and RTP
ports are private. Restrict both signaling and TURN credentials to the signed-in
owner, then verify that a logged-out client cannot claim an extension, register
SIP, or reach media ports. Make the public route only after these gates pass.

The VPN path is much smaller and works with the current SIP and media topology.
