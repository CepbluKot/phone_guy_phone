# Yealink T21 registration as extension 1234 — updated 2026-10-16

> **Текущее состояние, 2026-10-16:** Yealink T21 `1234` зарегистрировался
> после того, как в обоих телефонных полях — SIP-сервер и Outbound-сервер —
> внешний порт исправили с `5063` на `5062`. Публичный TCP/5062 на VPS
> перенаправляется на Asterisk VM `192.168.20.70:5063`; внутренний `5063` не
> нужно вводить в телефон. Asterisk увидел контакт AOR `1234` по TLS, а владелец
> подтвердил, что телефон работает. SIP-пароль не менялся и не записывается
> в этот документ.
>
> Остальные публичные телефоны по-прежнему используют TLS 1.2+ на TCP/5061.
> Совместимый listener для `1234` использует TLS 1.0 и не обеспечивает
> изоляцию входящих учётных записей только по extension. Подробности, проверка
> и оставшиеся ограничения описаны в последнем разделе. Более ранние записи
> ниже являются историческими снимками состояния до окончательного исправления.

## Symptom

The Yealink T21 at `192.168.20.36` showed a registration error for account
`1234`. Asterisk had no PJSIP contact while the handset was attempting to
register; the targeted SIP trace did not see a `REGISTER` arrive.

## Historical compatibility route before the final handset correction

At that time, the older T21 at `192.168.20.36` negotiated TLS 1.0 while the
public registrar `phone.awesomeio.ru:5061` required TLS 1.2 or newer. A
temporary compatibility route used public TCP/5062 and VM container TCP/5063.
That route previously allowed this phone to register as `1234`. At an
intermediate stage the phone form used registrar TCP/5061 and Outbound-server
TCP/5062, but registration still failed. The live, owner-confirmed working
settings are recorded in the final correction section below: both the SIP
server and Outbound-server use the public port `5062` with TLS. The older
intermediate settings are retained here only as history.

An earlier troubleshooting entry recorded adding the official ISRG Root X1
certificate to the Yealink trusted-certificate store for a separate TLS
failure. That was not the root cause of the registration failure fixed below:
the final correction was to use public port `5062` in both SIP Server 1 and
Outbound Server. Certificate and server-name verification remain enabled.

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

The **pre-final-save** Yealink Account page check on 2026-10-16 showed the
account enabled with `Register Failed`, registrar `phone.awesomeio.ru:5061`,
Outbound Proxy `phone.awesomeio.ru:5062`, and TLS selected. The registration
name was `1234`.
The browser accessibility snapshot did not expose the `User Name` value; an
earlier screenshot showed `1234`, so do not overwrite the account or password
based on that snapshot alone. No handset settings were changed during this
check.

Before the final handset port save, Asterisk logged a TLS `unsupported
protocol` handshake through the edge and had no contact for AOR `1234`. That
handshake did not identify `.133` uniquely. After server deployment, the public
TLS 1.0/AES128-SHA handshake succeeded, but the AOR still had no contact. The
default listener stays on TLS 1.2; only endpoint 1234 is configured to use the
weaker TLS 1.0 listener, though inbound registration is not restricted by
endpoint. At that pre-final stage, the next check was to verify `User Name`
`1234` and preserve the existing SIP password; the final correction below
identified and fixed the phone's public port values.

## Reboot persistence and pre-final-handset snapshot — 2026-10-16

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

At this pre-final-handset snapshot, Asterisk still had no contact for `1234`.
The next section records the subsequent handset-side correction and successful
registration. Two-way audio, PSTN calls, and the separate browser-to-browser
acceptance were not tested as part of that registration correction.

## Final handset correction and registration acceptance — 2026-10-16

### Root cause

The public edge exposes TCP/5062 and forwards it to the Asterisk VM's legacy
TLS listener on TCP/5063. An earlier failed page snapshot had registrar
`5061` and Outbound-server `5062`; the later screenshot immediately before the
successful save showed `5063` in both fields. The saved working values are now
`5062` in both fields. `5063` is the **internal destination port**; there is
no public TCP/5063 forwarding rule for the handset. The phone therefore had
an incorrect public destination even though the VPS-to-VM forwarding route
and Asterisk listener were ready.

The final successful route is:

```text
Yealink T21
  -> phone.awesomeio.ru:5062/TLS (public edge)
  -> VPS nftables DNAT/SNAT
  -> 192.168.20.70:5063/TLS (Asterisk legacy listener)
```

Port `5062` is the phone-facing public port. Port `5063` is only the private
backend destination. Do not put `5063` in the Internet-facing Yealink form.

### Working Yealink Account 1 values

