# Internet physical SIP phones implementation plan

> **Execution status (2026-10-01):** the server-side gates passed and the narrow
> public edge is enabled. The browser-to-browser acceptance rerun is currently
> incomplete after a call stalled before Asterisk; a real off-VPN handset test
> also remains. See `docs/BROWSER_CALL_ACCEPTANCE.md` and `docs/LIVE_STATUS.md`.

**Goal:** Prepare secure direct Internet registration for the existing hardware
phones, keeping Asterisk private behind the current WireGuard VPS and preserving
the Go/browser call paths.

**Architecture:** Asterisk terminates SIP TLS on TCP/5061 using the existing
`phone.awesomeio.ru` public certificate, requires SDES-SRTP on physical
endpoints, and advertises the VPS address for public media. The VPS applies
isolated nftables DNAT/SNAT for SIP and the existing small RTP range. A VM
Docker ingress chain allows SIP only from the VPS and RTP only from LAN/VPN/VPS
sources. Certificate sync and edge firewall are separate, reversible services.

**Tech stack:** Asterisk 22 / PJSIP, Docker Compose, Go call controller,
nftables, iptables `DOCKER-USER`, systemd, shell/Python deployment helpers.

---

## File map

- `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`: build,
  load, and require the TLS transport module.
- `conference/asterisk/pjsip.conf.template`: separate public TLS transport and
  strict physical endpoint media-encryption policy.
- `deploy/compose.conference.yaml`: publish the private VM TCP/5061 socket and
  mount `/etc/voice-certs/phone-sip` read-only at `/run/voice-tls`; Asterisk
  reads both TLS files through its `current` symlink.
- `deploy/firewall.sh`: permit TCP/5061 only from the VPS; restrict published
  RTP to existing trusted LAN/VPN/VPS sources without changing HTTP rules.
- `deploy/export-cert.sh`, `deploy/install-vps-export.sh`: narrowly extend the
  existing forced export to include the phone-domain certificate/key.
- `deploy/sync-sip-cert.sh`, `deploy/voice-sip-cert-sync.{service,timer}`:
  validate and atomically stage the certificate on VM209, without outputting
  key material.
- `deploy/validate_sip_cert_bundle.py`: validate exact tar members, certificate
  SAN/expiry, and matching private key before extraction.
- `deploy/sip-edge/`: isolated VPS nftables config, apply/disable scripts, and
  systemd unit. Installation validates and backs up but leaves forwarding
  disabled until a deliberate `enable` action.
- `deploy/prepare-internet-phones.sh`: preflight and package/validate the VM
  preparation without enabling public NAT.
- `tests/test_internet_sip_security.py`: regression checks for port scope,
  security requirements, certificate export scope, and disabled-by-default edge.
- `docs/OPERATIONS.md`, `docs/LIVE_STATUS.md`,
  `docs/PUBLIC_PHONE_ACCESS_PLAN.md`: operating instructions, prerequisites,
  migration steps, and accurate current status.
- `docs/BROWSER_CALL_ACCEPTANCE.md`: append the fresh browser-to-browser result
  after any deployed project update.

## Task 1: Add TLS/SRTP Asterisk support

**Paths:** `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`,
`pjsip.conf.template`, `deploy/compose.conference.yaml`.

1. Add the Asterisk TLS transport module to the pinned build, load list, and
   health check; keep existing UDP/WSS transport definitions unchanged.
2. Add a TLS transport bound on 5061, TLS 1.2 or newer, certificate file paths
   fixed at `/run/voice-tls/current/fullchain.pem` and
   `/run/voice-tls/current/key.pem`, external signaling/media address
   `94.102.89.13`, external signaling port 5061, and local networks limited to
   the actual VM LAN and Asterisk Docker bridge. Exclude the WireGuard source
   `10.19.87.1` so external SDP is rewritten to the public address.
3. Require `media_encryption=sdes` for physical endpoints and do not set
   `media_encryption_optimistic`. Keep browser WebRTC DTLS-SRTP endpoint config
   untouched.
4. Publish only `192.168.20.70:5061:5061/tcp`; retain private UDP/5060 and the
   existing RTP range. Mount only the phone-specific certificate directory
   read-only.
5. Add tests that assert the exact transport, strict SRTP, current browser
   transport, and port publication.

**Verification:** Asterisk image build; `asterisk-healthcheck`; rendered Compose
config; Go and browser unit/build suites; browser-to-browser live acceptance
after any Asterisk deploy.

## Task 2: Secure certificate delivery

**Paths:** `deploy/export-cert.sh`, `install-vps-export.sh`, new sync script and
systemd units.

1. Extend the root forced-command tar bundle by two fixed member names only:
   `phone-fullchain.pem` and `phone-key.pem`, read from the existing Caddy
   certificate store. Keep all user input ignored by the forced command.
