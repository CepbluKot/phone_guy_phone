# Browser Softphone and Internal Calling

Status: implemented and deployed to VM209 in release `20260926T180933Z`.
Private browser SIP registration was verified. Real call/media acceptance remains pending.

## Goal

Allow a person to call an enrolled physical SIP phone from the private website,
and allow that physical phone to call the person in the browser. Each person is
listed by a nickname and an existing internal SIP extension. When that
extension is called, Asterisk rings its physical phone and its active browser
client at the same time; the first device to answer takes the call.

## Accepted decisions

- The browser phone is a WebRTC SIP client registered to the existing Asterisk
  instance. Do not add a separate PBX, PSTN carrier, or media gateway.
- The feature is for configured internal extensions only. No external phone
  numbers, PSTN routing, or arbitrary SIP destinations.
- The browser phone is available at a separate `/phone/` page. The existing
  `/admin/` remains the administrative interface.
- A person enters a nickname and a configured internal extension. There is no
  invitation code or ownership challenge in this first version. Any VPN user
  may claim an eligible extension when it has no active browser session.
- A nickname is associated with one configured extension. The person may
  update it when starting a later browser session, as long as that extension
  has no current browser session. Nickname and extension are not treated as
  authentication credentials.
- Browser attachment is temporary. At most one active browser client may be
  attached to an extension. A configured physical handset does not prevent the
  browser from attaching. A second browser client is rejected while the first
  is active. Closing or losing the browser session releases the attachment;
  Go renews the lease every 10 seconds and expires an unreachable session after
  30 seconds. A clean WebSocket disconnect releases it immediately.
- The browser receives only session-scoped WebRTC SIP credentials, never ARI
  credentials or physical-handset SIP passwords. Session credentials and
  signaling secrets are not persisted in browser storage or logs. The
  implementation must revoke the session registration before another browser
  client can claim that extension.
- All calls continue through the Go call controller and Asterisk ARI. The
  browser must not originate arbitrary endpoints or bypass voice routing.
- Resolve the voice profile by the caller's configured logical extension, for
  both physical and browser clients. `phone-guy` audio is fail-closed through
  RVC; raw source audio must never be bridged around an RVC failure. `original`
  remains intentionally unprocessed. Profile edits affect new calls only.
- Preserve the enforced limit of one simultaneous RVC-processed call. Do not
  change capacity based on telemetry or browser registration count.
- Do not record calls or retain PCM audio or transcripts. Keep the service
  private to the current VPN/Caddy boundary; do not expose SIP, RTP, ARI, or RVC
  to the public internet.

## Current repository facts

These are source observations, not claims about current live deployment:

- The application already has a Go ARI call controller and a React admin app.
  The controller currently originates one PJSIP peer and resolves callers
  against configured numeric extensions.
- `conference/asterisk/pjsip.conf.template` defines a UDP PJSIP transport and
  physical endpoints. The shared endpoint template has `ice_support=no`; the
  source `http.conf` binds to loopback, while the deployed container's HTTP
  process binds inside its container and Docker publishes that port only on
  host loopback. No PJSIP WebSocket/WebRTC endpoint is configured.
- The deployed Asterisk 22.11.0 container currently has no
  `/etc/asterisk/sorcery.conf` and does not load
  `res_pjsip_transport_websocket`; ARI dynamic PJSIP object creation is not
  enabled until a volatile memory mapping is added for browser auth/AOR/
  endpoints while retaining the static `pjsip.conf` mappings.
  The browser's dynamic AOR ID must equal its SIP URI user so Asterisk resolves
  REGISTER to the intended AOR.
- Asterisk's RTP range is `10000-10019/udp`; the Compose deployment publishes
  that range only on the private VM address. The browser media path must remain
  reachable only through the existing VPN/LAN routes.
- The existing `chan_websocket` media support carries PCM for current media
  features. It does not by itself register a SIP/WebRTC browser endpoint.