| Field | Value |
| --- | --- |
| Account / Line Active | Enabled |
| Label / Display Name | `1234` |
| Register Name | `1234` |
| User Name | `1234` |
| Password | Existing SIP password; preserved, not documented or rotated |
| Use Outbound Server | Enabled |
| Outbound Server | `phone.awesomeio.ru` |
| Outbound Server Port | `5062` |
| Transport | TLS |
| SIP Server 1 | `phone.awesomeio.ru` |
| SIP Server 1 Port | `5062` |
| Registration Period | `3600` seconds |
| NAT / STUN | NAT disabled; STUN host blank (as shown in the working form) |
| SIP Server 2 | Blank |

The registrar and proxy both use public TCP/5062 so REGISTER traffic from this
legacy handset is delivered to the TLS 1.0 compatibility listener. The server
port on the handset is not the same value as Asterisk's internal listening
port. No SIP credentials were changed during this correction.

### Verification evidence

- Before saving the corrected ports, `pjsip show contacts` had no contact for
  AOR `1234` and the Yealink page showed a registration error.
- After changing both handset ports to `5062` and saving, the VPS edge's
  accepted TCP/5063 counter increased from 282 to 305 packets during the
  verification window. This is consistent with traffic reaching the forwarded
  Asterisk listener; the counter is runtime telemetry and will reset when its
  ruleset is reloaded.
- Asterisk then listed a dynamic TLS contact for AOR `1234`, observed as
  `sip:1234@192.168.20.12:<dynamic-port>;transport=TLS`. The first post-save
  read showed source port `3515`; a later read showed `1084`, so this port is
  ephemeral and can change on re-registration. The observed `.12` address is
  the contact address stored by Asterisk on the private network; it is not a
  reason to replace the handset's public server name or port.
- Asterisk reported zero active calls at the time of the check. The owner then
  confirmed: “ура работает”.
- Immediately after the handset save, its web account page remained on the
  “Пожалуйста, подождите…” overlay. After the later VM reboot, the page showed
  `Зарегистрирован` (see the reboot recovery record below).

### Persistence and safe recovery

The handset account values are saved on the phone by the Yealink Account page.
The edge route is owned by the enabled `voice-sip-edge.service`; it survived a
real VPS reboot on 2026-10-16. On VM209, the Asterisk runtime is release-owned,
the Compose file publishes TCP/5063, and the VM firewall restricts that port to
the SIP edge/WireGuard source. Other phones remain on TCP/5061 with TLS 1.2+.

VM reboot recovery was also exercised on 2026-10-16 while Asterisk reported
zero active calls. VM209 rebooted at `2026-10-16 12:38:36 UTC`; Docker and
`voice-firewall.service` became active at `12:38:53 UTC`. The
`voice-conference-asterisk-1` container started at `12:38:53 UTC` with restart
policy `unless-stopped` and became healthy. Asterisk again showed a dynamic
contact for AOR `1234`, and the Yealink Account page at `192.168.20.133` showed
`Зарегистрирован`; Asterisk showed zero active calls. The edge VPS was still
`enabled/active` with public TCP/5062 DNAT to VM TCP/5063. This verifies service
and registration recovery after an Asterisk VM reboot. The handset itself was
not power-cycled in this check, and call audio/hangup behavior was not tested.

For a future registration failure, check in this order:

1. On the phone, verify both `SIP Server 1 Port` and `Outbound Server Port`
   are `5062`, the transport is TLS, and the username/register name are
   `1234`. Do not share or replace the password just to inspect it.
2. On VPS `evita`, verify that the edge unit is active and the public to
   private port map remains installed:

   ```sh
   sudo systemctl is-active voice-sip-edge.service
   sudo nft list ruleset | grep -E 'dport 5062|dport 5063'
   ```

   The DNAT rule should map public TCP/5062 to `192.168.20.70:5063`.
3. On VM209, verify Asterisk is healthy, endpoint `1234` still selects
   `transport-tls-1234-internet-legacy`, and `pjsip show contacts` contains an
   AOR `1234` contact. Run these on the VM host:

   ```sh
   sudo docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show endpoint 1234'
   sudo docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show contacts'
   sudo docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count'
   ```

   A present contact is the registration signal; the endpoint's `NonQual`
   display is expected because `qualify_frequency` is disabled and does not
   mean authentication failed.
4. If no contact appears, inspect only sanitized registration/error events.
   Never paste SIP Authorization headers, passwords, or unredacted packet
   captures into the repository or chat.

### Scope and limitations

The listener on TCP/5063 is configured with TLS 1.0 for this legacy phone; the
default public TCP/5061 listener remains TLS 1.2+. TLS 1.0 is weaker and should
remain limited to this compatibility path. The VM firewall restricts backend
access to the edge/WireGuard source, but PJSIP's endpoint `transport=` setting
does not make inbound registration exclusive to extension `1234`; any valid
SIP credential that reaches the listener may authenticate there.

This correction verifies SIP registration only. It did not run the separate
two-browser call acceptance, place a PSTN call, or verify two-way audio and
hangup behavior. Also, SIP TLS protects signaling, not RTP media; extension
`1234` retains its previously approved `media_encryption=no` compatibility
setting, so call audio is not encrypted in transit.
