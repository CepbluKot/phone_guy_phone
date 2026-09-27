# Asterisk Phone Provisioning Design

Status: proposed design for review
Date: 2026-09-25

## Goal

Let an administrator assign one configured SIP extension to a physical phone
and provision that phone from the existing Asterisk service. The admin page
must show each known phone, its MAC address, assigned extension, SIP
registration state, and model/profile. Reassignment must update the phone's
real SIP account after it fetches its configuration.

## Accepted direction

- Current phase: an editable MAC-to-extension phonebook is an administrative
  inventory only. Saving a phonebook assignment does not modify handset SIP
  credentials or Asterisk endpoints. Provisioning remains a separate future
  capability gated on the prerequisites below.
- Use Asterisk's open-source `res_phoneprov` and
  `res_pjsip_phoneprov_provider` modules, not a new provisioning product.
- Keep the React admin and Go control plane already in the project.
- Persist an explicit physical-device-to-extension mapping in the Go-managed
  configuration. Do not infer ownership from caller ID or IP address.
- Start with the physical phones reachable on the current private LAN. Support
  only models with a verified template; unknown models remain unassigned.
- Set the provisioning URL manually on each handset. Do not change DHCP,
  router provisioning, or any network-wide option.
- Publish only the provisioning path through the existing HTTPS Caddy site.
  Keep ARI inaccessible to handset clients. Restrict provisioning requests to
  the configured handset source addresses; the endpoint returns SIP account
  credentials and must not be generally reachable from the LAN or VPN.
- A mapping change applies to the next provisioning fetch. The administrator
  initiates the phone's re-provision/reboot; active calls and unrelated Asterisk
  routing are not changed.
- Retain the current passwordless admin mode requested by the user. This does
  not relax source-address restrictions on the credential-bearing provisioning
  path.

## Current repository facts

- The Asterisk image is built from a pinned Asterisk 22 source and explicitly
  selects modules; `autoload=no` is set. Neither phone-provisioning module is
  currently selected or loaded.
- Asterisk HTTP currently serves ARI on port 8092. In Compose, the host-side
  port is loopback-only. Phone provisioning must not expose the whole listener.
- The PJSIP endpoint inventory is `1983`, `1987`, `1988`, and `2014`. Endpoint
  authentication secrets are in the generated PJSIP runtime config, not the
  Go-managed voice-route file.
- The existing Go admin API stores voice-processing profiles by extension;
  it does not yet store device/MAC assignments or provision phone accounts.
- The repository's UI work and Go admin changes already in the working tree
  are user work and must be preserved.

## Proposed architecture

```text
Admin browser -- HTTPS --> Caddy --> Go admin API
                                   └─ durable MAC → endpoint mapping

Known handset -- HTTPS GET /phoneprov/<MAC>/... --> Caddy source-IP allowlist
                                                    └─ Asterisk HTTP phoneprov
                                                         ├─ res_phoneprov
                                                         ├─ PJSIP provider
                                                         └─ model template + endpoint auth
```

The mapping is the authoritative assignment. It includes the normalized MAC,
verified model/profile, assigned endpoint, and the last known permitted source
IP. Each MAC and extension can appear in at most one active assignment. An
endpoint already assigned to another phone cannot be selected. The API validates
all fields and writes updates atomically with optimistic revision checks.

The Go control plane materializes Asterisk's phoneprov association config from
the validated mapping, then asks Asterisk to reload the relevant PJSIP config
through a loopback-only authenticated control interface. The reload operation
must be serialized and report failure without acknowledging a mapping that
Asterisk did not accept. The administrator UI reports the mapping as pending
until Asterisk has accepted the new config. The implementation must first
confirm the supported Asterisk 22 reload command and control interface; it
must not shell out to arbitrary commands or grant the Go container Docker
socket access.

Provisioned templates render the already-configured PJSIP endpoint username and
secret. The credential is never returned by the admin API, written to the
mapping file, or logged. Requests for unknown MACs, unassigned devices, unknown
templates, and arbitrary paths return 404. The template set must not serve
firmware or unrelated files until needed and reviewed.

