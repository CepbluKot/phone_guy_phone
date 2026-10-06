# Global Physical Phone Onboarding Design

Status: draft for owner review

Date: 2026-10-06

## Goal

Make physical SIP phones use one predictable, secure network and media path so adding a handset does not require per-phone IP routes, firewall exceptions, or Asterisk transport edits. Define a safe path to automate the remaining per-device account and MAC assignment work.

## Current findings

- Internet phones register through the shared PJSIP TLS transport on TCP 5061. Its signaling and media addresses point to the public SIP edge; its only `local_net` is the Asterisk Docker network. Do not add the home LAN or the phone's shared-Ethernet subnet to this Internet transport. That can cause Asterisk to advertise an unreachable private address to a phone behind NAT.
- All physical endpoints inherit common settings from `[phone-endpoint]`: PCMA, SDES-SRTP, symmetric RTP, `force_rport`, contact rewriting, and `direct_media=no`. The standard `rtp_timeout=30` remains a dead-channel guard, not a value to disable globally.
- After the transport correction was deployed, a physical 2015-to-2014 call remained active for 2 minutes 16 seconds. Asterisk reported increasing RTP packet counters in both directions. This verifies this call's SIP/media path and survival past the previous timeout; it does not verify human-audible quality or the separate mandatory browser-to-browser acceptance.
- A temporary diagnostic host route and Ethernet firewall rule were removed after investigation. Neither belongs in the steady-state design.
- The current MAC-to-extension phonebook is inventory; it does not provision SIP credentials or configure the handset. The current manual onboarding guide accurately requires device-by-device setup.

## Design options

### Option A — Keep manual onboarding

Retain the common server transport and endpoint defaults, document a standard handset checklist, and continue entering account settings by hand.

- Lowest implementation and credential-delivery risk.
- Still requires repeated handset UI work and an operator-maintained MAC assignment.

### Option B — Standard network plus admin enrollment flow (recommended)

Make the network path uniform and add an admin flow that registers the physical device, assigns an existing free extension, and prepares one verified model-specific configuration. Keep handset provisioning opt-in until the security and model prerequisites pass.

- Removes per-phone routing and manual server-file edits.
- Leaves a small number of explicit handset enrollment actions until secure provisioning is verified.
- Fits the existing Go admin and phonebook model.

### Option C — Full automatic provisioning immediately

Expose a public provisioning endpoint and push SIP credentials into each Yealink as soon as it appears.

- Minimizes operator interaction.
- Highest risk: public provisioning returns reusable SIP credentials, exact model/firmware support is not established, and phone discovery alone must not authorize credential delivery.
- Not recommended without one-time device enrollment, strict response handling, and model-specific acceptance.

## Recommended architecture

### 1. One network profile per host Ethernet port

When a computer must share its Internet connection with a directly connected phone, configure NetworkManager shared mode once on that Ethernet connection. The host provides DHCP and NAT for the phone; the phone receives an address automatically. Any phone connected to that port uses the same setup.

Do not add a phone-specific `/32` route, static address, DNAT rule, or firewall exception on the workstation or Asterisk VM. The phone registers to the public SIP hostname over TLS and sends media through the existing public edge. A phone connected to a normal router or hotspot uses the same SIP settings without the host-sharing profile.

The workstation profile is host configuration, not part of the Voice Changer deployment. Document the selected Ethernet connection name and verify its shared-mode behavior on the owner workstation before automating it.

### 2. One shared Internet SIP policy

All Internet physical phones use the same PJSIP TLS transport and common physical-phone endpoint template:

- SIP TLS 1.2 or newer to `phone.awesomeio.ru:5061`, with certificate name validation.
- Mandatory SDES-SRTP; no UDP/5060, plaintext media, or insecure fallback.
- PCMA and symmetric RTP; Asterisk stays in the media path with `direct_media=no`.
- `external_signaling_address` and `external_media_address` use the public edge address. `local_net` contains only the Docker bridge for this Internet transport.
- Keep the public edge NAT limited to TCP 5061 and the existing UDP 10000-10019 RTP range. Keep ARI, admin, Go HTTP, and RVC private.