2. Sync into a mode-0700 runtime directory; verify exact tar members, SAN,
   not-after horizon, and matching public/private keys before installing a
   versioned pair under `/etc/voice-certs/phone-sip/releases/` and atomically
   switching `current`.
3. Install the public certificate mode `0444` and key mode `0440` owned by
   `root:10001`, readable only by the Asterisk container identity. Delete all
   staged bundle/key copies on every exit. Add a disabled-by-default systemd
   timer/service for the SIP pair.
4. Ensure certificate changes are staged and reported as pending; reload the
   Asterisk TLS transport only when no channels are active. Roll back the
   `current` pointer if reload or TLS handshake validation fails. Do not expose
   key contents or certificate bundle data in output.

**Verification:** unit tests with synthetic certificates for wrong SAN, expired
certificate, mismatched key, malicious/extra tar members, and unchanged pair;
`shellcheck` if installed; timer syntax verification.

## Task 3: Restrict the VM published ports

**Paths:** `deploy/firewall.sh`, firewall tests.

1. Preserve the existing `VOICE_INGRESS` rule for Go HTTP unchanged.
2. Add a separate idempotent `VOICE_SIP_INGRESS` chain before Docker's accept
   rules: allow only TCP/5061 from `10.19.87.1`; allow UDP/10000-10019 from the
   home LAN, the configured VPN CIDR, and the VPS; drop other packets to those
   published ranges.
3. Do not alter UDP/5060 behavior or private Caddy/ARI/RVC bindings.

**Verification:** shell syntax; test chain order and exact source/port scope;
`iptables-restore --test` or isolated network namespace where available.

## Task 4: Install and enable the authorized VPS public SIP edge

**Paths:** new `deploy/sip-edge/*` scripts/config/systemd, plus static tests.

1. Create an independent IPv4 nftables table for public edge filtering and an
   independent NAT table. Forward only public TCP/5061 and UDP/10000-10019 to
   `192.168.20.70`; SNAT to `10.19.87.1` so Asterisk replies route back through
   the VPS conntrack path.
2. Rate-limit new TLS SYNs per source before SNAT and bound RTP packets per
   source and globally. Preserve established return flows.
3. Install/check scripts must create a dated backup before any live mutation,
   validate nft syntax, and support exact removal of only the feature-owned
   tables. Installation leaves the service disabled until the certificate,
   Asterisk TLS listener, VM firewall, and zero-call preconditions pass.
4. The user explicitly authorized public access for physical phones. Enable the
   forwarding service after these preconditions pass; keep handset registration,
   media, and profile conversion marked unverified until a handset connects
   from a non-VPN network.

**Verification:** `nft -c` on the complete rules file; idempotent apply/disable
test in a disposable network namespace if available; security test asserts no
UDP/5060/ARI/Go/admin rules and no default enable.

## Task 5: Migration and rollback runbook

**Paths:** operations/public-access docs and acceptance record.

1. Document phone settings: registrar `phone.awesomeio.ru`, port 5061/TLS,
   current extension/password, certificate verification, mandatory SDES-SRTP,
   NAT keepalive. Record that incompatible devices stay VPN-only.
2. Document 1983 static-contact removal only after the handset has been
   confirmed registered through the new path; preserve a backup for rollback.
3. Document phased activation order: strict handset TLS/SRTP settings, private
   server preflight, enable the public edge after server gates, then configure
   and test a handset from outside VPN, public scan from a non-proxied network,
   and operations/homelab logs. Keep 1983's static contact until its external
   registration has passed.
4. Document rollback: edge off first, restore Asterisk runtime and 1983 static
   contact, preserve certificate and browser service state.
5. State current activation status accurately: public forwarding is enabled
   only after server-side TLS/SRTP and firewall gates pass; real handset
   registration, calls, audio, and profile conversion require a non-VPN handset.

**Verification:** docs link and placeholder scan; all scripts and config checks;
fresh two-browser acceptance if runtime is changed. Internet handset media and
voice-profile conversion remain unverified until tested from a non-VPN phone.

## Review focus

- Asterisk transport changes require container recreation; confirm existing
  WSS/UDP transport and ephemeral browser endpoints still work after reload.
- Docker port publication bypasses UFW; verify Docker `DOCKER-USER` drops
  non-edge TCP/5061 and untrusted RTP sources.
- RTP on this deployment must traverse the public VPS in both directions; test
  Asterisk route to `10.19.87.1` and NAT state before enabling external phones.
- Verify 1983 no longer resolves to its stale static LAN contact after its
  dynamic registration is active; never remove it during a live call.
- Public certificate rotation must not interrupt a call or leave an invalid
  key/certificate pair installed.
