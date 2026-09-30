# Public browser-phone access design

## Goal

Allow the owner to use the browser phone from the public internet while keeping
the admin UI, SIP secrets, ARI, RVC, and direct Asterisk signaling/media ports
private.

## Approved access shape

- Publish only `phone.awesomeio.ru` through the existing public HTTPS edge.
- Use Teleport Application Access with a dedicated local account and a role
  that grants only the `voice-phone` application. Require password and TOTP.
- Keep `voice-admin.lan.awesomeio.ru` private. Do not reuse the broad owner or
  colleague Kubernetes roles for this app.
- Enroll a Teleport Application Service on the voice VM. It makes an outbound
  authenticated connection to the existing Teleport proxy and serves only the
  phone origin. Do not open a new inbound VM port.
- The public phone origin serves `/phone/`, the phone API, shared static assets,
  health, and the authenticated `/ws/phone-signaling` WebSocket. The Go service
  proxies signaling to Asterisk loopback. No other app/admin paths are exposed.
- Route browser media through an authenticated TURN relay on the existing VPS.
  Issue time-limited TURN credentials only from the phone API after Teleport
  authentication. Restrict TURN relay peers to the Asterisk host and configured
  RTP range. Open only TURN listener/relay ports on the VPS; keep Asterisk RTP,
  SIP, ARI, and RVC listeners private.
- Add `phone.awesomeio.ru` as one explicit public DNS A record to the existing
  VPS. Do not publish any `.lan` names or private IPs.

## Security and acceptance gates

1. Anonymous browser requests to the public phone origin redirect to Teleport;
   phone APIs and WebSocket cannot claim or register an extension anonymously.
2. A dedicated phone account can access the phone app only; it cannot access
   admin, Kubernetes, or other Teleport apps. TOTP enrollment is completed.
3. CSP, exact origin validation, secure cookies, and `no-store` remain enforced.
4. TURN credentials expire, fail without authentication, and cannot relay to
   arbitrary IP addresses or ports.
5. External scans confirm there is no public SIP, ARI, RVC, VM HTTP, or Asterisk
   RTP listener. Only intended HTTPS and TURN ports answer on the VPS.
6. From a non-VPN network, the owner completes registration and calls in both
   directions with audible media, hangup cleanup, and the configured voice
   processing. Anonymous and unauthorized attempts are rejected.

Do not activate the public route until all gates pass. If a gate fails, retain
the private service and roll back public DNS/edge/firewall changes.