A phone-specific exception is allowed only for a verified model/firmware behavior with a reproduction and a regression check. Do not disable `rtp_timeout` globally to hide a broken media path. If a handset suppresses RTP during silence and triggers the timeout, test its media-suppression setting and correct that model profile with evidence.

### 3. One admin enrollment flow for device-specific state

Add a private admin workflow with these inputs and checks:

1. Read the MAC from the handset or its live registration; require operator confirmation. Record verified model and firmware rather than inferring them from IP or a web banner.
2. Select an available physical extension. Reject duplicate MAC or extension assignment, virtual service extensions, and extensions already owned by another phone.
3. Save the MAC-to-extension assignment and expose registration status. Treat the assignment as an explicit device identity, not as proof of SIP authentication.
4. Keep SIP credential creation and device provisioning as a distinct secure operation. Never show secrets in logs, normal API responses, source control, or ChatGPT.

The initial release can automate inventory and assignment while leaving the owner to enter credentials into the handset. A later provisioning release may serve only a verified model's configuration through HTTPS after a high-entropy, short-lived, one-time enrollment token is paired with that MAC and extension. The token is consumed on successful fetch; responses are uncached, access logs redact the device path/token, and unknown or unassigned devices receive no configuration. Require handset certificate validation and verify that the exact firmware supports the chosen HTTPS flow before enabling it.

The existing proposal in `2026-09-25-asterisk-phone-provisioning-design.md` assumes phones on a private LAN and restricts credential delivery by source IP. That restriction does not work for general Internet phones with dynamic public addresses. Reuse its inventory and verified-template ideas, but revise the credential authorization model before implementing public provisioning. Do not expose the existing Asterisk HTTP listener, ARI, or a broad provisioning path.

## Rollout

1. Keep the deployed shared TLS/media transport as the baseline. Confirm the live config and ensure no diagnostic host routes or workstation firewall rules remain.
2. Update the operator guide to give the same SIP profile for every Internet phone and a one-time NetworkManager shared-mode setup for direct Ethernet use.
3. Implement and test the private admin enrollment flow for MAC, verified model/firmware, and extension assignment. This phase does not retrieve or display a SIP password.
4. Verify HTTPS provisioning capabilities and token binding on one exact Yealink model and firmware. Only then design and enable its one-time credential-bearing configuration path.
5. Repeat physical handset acceptance after each relevant release and retain the mandatory browser-to-browser acceptance for every project update.

## Acceptance criteria

- A phone connected directly to the prepared host Ethernet port obtains network settings automatically; a phone on a normal router/hotspot needs no different Asterisk or RTP configuration.
- Two physical phones on different NATs can register over TLS without phone-specific VM routes or firewall rules.
- Calls in both directions pass audio RTP both ways, remain connected through a quiet interval longer than 60 seconds, and clear all Asterisk channels after hangup.
- The public edge exposes only TCP 5061 and UDP 10000-10019 for physical SIP; public UDP 5060, ARI, admin, Go HTTP, and RVC remain unavailable.
- The enrollment workflow rejects duplicate assignments and does not reveal credentials. If provisioning is enabled, unknown MACs, expired/reused tokens, wrong model profiles, and unassigned devices cannot fetch an account configuration.
- Browser-to-browser acceptance is rerun and passes under the repository's existing checklist.

## Non-goals

- Disabling call timeouts globally.
- Adding per-phone static IPs, routes, public port forwards, or firewall exceptions.
- Provisioning unverified handset models or permitting plaintext fallback.
- Publishing Asterisk HTTP, ARI, admin, Go API, or RVC to the public Internet.
- Changing existing handset assignments without a separate migration plan.
