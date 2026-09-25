# Voice Admin Product Requirements

Status: canonical requirements index; implementation and live deployment are
tracked separately. Last updated: 2026-09-25.

This document records the durable product decisions and requirements for the
private voice service and its React/Go administration app. Detailed subsystem
designs are linked below. When a design document adds implementation detail,
these product requirements remain the user-visible outcome and constraints.

## Product goal

Operate the private SIP voice service from one admin app: assign voice behavior
to SIP identities, discover and enroll physical phones, map a real phone to a
configured SIP extension, see service load, and understand both the enforced
voice-conversion limit and measured capacity.

## Runtime and UI

- The application web service, admin API, SIP/ARI call control, and other
  application runtime services run in Go.
- React is the admin UI. Its visual direction follows the shell, navigation,
  cards, tables, and status treatment of `/home/oleg/Documents/work/proactive-monitoring`;
  it does not import that project's source or runtime dependencies.
- Keep Python for the existing RVC inference runtime, where the model and
  PyTorch/CUDA stack require it. The Go application calls the private RVC
  WebSocket service.
- Serve the app through the existing private Caddy/VPN boundary. Do not expose
  SIP, RTP, ARI, the RVC worker, or provisioning credentials to the public
  internet.
- The admin is temporarily passwordless at the user's request. Preserve the
  ability to restore authentication; passwordless access is safe only within
  the existing private network boundary and must not grant access to SIP
  provisioning credentials.

## Voice profiles and call behavior

- The admin manages voice profiles by configured SIP extension. Initial
  profiles are `original` and `phone-guy`.
- When a phone assigned a profile participates in a call, that phone's outgoing
  speech follows its profile whether it originated or answered the call.
- For `phone-guy`, other participants receive only processed speech from that
  phone. Incoming speech from the other participants remains clear at the
  assigned phone. Do not process unrelated phones based on caller ID text or IP
  address.
- Resolve a phone by its verified PJSIP identity. Snapshot the selected profile
  when the call is admitted; edits apply to new calls, not active calls.
- Processing is fail-closed: if routing, conversion, validation, or the RVC
  worker fails, do not bridge that phone's raw audio to other participants.
- Do not record calls, retain PCM audio, or retain transcripts.

## Physical phones, discovery, and SIP assignment

- Show known physical phones with MAC, verified model/firmware where available,
  current IP, SIP registration state, assigned extension, voice profile, and
  provisioning state.
- Give the inventory two separate admin navigation points:
  - **Existing phones**: every phone already enrolled/assigned in the app,
    including currently offline phones, with registration and provisioning
    status.
  - **New / unassigned phones**: newly discovered candidates and any known
    device that has no phone-to-extension assignment yet. This view is the
    explicit place to assign a free SIP extension.
- A phone moves from “new / unassigned” to “existing phones” only after an
  administrator saves its assignment. A temporary loss of SIP registration
  must not move an enrolled phone back into the new-device list.
- Discover phones already visible as PJSIP contacts. Also discover unregistered
  new phones from a read-only DHCP-client/lease source when the router exposes a
  safe, supported way to read it. A discovered device is a candidate, not an
  enrolled phone; do not infer its model or identity from an IP address alone.
- Do not change DHCP, router settings, network-wide provisioning options, or
  perform active subnet scans as a fallback. If DHCP lease access is unavailable,
  show that discovery limitation and retain Asterisk registration/manual
  enrollment as the supported sources.
- Let the administrator map a discovered physical phone to an available,
  configured SIP extension. Prevent duplicate active MAC and extension
  assignments. Keep voice-profile selection separate from SIP account
  assignment.
- A real SIP-account change requires phone provisioning; changing a UI label
  alone is insufficient. Use Asterisk's `res_phoneprov` and
  `res_pjsip_phoneprov_provider` with a verified per-model template. The
  administrator manually enters the provisioning URL on a supported handset;
  no phone is rebooted or reconfigured automatically.
- Only configured endpoints and verified phone models may be provisioned.
  Unknown devices remain visible as unassigned. Never show SIP passwords in the
  UI or admin API. Restrict the credential-bearing provisioning URL to
  explicitly approved phone source addresses and deny unrelated paths.
- A mapping change takes effect when the phone fetches its configuration. Show
  “needs reprovisioning” until the fetch succeeds; live SIP registration remains
  the source of truth for connectivity.

## Load monitoring and capacity estimate

- The admin shows data freshness and service readiness, active processed calls,
  RVC active/queued work, recent processing latency, and relevant VM CPU/RAM and
  GPU/VRAM metrics when those sources are available.
- Collect only operational metrics. Do not collect audio, SIP secrets, ARI
  credentials, or provisioning response bodies. Use read-only local metrics
  sources; do not grant the Go service Docker-socket access or expose a new
  unauthenticated network listener.
- Present two distinct capacity values:
  1. **Allowed now:** the enforced concurrent processing limit from the current
     call controller.
  2. **Measured capacity estimate:** only a repeatable controlled-load result
     using the production model, stream settings, and hardware, including the
     measurement time and safety headroom.
- Never infer a supported user count from a single CPU/GPU snapshot or a
  research/demo worker. Do not raise the enforced limit automatically when an
  estimate changes. A change to call admission capacity requires its own
  implementation and verification.
- If measurement data is missing or stale, say “not measured” and show the
  enforced limit instead of inventing a potential-user count.

## Security, persistence, and operational constraints

- Store voice profiles and device-to-extension mappings in validated,
  versioned, secret-free configuration with atomic updates and stale-revision
  protection.
- Never store SIP or ARI credentials in admin configuration, browser code, or
  logs. Asterisk remains the source for SIP credentials.
- Keep ARI and RVC loopback/private. Expose only the exact provisioning path
  required for supported phones and apply a narrow source-address allowlist.
- Do not modify VM208, Frigate, public DNS/firewall rules, or unrelated call
  routes as part of this work.
- Preserve existing uncommitted user changes. Before live deployment, inspect
  the current service ownership and state, prepare scoped rollback artifacts,
  and avoid restarts while calls are active.

## Current documented capacity and verification status

Repository inspection on 2026-09-25 found a Go `sessionGate` with one token,
which currently enforces at most one processed call at a time. The production
Python worker's `/healthz` reports readiness, active/running work, and queued
windows. The Go RVC stream validates `processingMs` metadata for one-second
output blocks but does not yet publish an admin telemetry API. These are source
facts, not a fresh live VM measurement.

The production worker's reproducible multi-user capacity has not been measured
in this design. Therefore the initial dashboard must show the current enforced
limit as one and the measured potential capacity as “not measured” until a
controlled production-path run supplies evidence.

## Related project documents

- [Per-phone voice role and Go control-plane design](superpowers/specs/2026-09-24-per-phone-voice-role-admin-design.md)
- [Asterisk physical-phone provisioning design](superpowers/specs/2026-09-25-asterisk-phone-provisioning-design.md)
- [Phone discovery, load monitoring, and capacity design addendum](superpowers/specs/2026-09-25-phone-discovery-load-capacity-design.md)
- [AI execution workflow](AI_EXECUTION_WORKFLOW.md)

These documents have different statuses. A proposed design or implementation
plan is not evidence that the feature has been implemented, deployed, or
verified on live phones.