- The phonebook and voice profiles already use configured extension identities.
  Unknown extensions are rejected by the call router.
- The admin currently has a passwordless mode by explicit user request. It is
  reachable only within the private network boundary; this feature does not
  add public access or treat a nickname as proof of identity.

## User and call flows

### Browser session

1. The user opens `/phone/`, enters a nickname and an extension from the
   configured internal extension inventory, and grants microphone permission.
2. The Go service validates the extension and atomically acquires its browser
   session lease. If another browser lease is active, the request fails with
   an “already connected” status. The service does not accept arbitrary
   endpoint IDs supplied by the browser.
3. Go provisions or authorizes a session-scoped WebRTC SIP registration for a
   dedicated browser endpoint associated with that logical extension. The
   browser connects to Asterisk over WSS and uses DTLS-SRTP media. No physical
   phone credentials are reused.
4. The active browser session is visible in the call UI and admin status. The
   browser connection remains present while the user is available to receive
   calls. Closing the page or losing its signaling connection releases the
   lease. Go renews the lease every 10 seconds and expires an unreachable
   session after 30 seconds. A clean WebSocket disconnect releases it
   immediately.
5. A user may call a selected nickname or internal extension from the browser.
   Only directory entries backed by configured extensions are callable.

### Call delivery

- Physical-to-browser: the physical phone dials the configured internal
  extension. Go resolves the source endpoint to its logical extension and
  voice profile, then originates the configured target devices through ARI.
- Browser-to-physical: the browser SIP call enters the same trusted Go call
  controller. Its session maps to the logical extension and profile; the
  selected internal target is routed through the existing controller.
- When a target has a physical endpoint and an active browser endpoint, the
  controller originates both. The first answered endpoint is connected; Go
  cancels and cleans up the other leg. If no browser session is active, only
  the physical phone is rung. If neither target is available, the call fails
  with a visible status.
- The route snapshots source and destination identities and the source profile
  at admission. Neither caller ID text nor a browser-provided endpoint name is
  an authorization identity.
- A call requiring `phone-guy` processing is rejected if the controller, RVC,
  or media validation fails. There is no raw-audio fallback. `original` calls
  use the explicit unprocessed profile.

## Components and contracts

### React browser phone

- Provide nickname and internal-extension entry, microphone permission and
  device status, a contact list, outgoing call, incoming call accept/decline,
  hang-up, and clear states for connecting, ringing, connected, busy, and
  failure.
- Show whether the browser session is registered and able to receive calls.
- Keep browser signaling credentials in memory only; clear them on logout or
  disconnect. Never expose ARI credentials or allow the browser to choose raw
  Asterisk channel names.
- Do not retain captured or received audio after the active call/session.

### Go application and call controller

- Add a same-origin session API for claiming, renewing, and releasing one
  browser lease per configured extension. Lease acquisition must be atomic
  across concurrent requests. A disconnected lease is not reusable until its
  Asterisk WebRTC contact and owned channels have been revoked or confirmed
  gone.
- Keep profile/directory persistence versioned, validated, secret-free, and
  atomically updated. Nicknames are display labels; extension is the unique
  routing key.
- Add explicit logical identity mapping from each physical/browser PJSIP
  endpoint to one configured extension. Unknown identities or mismatches are
  rejected.
- Extend the controller from one peer to a controlled set of target legs for
  parallel ringing. It must choose one winner, cancel remaining legs, close
  bridges/media, release the existing processing lease exactly once, and
  expose call state to the UI.
- Provide the browser with only the temporary credentials and signaling data
  for its own active session. Keep ARI and RVC secrets server-side.
- Keep all origins, methods, call targets, codecs, channel names, and extension
  mappings allowlisted. Keep API same-origin, VPN-only, and protected by the
  existing strict Origin controls where applicable.

### Asterisk and network

- Add the required PJSIP WebSocket transport and WebRTC endpoint behavior for
  the configured extensions, including ICE and DTLS-SRTP. Do not alter physical
  handset credentials or provisioning.
