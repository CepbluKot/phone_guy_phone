# Phone Discovery, Load Monitoring, and Capacity Design

Status: proposed design for user review
Date: 2026-09-25
Requirements source: [Voice Admin Product Requirements](../../VOICE_ADMIN_REQUIREMENTS.md)

## Goal

Extend the React/Go admin app so an operator can find newly connected phones,
assign a configured SIP extension to a physical device, monitor voice-service
load, and see a defensible estimate of concurrent voice-conversion users.

This design extends the per-phone role and Asterisk provisioning designs. It
does not replace them; their call isolation, private-network, and credential
handling requirements still apply.

## Accepted direction

- Discover currently registered devices from Asterisk PJSIP contacts.
- Add a read-only DHCP lease source for unregistered candidates if the router
  exposes a supported and least-privilege read interface. Do not write router
  configuration or change DHCP behavior. Do not perform active subnet scans.
- Keep discovery separate from enrollment: a discovered MAC/IP is unassigned
  until an administrator explicitly maps it to an available configured SIP
  extension.
- Provision actual SIP-account settings through the existing Asterisk
  `res_phoneprov`/`res_pjsip_phoneprov_provider` design and verified model
  templates. Keep manual initial provisioning URL setup on the phone.
- Add lightweight, read-only telemetry to the existing Go admin service. Reuse
  the RVC worker health endpoint and the validated `processingMs` metadata from
  the production WebSocket path. Do not add a second monitoring stack for this
  first dashboard.
- Separate the call controller's enforced limit from the measured potential
  capacity. Current source enforces one processed call; show that as “allowed
  now.” Show a higher potential only after a controlled production-path load
  measurement with stated headroom and timestamp.
- Do not change the call-admission limit automatically based on a metric or
  estimate.

## Current repository evidence

- `internal/calls/gate.go` creates a one-token `sessionGate`; the same router
  uses it when admitting processed calls.
- `rvc_service/server.py` serves production `/healthz` with readiness, active,
  running, and queued-window fields.
- `internal/rvc/stream.go` requires and validates `processingMs` metadata for
  each one-second output block but currently discards the value after checking
  it.
- `cmd/voice-web/main.go` currently exposes only generic Go `/healthz`; it has
  no admin telemetry endpoint.
- `deploy/compose.goweb.yaml` runs Go in a container with host networking and
  does not grant a Docker socket. The RVC worker is a separate host service.
- The repository's homelab documentation identifies Archer AX12 at
  `192.168.20.1` as the home DHCP server. Its current lease-read API and
  credentials are not verified from the voice-service VM.
- The one-session Go limit is source evidence. The production worker's
  multi-user capacity and current GPU/VM headroom are not established by this
  inspection or by historical benchmark documents.

## Proposed architecture

```text
PJSIP contacts ─────────────┐
                            ├─ Go inventory service ── React admin
Read-only DHCP lease source ┘          │
                                       ├─ extension/device assignments
RVC /healthz ──────────────────────────┤
Go processed-call + RVC block metrics ─┤
Read-only host/VM metric provider ──────┘
```

The Go inventory layer merges sources by MAC only when a source actually
provides a MAC. PJSIP contacts provide SIP endpoint and contact address; they
must not be treated as a MAC source. DHCP candidates provide observed MAC/IP
and lease freshness. When no join key is available, show separate source rows
with their provenance rather than guessing that two IP observations are the
same phone.

Every inventory record reports its source and observation time. DHCP-derived
devices are candidates; Asterisk registration is the source of truth for SIP
connectivity. Model/vendor information may be a hint, but provisioning requires
administrator-confirmed model and firmware from a verified registry.

The assignment store remains the authoritative MAC-to-extension mapping. The
existing extension uniqueness constraint stays in force. Assignment does not
change the voice profile; profile remains attached to the SIP extension. The
existing provisioning mechanism applies the endpoint settings only after the
phone fetches its configuration.

Telemetry uses bounded in-memory rolling windows and returns summaries rather
than unbounded samples. The Go service aggregates successful `processingMs`
observations with active/queued state from the worker. A metric source failure
produces an explicit unavailable/stale state and never blocks SIP call control.
Only read-only host metrics are permitted. The implementation must choose a
least-privilege provider that does not require the Docker socket or a new
unauthenticated listener; if safe host CPU/RAM/GPU access cannot be provided,
show only the available service/RVC metrics and mark host metrics unavailable.

## Admin behavior

### Phones

- Add “discovered” candidates that have no assignment, showing source, MAC,
  IP, last seen, and available confidence/model information.
- Keep “registered” status separate from “discovered” status.
- Let the administrator select an available configured extension and confirm
  the physical-device assignment. Reject duplicate MACs, duplicate extension
  assignments, stale revisions, unsupported extensions, and unverified
  provisioning models without mutating the current mapping.
