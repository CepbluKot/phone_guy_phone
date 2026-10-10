# Yealink T21 registration as extension 1234 — 2026-10-10

## Symptom

The Yealink T21 at `192.168.20.36` showed a registration error for account
`1234`. Asterisk had no PJSIP contact while the handset was attempting to
register; the targeted SIP trace did not see a `REGISTER` arrive.

## Cause and fix

This legacy handset negotiates TLS 1.0, while the public registrar
`phone.awesomeio.ru:5061` requires TLS 1.2 or newer. The handset therefore uses
the private outbound proxy `192.168.20.70:5062`, whose TLS 1.0 compatibility
listener is limited to approved LAN sources. The public TLS 1.2 listener was
left unchanged.

The final handset-side fix was to add the official ISRG Root X1 certificate to
the Yealink trusted-certificate store, then save the account settings again.
Certificate and server-name verification remain enabled. After that change the
handset registered as `1234`.

## Verification and limits

On 2026-10-10, Asterisk's live `pjsip show aor 1234` output contained a dynamic
contact from `192.168.20.36` over TLS. `pjsip show endpoint 1234` showed the
endpoint attached to `transport-tls-2016-lan` on port `5062`. This confirms SIP
registration; it does not by itself confirm a completed phone call or audio.
No SIP password or certificate private key is recorded here.