- Ensure the SIP-over-WebSocket path is proxied through the private HTTPS site
  to the loopback-only Asterisk HTTP listener without exposing ARI. Preserve
  the listener's loopback restriction and the existing private TLS boundary.
- Ensure browser RTP/ICE reaches only the existing private Asterisk address
  and the restricted RTP range from VPN/LAN clients. Do not open public SIP or
  RTP firewall rules. If the private path cannot be made reliable with the
  current network topology, stop and revise the design before deployment.
- Asterisk must deliver both device legs to the trusted Go controller. There
  must be no native dialplan bypass to a direct, unprocessed endpoint bridge.

## Failure handling and privacy

- Reject an unknown or unconfigured extension, malformed nickname, duplicate
  active browser lease, unsupported WebRTC registration, or stale lease claim.
- If the browser loses signaling, mark it unavailable and release it after
  the bounded expiry. The phone remains independently registered and callable.
- If one target leg answers, cancel the other even if its answer races with
  the winner event. Hang-up from either side tears down all owned legs and
  resources.
- On RVC or audio-path failure for a processed profile, terminate the call;
  never connect the raw source as a fallback.
- Return a temporary SIP credential only in the active browser-session claim
  response over HTTPS. Do not include it in directory/admin API responses,
  persistent browser storage, or logs. Never send ARI, RVC, admin, or
  physical-handset SIP secrets to the browser.
- Do not persist call audio, transcripts, or detailed sensitive signaling
  payloads in the directory, API responses, browser storage, or logs.
- Existing internal calls that do not select this browser client retain their
  configured routing behavior; no external dialing or unrelated endpoint is
  introduced.

## Verification required before release

The implementation plan must include automated contract/unit coverage and a
private end-to-end acceptance run. Do not claim completion from source checks
alone. Verify at minimum:

1. Browser registration and clean release, plus bounded cleanup after abrupt
   page/network loss.
2. A second browser cannot claim an extension while its first browser lease is
   active; the physical phone remains available as the other device.
3. A physical phone can call an active browser session and a browser can call a
   physical phone using internal extensions only.
4. When both devices are registered, both ring, exactly one answers, and the
   losing leg is canceled and cleaned up.
5. Browser and physical calls use the configured source extension profile;
   `phone-guy` output contains processed speech only and fails closed if RVC is
   unavailable. `original` remains intentionally unprocessed.
6. Microphone denial, no audio route, busy target, stale lease, signaling loss,
   call cancellation, and hang-up produce bounded cleanup and clear UI errors.
7. The one-processed-call limit remains enforced, no audio is recorded or
   retained; ARI/RVC/admin/physical-phone secrets are absent from browser
   traffic and logs; only the temporary browser SIP credential is returned by
   the active-session claim response.
8. The WSS and RTP paths work through the private VPN/Caddy topology; public
   SIP, RTP, ARI, and RVC remain inaccessible.

## Non-goals

- PSTN, carrier trunks, external numbers, public access, or arbitrary SIP URIs.
- Persistent browser pairing or browser credentials stored by the browser.
- Invitation codes, OTP, per-person account passwords, or extension ownership
  verification in this first version.
- More than one active browser per extension, multiple simultaneous processed
  calls, changing physical SIP credentials, handset provisioning, or recording.
- Increasing the enforced processing limit based on a capacity estimate.

## Implementation risk to resolve in the plan

The current source has no PJSIP WebRTC transport or browser endpoint, and the
Go controller originates one peer at a time. Before broad implementation,
validate a narrow, reversible Asterisk 22.11 path for session-scoped browser
registration, lease revocation, private WSS proxying, and parallel target-leg
control. Use only a constrained Go/Asterisk interface; do not grant the Go
container a Docker socket, arbitrary shell access, or a public ARI listener. If
the session registration cannot be revoked before lease reuse, stop and return
with the concrete constraint and a revised design option.