Caddy routes only `/phoneprov/*` to Asterisk. It has an explicit deny handler
for all other requests on that path and permits source IPs associated with
configured handsets only. ARI remains on its existing loopback-only host port.
Before deployment, verify handset source addresses are stable/reserved and
confirm their firmware trusts the existing site certificate for HTTPS
provisioning. If either condition is false, stop and revise the security design
before exposing SIP credentials.

## Admin behavior

- Show known phones with MAC, verified model, current IP/registration state,
  assigned extension or “unassigned”, voice-processing profile, and last
  provisioning status/time.
- Let the administrator select an available configured extension for an
  unassigned phone or change an assignment. Reject duplicate MACs and duplicate
  extension assignments.
- Show the exact HTTPS provisioning URL and concise per-model instructions for
  entering that URL on the handset. Never display a SIP password.
- Changing an assignment marks the handset “needs reprovisioning”; after a
  successful fetch, show it as provisioned. Runtime registration remains the
  source of truth for whether the SIP account is connected.
- Keep voice-processing profile selection separate from SIP extension
  assignment. Profile behavior continues to be keyed by endpoint and remains
  unchanged by provisioning.

## Device discovery and model support

Use the existing live PJSIP contact inventory to show registered extension and
contact IP. MAC/model inventory must be verified from device-side facts, not
guessed from the IP or a generic web-server banner. The exact Yealink and second
handset models and firmware versions must be confirmed before finalizing their
templates. Devices whose model is not confirmed can be recorded as discovered
but are not provisionable.

Provisioning does not automatically change registration, reboot devices, alter
DHCP, or change extension passwords. It changes the config served for a
selected MAC; the phone applies it only after its next fetch/reboot.

## Failure and security behavior

- Invalid mapping, stale revision, failed persistence, or Asterisk reload
  failure leaves the last accepted mapping and provisioning response active.
- Never publish credentials for an unknown or unassigned MAC.
- No general wildcard source allowlist; only verified handset addresses are
  permitted. Changes to a handset address require an administrator update.
- Provisioning responses and URLs are excluded from access logs where they
  contain device identity. No SIP secrets or response bodies in logs.
- Existing Go admin's no-password mode remains confined to its private admin
  surface; it cannot grant access to `/phoneprov`.
- No changes to public DNS, public firewall rules, DHCP, VM208, or call routing.

## Rollout and acceptance

1. Build a candidate Asterisk image with the two modules and model templates;
   verify modules load and a synthetic MAC request renders the expected SIP
   username/secret without including another endpoint's credentials.
2. Implement the Go device mapping API/store, constrained provisioning reload
   adapter, and React assignment UI.
3. Add Caddy's exact-path proxy and source-address allowlist. Verify ARI remains
   unreachable from handset IPs and that arbitrary paths/MACs cannot retrieve
   configuration.
4. Deploy the candidate with rollback artifacts while no calls are active.
   Do not modify a physical handset automatically.
5. Manually configure the provisioning URL on one verified Yealink handset,
   assign a test extension, fetch config, and verify registration and calls.
   Repeat for each additional verified model before enabling it in the UI.

Success means an administrator can assign and reassign a configured SIP
extension to a verified phone, retrieve only that phone's current configuration
through the restricted endpoint, see its registration/provisioning state, and
place calls after the phone fetches the config. The HTTPS provisioning route
does not expose ARI or other config, and voice routing/profile behavior remains
unchanged.

## Risks and open verification

- Asterisk's PJSIP phoneprov provider renders `SECRET`; a mistake in Caddy
  matchers or host firewall could disclose SIP credentials. The source allowlist
  and unrelated-path denial are release blockers.
- The Asterisk 22 reload mechanism for generated phoneprov objects must be
  verified. If a safe authenticated reload channel cannot be provided without
  broadening privileges or exposing AMI, the design must stop and return for
  revision rather than silently introducing Docker-socket access or an
  unauthenticated control port.
- Exact handset models, firmware, HTTPS CA trust, and stable source addresses
  are not yet verified. Only confirmed devices may be enabled.
- A handset provisioning fetch may overwrite local settings. Test on one
  handset and preserve its current configuration for rollback before using
  additional devices.
