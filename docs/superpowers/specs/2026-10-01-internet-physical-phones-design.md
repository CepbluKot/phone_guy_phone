# Internet access for physical SIP phones

## Goal

Allow the existing physical phones to register with the private Asterisk service
from an ordinary Internet connection, without installing or joining WireGuard on
the phone. Preserve extension identity, Go call routing, voice profiles, browser
calling, and the private admin boundary.

## Existing constraints

- Asterisk runs on VM209 at `192.168.20.70`; the public edge is the existing VPS
  at `94.102.89.13` and reaches the home LAN over WireGuard.
- `phone.awesomeio.ru` already resolves to the public edge and has a valid
  Let's Encrypt certificate there.
- At task start, Asterisk had UDP/5060 and RTP/10000-10019 published only on
  the VM's LAN address. Browser signaling remains private WSS and browser media
  uses the existing TURN service.
- Asterisk endpoints are the trusted calling identity used by Go. Public access
  must keep the existing extension IDs and must not trust caller ID.
- Extension 1983 currently has a fixed LAN contact; its Internet registration
  requires a controlled switch to a dynamic contact.
- User data, SIP passwords, and private keys must never enter Git or logs.

## Selected design

1. Add an Asterisk PJSIP TLS transport on TCP/5061. The TLS listener terminates
   on Asterisk, so the handset validates the existing `phone.awesomeio.ru`
   certificate end to end. Extend the current forced-command certificate export
   to return only that certificate/key pair in addition to the existing LAN
   certificate pair. VM-side sync validates SAN, expiry, and key match and writes
   root-controlled files readable only by the Asterisk container identity.
2. Require SDES-SRTP for physical PJSIP endpoints. Signaling encryption alone is
   not sufficient for Internet voice. The phone's TLS connection protects SIP
   credentials and SDP; SRTP protects the audio. Do not allow plaintext RTP as a
   fallback. The migration must update handset settings before exposing the
   public route because the endpoint security policy applies to its calls on
   both LAN and Internet transports.
3. Publish Asterisk TCP/5061 on the VM LAN address, protected in Docker's
   `DOCKER-USER` chain so only the public VPS can reach it. Keep UDP/5060
   private. Preserve the current RTP range for LAN, VPN, and VPS sources; drop
   other forwarded RTP traffic at the VM boundary.
4. On the VPS, use a separate nftables ruleset with exact DNAT for TCP/5061 and
   UDP/10000-10019 to VM209. SNAT forwarded packets to the VPS WireGuard
   address so Asterisk replies return through the public edge and existing
   connection tracking maps them back to the handset. Rate-limit new SIP TLS
   connections per source before SNAT and bound RTP packet rate. Do not change
   existing TURN, HTTPS, WireGuard, or unrelated firewall tables.
5. Enable the edge only after the TLS transport, certificate, VM publication,
   strict SRTP policy, firewall, and zero-call preconditions are ready. The user
   explicitly authorized the narrow public route. The route is enabled after
   those server-side gates; handset configuration and the `1983` dynamic-contact
   migration remain separate client-side steps. No public UDP/5060, Go HTTP,
   ARI, RVC, or admin route is added.

## Phone settings at activation

- SIP server/registrar: `phone.awesomeio.ru`
- Port: `5061`; transport: TLS; certificate verification enabled
- User/auth ID: the phone's current assigned extension
- Password: the existing SIP password already configured on the phone
- Media security: SRTP/SDES required; no insecure fallback
- NAT keepalive/registration refresh enabled; no VPN required

Do not publish the passwords in this document. Device menu labels vary by
firmware. Confirm the phone offers TLS and SDES-SRTP before moving it. If a
handset cannot enforce SRTP, it must not be enabled on the public route.

## Failure handling and rollback

- Before any VPS mutation, create a dated backup of the affected firewall,
  systemd, and forced-command files. The VPS firewall feature uses separate
  table names so it can be removed without touching TURN rules.
- Before any Asterisk restart, require zero active channels and save the exact
  Go/Asterisk compose release, rendered PJSIP config, and firewall state.
- If TLS, registration, audio, voice conversion, or teardown fails, close the
  public edge rules first. Restore the previous Asterisk runtime/image and
  restore the 1983 static contact if it was changed. Existing HTTPS/TURN and
  private browser calls remain separate from the SIP edge.
- Certificate renewal must validate the new pair before replacing the current
  files; reload/restart only with no active calls and leave the prior pair for
  rollback.

## Acceptance gates

1. Static checks: Asterisk builds with TLS transport support; the loaded
   transport uses TLS 1.2 or newer, the expected certificate paths and public
   signaling/media addresses; physical endpoints require SDES-SRTP; UDP/5060
   is not published publicly; only the stated ports appear in edge NAT rules.
2. Deployment checks: certificate bundle contains only the approved files;
   certificate SAN, expiry and private-key match pass; Docker and nftables
   configuration validate; VM firewall blocks non-edge TCP/5061 and rejects
   RTP outside LAN/VPN/VPS source ranges.
3. Public scan: TCP/5061 completes TLS with the expected domain certificate;
   public UDP/5060, ARI, Go HTTP and admin remain unreachable; only the intended
   RTP range is forwarded.
4. Live handset acceptance from a non-VPN network: registration; browser-to-
   handset and handset-to-browser calls; two-way audio; selected voice profile
   conversion; hangup from either side; zero leftover Asterisk channels.
5. Repeat the mandatory browser-to-browser acceptance to detect regressions.

The server-side Internet route is deployed and enabled. The physical-phone
feature is not acceptance-complete until a handset registers and completes
two-way calls from a non-VPN network. A workstation scan currently reports
TCP/5060 open through its transparent fake-IP proxy even though the VPS drops
the arriving SYNs and has no listener or NAT for that port; verify again from a
non-proxied network before concluding the outside scan gate.