- After assignment, display the extension, existing voice profile, SIP
  registration state, and provisioning state. Use the approved model-specific
  URL/instructions without displaying the SIP secret.
- If DHCP reading is unsupported or unavailable, explain that new unregistered
  devices cannot be discovered from that source; do not imply a complete
  network scan.

### Load and capacity

Show at minimum:

- Go service and RVC readiness, with last successful poll time;
- active processed calls versus the enforced admission limit;
- RVC active/running and queued work;
- recent processing latency summary from validated one-second RVC metrics;
- VM CPU and RAM, plus GPU utilization and memory when safely available;
- stale/unavailable state for any failed metric source;
- “Allowed now” and “Measured estimate” as distinct values, with the estimate's
  measurement timestamp, test profile/duration, and headroom.

Until a production-path concurrency measurement exists, render “Measured
estimate: not measured.” A historical test or research/demo service is not
valid evidence for current production capacity.

## Capacity methodology

The first implementation reports the enforced call-admission limit and gathers
the evidence required for a later estimate. A capacity estimate may be
published only after a repeatable controlled-load procedure:

1. Use the production Go call path, production Python RVC worker, selected
   model/profile, audio format, one-second block cadence, and the deployed VM/GPU.
2. Increase concurrent processed speakers in controlled steps while observing
   processing latency against the real-time block budget, output continuity,
   queue growth, errors, CPU/RAM/GPU/VRAM, and service restarts.
3. Sustain a candidate count for a documented interval and repeat the run.
4. Set the measured estimate below the highest count that passes all
   acceptance thresholds, retaining an explicit safety margin.
5. Store the run's non-secret summary and timestamp. Do not store audio.

This dashboard does not itself alter the enforced limit or initiate a disruptive
load test. A separate approved implementation/deployment step is required to
change admission behavior.

## Failure and security behavior

- DHCP data unavailable: retain Asterisk inventory, label DHCP discovery
  unavailable, and show when it last succeeded.
- Asterisk unavailable: retain known assignments but label registration state
  unknown; do not infer disconnected from a failed poll.
- RVC or metrics unavailable: keep the admin usable and call controller
  independent; display stale/unavailable data and do not fabricate capacity.
- Malformed or conflicting discovery records are quarantined from assignment
  until corrected. Discovery never creates or changes assignments on its own.
- Keep DHCP credentials outside Git and browser code. Use a read-only account or
  token if the router supports one; never collect router configuration.
- Keep existing origin and admin API protections. The temporarily passwordless
  admin mode does not authorize provisioning requests from arbitrary addresses.
- Do not add broad host privileges, Docker socket access, public metrics ports,
  or a new listener bound beyond loopback/private service interfaces.
- Do not log credentials, provisioning response bodies, SIP audio, or raw
  unbounded metric histories.

## Rollout and acceptance

1. Read-only preflight confirms whether the AX12 exposes a supported DHCP lease
   interface, whether the Go runtime can read it safely, exact router/phone
   network reachability, and the least-privilege host metric source. If the
   router has no suitable interface, stop before adding active scanning and
   revise the discovery approach with the user.
2. Implement source adapters and provenance-aware inventory, preserving existing
   Asterisk contact and assignment behavior.
3. Add rolling RVC/Go telemetry and a read-only host metric provider only where
   safe; expose a same-origin admin API and UI with freshness states.
4. Verify candidate discovery does not assign endpoints automatically,
   duplicate assignments are rejected, unavailable sources are reported, and
   no credential or audio reaches telemetry/logs.
5. Establish a controlled production-path load measurement before displaying
   any potential-user count above the enforced limit.
6. Deploy only after scoped rollback and call-idle preflight; verify the UI and
   actual phone registration on the private network.

Success means the admin can see registered phones and DHCP-discovered
unregistered candidates when the router supports safe lease reads, explicitly
assign a configured extension to a verified phone, view live service metrics
with freshness, and distinguish the enforced one-call limit from a measured
potential capacity. Monitoring must not affect call processing, and the
provisioning route must remain credential-restricted.

## Risks and open verification

- The router's DHCP lease-read API is unverified. Do not implement guesswork or
  active scanning as a substitute.
- Exact handset models, firmware, HTTPS certificate trust, stable source IPs,
  and a constrained Asterisk reload path remain provisioning prerequisites in
  the existing design.
- Safely obtaining VM and GPU metrics from the current Go container may require
  a narrowly scoped local provider. If that means broad privileges, omit those
  metrics until a safer interface is agreed.
- One active processed call is the current source-level limit. Increasing it
  can change real-time audio quality and call behavior; measurement alone does
  not authorize raising the limit.
- Historical benchmark figures are not current capacity evidence.
