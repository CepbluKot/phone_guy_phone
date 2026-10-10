# Yealink T21 registration as extension 1234 — 2026-10-10

## Symptom

The Yealink T21 at `192.168.20.36` showed a registration error for account
`1234`. Asterisk had no PJSIP contact while the handset was attempting to
register; the targeted SIP trace did not see a `REGISTER` arrive.

## Cause and fix

This legacy handset negotiates TLS 1.0, while the public registrar
`phone.awesomeio.ru:5061` requires TLS 1.2 or newer. It first registered through
the private proxy `192.168.20.70:5062`. To make it work through the internet,
the VPS now exposes `phone.awesomeio.ru:5062` and forwards it to the separate
TLS 1.0 listener on VM TCP/5063. The private VM listener on 5062 and public
TLS 1.2 listener on 5061 remain unchanged. On this handset, enable
**Use Outbound-server** and set it to `phone.awesomeio.ru:5062`; keep the
registrar at `phone.awesomeio.ru:5061` with TLS.

The final handset-side fix was to add the official ISRG Root X1 certificate to
the Yealink trusted-certificate store, then save the account settings again.
Certificate and server-name verification remain enabled. After that change the
handset registered as `1234`.

## Why calls still failed after registration

The live Go call router also checks its assigned-phone allowlist. Its phonebook
was at revision 3 and did not contain the handset at `192.168.20.36` or
extension `1234`, even though Asterisk and the voice-route map knew about
`1234`. That made calls from or to this physical extension fail endpoint
resolution. The handset was added to the phonebook as `1234` (revision 4); the
runtime store updates immediately, so no Go restart was needed. The initial
phonebook seed now includes the same assignment for fresh setup.

The next live call trace showed a separate handset media mismatch: the T21
offered `RTP/AVP` without encryption, while the Asterisk endpoint required
`RTP/SAVP` with SDES. Asterisk returned `488 Not Acceptable Here`, and the
handset also rejected Asterisk's secure offer with `488`. This firmware's
Advanced page has no SRTP setting. On 2026-10-10, the owner explicitly approved
plain RTP for extension `1234` only. The PJSIP endpoint now sets
`media_encryption=no`; other physical endpoints continue to require SDES-SRTP.
Voice for `1234` is unencrypted in transit.

The trace also showed Asterisk advertising its Docker address (`172.19.0.2`) to
the LAN handset. Release `20261010T103927Z` removes the handset LAN from the
private transport's `local_net`, allowing `external_media_address` to advertise
the VM's published media address. The live transport now lists only the Docker
network in `local_net`; a post-change call has not yet confirmed the SDP address.

## Decline when the callee answered

After the RTP exception, the call rang but was declined at connection time.
Between 11:46 and 11:48 UTC, `voice-go` logged four
`call connection failed (rvc_unavailable): open RVC stream:
rvc_model_unavailable` errors. The route for `2015` uses the `phone-guy` profile,
which requires the RVC worker before bridging; this failure was separate from
the SIP registration and RTP negotiation fixes above.

RVC `/healthz` returned `503 model_unavailable`. VM209 had loaded NVIDIA kernel
module `580.173.02` while the installed driver files were `580.178.04`; `nvidia-smi`
reported an NVML version mismatch and startup logged CUDA error 804. With no
active calls, VM209 was rebooted at 11:50 UTC. After reboot, the loaded module
matched `580.178.04`, the GTX 1050 Ti was visible, RVC health returned `ready`,
and Go/Asterisk returned healthy. The physical retry, two-way audio and hangup
after reboot still need acceptance; the reboot alone does not prove the call is
fixed.

## Verification and limits

On 2026-10-10, live TLS handshakes to `phone.awesomeio.ru:5062` using TLS 1.0
and to `phone.awesomeio.ru:5061` using TLS 1.2 both passed certificate and
hostname verification. VM209's Asterisk showed a dynamic `1234` contact from
`192.168.20.36` over TLS through the existing private path, and zero active
channels. The owner confirms that the handset registers as `1234`, and the
current Asterisk contact is present. Release `20261010T114014Z` applies
`media_encryption=no` only to `1234`; the other physical endpoints remain on
SDES-SRTP. The precise public-versus-private signaling path and a completed
two-way call/audio check remain unverified. No SIP password or certificate
private key is recorded here.
