# Yealink T21 registration as extension 1234 — 2026-10-10

> **Порядок совместимости на 2026-10-16:** обычные публичные Yealink остаются
> на `phone.awesomeio.ru:5061` по TLS 1.2+. Для старого Yealink на номере
> `1234` настроен на отдельный маршрут Outbound Proxy TCP/5062 → VM TCP/5063
> с TLS 1.0. Это назначение в конфигурации endpoint, а не серверная
> изоляция: Asterisk может принять на этом listener другую корректную SIP-
> учётку. TLS 1.0 слабее основного TLS 1.2+ listener.
> Edge-правило установлено на VPS 2026-10-16 и пережило реальную перезагрузку;
> внешний TLS 1.0 handshake на 5062 прошёл. Asterisk пока не видит контакт
> 1234, поэтому регистрация и звонки всё ещё требуют приёмки.

## Symptom

The Yealink T21 at `192.168.20.36` showed a registration error for account
`1234`. Asterisk had no PJSIP contact while the handset was attempting to
register; the targeted SIP trace did not see a `REGISTER` arrive.

## Previously working compatibility route

At that time, the older T21 at `192.168.20.36` negotiated TLS 1.0 while the
public registrar `phone.awesomeio.ru:5061` required TLS 1.2 or newer. A
temporary compatibility route used public TCP/5062 and VM container TCP/5063.
That route previously allowed this phone to register as `1234`. It is prepared
in the VM release source and edge ruleset source. For the phone, keep the
registrar on `phone.awesomeio.ru:5061` and set Outbound-server to
`phone.awesomeio.ru:5062` with TLS. This applies only once the edge ruleset is
installed and active; the live public route is not yet confirmed.

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

## Registration failure and durable correction — 2026-10-16

The Yealink T21 at `192.168.20.133` showed `Register Failed` for account
`1234`. Asterisk listened on the published TCP/5061 TLS 1.2 transport, but its
runtime endpoint `1234` still selected the old TLS 1.0 transport on container
port `5063`; Docker did not publish 5063. The AOR had no registered contact.
The release updater copied this runtime endpoint forward but only synchronized
the shared transports, so the stale endpoint setting survived later releases.

Release `20261015T200914Z` briefly published the legacy VM TCP/5063 listener and
VPS TCP/5062 compatibility route, then was rolled back on 2026-10-16. The
current source assigns that transport to endpoint 1234, publishes VM TCP/5063,
and restricts it to the SIP edge/WireGuard source. This is not an inbound
extension restriction: any other valid SIP credential could register through
the same listener. On 2026-10-16 the edge rules were installed on VPS `evita`,
with public TCP/5062 forwarded to VM TCP/5063 under the existing TLS rate and
connection limits.

Every SIP release now pins endpoint `1234` to the dedicated
`transport-tls-1234-internet-legacy` transport and regenerates it from the
current template. Deployment and rollback checks require this endpoint on VM
TCP/5063. Compose and VM firewall settings are release-owned and survive a VM
reboot. Other public endpoints stay on TLS 1.2 TCP/5061.

The live Yealink Account page check on 2026-10-16 shows the account enabled
with `Register Failed`, registrar `phone.awesomeio.ru:5061`, Outbound Proxy
`phone.awesomeio.ru:5062`, and TLS selected. The registration name is `1234`.
The browser accessibility snapshot did not expose the `User Name` value; an
earlier screenshot showed `1234`, so do not overwrite the account or password
based on that snapshot alone. No handset settings were changed during this
check.

Asterisk logged a TLS `unsupported protocol` handshake through the edge before
the 5062 route was installed and no contact for AOR `1234`. That handshake did
not identify `.133` uniquely. After deployment, the public TLS 1.0/AES128-SHA
handshake succeeds, but the AOR still has no contact. The default listener
stays on TLS 1.2; only endpoint 1234 is configured to use the weaker TLS 1.0
listener, though inbound registration is not restricted by endpoint. Do not
keep changing phone ports or power-cycling the handset. Verify `User Name` is
`1234` and preserve the existing SIP password.

## Reboot persistence and acceptance

On VM209, `docker.service` and `voice-firewall.service` are enabled. The
firewall unit starts with Docker, and the Asterisk container uses the
`unless-stopped` restart policy. PJSIP runtime config lives on the VM under
`/opt/voice-go/releases/<release>/runtime/asterisk/pjsip.conf`, not in the
container's writable layer. The released Compose file publishes TCP/5061, the
restricted compatibility listener TCP/5063, and RTP/UDP 10000-10019.

The 2026-10-16 VM deployment is release `20261015T215053Z` (previous
`20261015T211534Z`). Asterisk is healthy, VM TCP/5063 is published and
source-restricted, the TLS 1.0 handshake succeeds locally, and there are zero
active calls. AORs `2015` and `1988` have contacts; AOR `1234` has none. From
this workstation, a direct-IP TCP probe to the public edge returned
`Connection refused` on TCP/5062; TCP/5061 completed TLS 1.2 with a valid
`phone.awesomeio.ru` certificate. The phone already has the public registrar
and proxy ports above; VM TCP/5063 is only the destination behind the public
edge. Its ruleset source has not been installed on the VPS, so changing phone
ports cannot restore registration. Enable the public TCP/5062 route first. A
container restart does not prove persistence across a VM reboot.

The edge systemd unit orders startup after WireGuard. Its apply action checks
the local route and nft syntax but does not depend on the separate Asterisk VM
being ready. The installer validates both TLS listeners before restart, enables
the unit, and restarts it so a previously active oneshot unit reloads new rules.
After the first reboot exposed an early-start race, the updated unit survived a
second real VPS reboot at `2026-10-16 10:49:51 UTC`; it was `enabled/active` and
the 5062 DNAT rule was present.

The final installer backup is
`/var/backups/voice-sip-edge-20261016T104931Z`. A public TLS 1.0/AES128-SHA
handshake to `94.102.89.13:5062` passed after reboot; 5061 and RTP rules remain
present. Asterisk still reports no contact for `1234`, and the Yealink UI still
shows `Register Failed`. The page shows registrar `phone.awesomeio.ru:5061`,
Outbound Proxy `phone.awesomeio.ru:5062`, TLS, and Register Name `1234`; the
browser accessibility snapshot did not expose User Name or Password values, and
the handset settings were not changed.

The phone's current Asterisk contact is still absent. Registration after the
server migration, a handset reboot, two-way audio, both hangup directions, and
the required browser-to-browser call acceptance remain unverified. Do not call
this integration complete until the call-acceptance record is updated.
