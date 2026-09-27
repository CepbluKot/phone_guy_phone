# Per-phone voice roles and Go control plane

Status: proposed architecture, based on the user's approved direction
Date: 2026-09-24
Target integration branch: `main`

## Goal

Allow an operator to assign a voice profile to a SIP phone in a React admin
application. Whenever that SIP identity participates in a call, its outgoing
speech follows the assigned profile. With `phone-guy`, other participants hear
only RVC-processed speech from that phone. The target production branch uses Go
for the web, admin API, call control, and other application runtime services.
Python remains for the RVC inference runtime, where the existing model and
PyTorch/CUDA stack require it.

## Accepted decisions

- This spec amends the 2026-09-08 target architecture only where it excluded an
  admin UI and assumed Python for web/call-control services. Its GPT v2 target,
  VPN boundary, fail-closed policy, one-session limit, and no-audio-retention
  decisions remain in force.
- React is the admin UI technology. The existing Proactive Monitoring frontend
  is a visual reference for its application shell, navigation, page headers,
  cards, tables, and status indicators. It remains a separate project; no source
  or runtime dependency is shared with it.
- Go is the backend technology for the admin API and SIP/ARI call control.
- The existing GPT v2 RVC worker remains Python and is called over its current
  private WebSocket contract. Do not port or alter model inference as part of
  this work.
- Profiles in the first version are `original` and `phone-guy`.
- A profile assignment is snapshotted when a call is admitted. Admin changes
  apply to new calls; they do not rewire active calls.
- VPN-only access, explicit admin authentication mode, fail-closed audio handling,
  no call recording, and no VM208/Frigate changes remain mandatory.

## Current state and scope boundary

The `codex/go-web-service` worktree contains an untracked Go candidate at
`cmd/voice-web` with a minimal static-file server and security headers. It does
not yet provide an API or SIP call routing and is not part of `main` at this
spec's creation. The existing `conference` and `selfmonitor` application
controllers are Python. The active RVC model worker is also Python.

The completed target migrates application web and call-control runtime
responsibilities to Go, including the required conference and self-monitor
behavior. Python test helpers, deployment utilities, research code, and the RVC
inference service are outside that runtime-language requirement. Preserve the
Go candidate's provenance and existing untracked work; inspect its exact state
before implementation, and do not replace it from `main` or regenerate it.

The admin UI manages voice assignments for configured SIP extensions. It does
not create SIP accounts, change endpoint credentials, expose ARI, or manage
network/firewall access. Before deployment, reconcile the actual configured
PJSIP endpoint inventory with the illustrative numbers in older documents.

## Architecture

```text
Browser on VPN
  └─ HTTPS → private Caddy → Go voice application
                              ├─ React admin SPA and authenticated API
                              ├─ durable voice-routing configuration
                              └─ Asterisk ARI call controller
                                   ├─ original profile → normal bridge path
                                   └─ phone-guy profile
                                        ├─ isolate raw source channel
                                        ├─ stream PCM to Python GPT v2 RVC
                                        └─ inject processed channel only

Python RVC inference remains a private loopback service.
```

The Go voice application owns the route configuration and call lifecycle. Keep
the route configuration in a versioned, secret-free local file with strict
schema validation and atomic replacement. It contains the configured extension
inventory and one profile per extension. Missing or unknown identities are
rejected; there is no implicit `original` fallback. Store no SIP or ARI
credentials in this file or in browser code.

The React app is a small, independent Vite-built SPA served by the Go
application. Its single-page workflow lists configured phones, shows each
current profile and save state, and allows changing the profile. It shows clear
busy, unavailable, validation, and save errors. Apply the reference project's
visual language without copying its source, project navigation, or unrelated
workflow features.

The Go API is same-origin with the SPA, versioned, and restricted to the admin
session. Reads return the current mapping and configuration revision. Writes
validate the extension and profile, reject stale revisions, atomically persist
the update, and return the accepted revision. The runtime reads the latest
validated revision before admitting each new call.

Use one configured operator credential stored outside Git by default. A
temporary passwordless mode may be enabled only on VM209 with
`VOICE_ADMIN_AUTH_DISABLED=true`; it relies on the existing private LAN/VPN
firewall boundary, so every device able to reach the admin origin can change
phone profiles. Keep the credential and default authenticated mode available
for immediate restoration. In both modes, mutating requests require an exact
allowed HTTPS Origin. When authentication is enabled, admin login uses a
short-lived Secure, HttpOnly, SameSite cookie and writes also require CSRF
validation. Neither the password nor session tokens are logged or returned by
APIs.

## Call and audio behavior

1. Asterisk routes both originating and terminating SIP channels through the Go
   call controller before native dialing or bridge membership can expose their
   media. Direct calls and conference participation follow the same profile
   rule.
2. The controller resolves each participant using its verified PJSIP identity
   and snapshots the profile for that call.
3. `original` follows the normal Asterisk bridge path.
4. `phone-guy` keeps the raw source channel out of every bridge audible to
   other participants. The controller streams its 48 kHz mono PCM16 audio to
   the existing local GPT v2 RVC worker and injects only validated processed
   frames into the call. The phone continues to receive the other participants'
   normal mixed audio.
5. Missing/invalid route data, unknown endpoints, RVC busy/unavailable,
   malformed frames, timeout, or disconnect terminate/reject the affected
   processed leg. There is never a raw-audio fallback.
6. The current RVC worker permits one active AI session. Concurrent
   `phone-guy` calls beyond that capacity receive an explicit busy/reject
   outcome without entering the audible bridge.
7. On hangup or failure, the controller closes owned ARI channels, bridges,
   queues, and RVC sessions idempotently. It does not retain audio or
   transcripts.

Incoming speech from the other call participants is not voice-converted by a
phone's outgoing profile. If several phones in a conference are assigned
`phone-guy`, each source must be processed independently; the single-session
capacity means only one such speaker can be active at a time in the first
version.

## Runtime migration and deployment

- Extend the existing Go web-service candidate rather than adding a duplicate
  Go web server. The Go runtime must own the admin API and ARI call lifecycle;
  determine the final process/container split from the existing VM bindings,
  ARI reachability, and least-privilege constraints during implementation.
- Port the existing conference listener and `1999` self-monitor controller
  behavior to Go before retiring their Python runtime deployments. Preserve
  their supported contracts and bounded cleanup behavior.
- Keep the Python RVC worker, its locked model environment, and its current
  WebSocket protocol unchanged. FCPE remains a canary and is not selected by
  the admin role.
- Serve the admin UI and API only through the existing private VPN/Caddy route;
  do not open SIP, RTP, or ARI to the public internet.
- Deploy only to VM209 after a fresh read-only baseline. Keep VM208 and Frigate
  untouched. Retain scoped rollback artifacts and verify them before removing
  any superseded runtime.
- The desired end state is integrated on `main`; do not overwrite or silently
  discard the existing Go worktree's untracked files while preparing that
  integration.

## Acceptance criteria

- Go unit/contract tests cover configuration parsing, unknown extension/profile
  rejection, atomic updates, stale revisions, admin authentication/CSRF, and
  call-session cleanup.
- React tests cover phone list loading, profile updates, stale-save/error
  states, and inaccessible/unauthenticated API behavior. The production build
  serves the SPA and API from the same private origin.
- A synthetic call matrix proves original and processed routes for a phone as
  caller and callee, including direct calls and conference membership.
- With `phone-guy`, raw source PCM is never present in an audible bridge before,
  during, or after RVC processing. Test busy, unavailable RVC, malformed output,
  timeouts, cancellation, concurrent calls, and process restart; all remain
  fail-closed.
- A private real-device call verifies that the assigned phone is processed in
  every call where it participates and that reverse-direction audio remains
  clear. Confirm that changing a profile affects only subsequent calls.
- Existing browser conference listening and the `1999` self-monitor behavior
  remain functional after their Go migration. The Python RVC health and
  processed-audio path remain healthy throughout deployment and rollback.
- Final runtime inventory on VM209 shows Go for application web/control
  services and Python only for the RVC inference runtime. No public listener,
  audio retention, VM208 change, or production raw fallback exists.

## Risks and escalation

- Existing repository design says the real SIP/media-routing path is incomplete.
  Reconcile that with current VM PJSIP/dialplan/ARI state before changing any
  live route; stop on mismatch rather than inferring ownership.
- Ensure every call involving a processed endpoint enters the Go controller
  before a native bridge or direct `Dial` can expose raw audio.
- The one-session RVC limit constrains simultaneous processed participants.
  Do not add a second model or lower quality as an implicit workaround.
- A Go deployment must be able to reach loopback ARI/RVC without broadening
  network bindings or exposing credentials. If the existing container boundary
  cannot meet this, choose a separate loopback-only Go runtime and document the
  exact interfaces before implementation.
- Do not remove Python sources or deployment artifacts until equivalent Go
  behavior is live-verified and a scoped rollback is available.

## Implementation brief

Goal: Add an authenticated React phone-profile admin app and make all
application web/call-control runtime services Go, while preserving Python only
for RVC inference.

Non-goals: Port PyTorch/RVC inference, change the selected GPT v2 model or wire
protocol, add multiple concurrent model sessions, expose public SIP/RTP/ARI,
change VM208, retain call audio, or import code from the reference frontend.

Accepted decisions and constraints: User approved a React admin app inspired by
Proactive Monitoring and a Go backend for UI/API/SIP routing. The final target is
`main` using Go for application web/control services; keep Python where model
inference requires it. Profiles are `original` and `phone-guy`; applies on new
calls; every processed path is fail-closed.

Steps:
1. Reconcile the exact Go candidate, existing VM topology, endpoints, routes,
   and dirty state; record a read-only baseline.
2. Add the React admin SPA and Go route API with authenticated, atomic,
   versioned configuration and tests.
3. Implement Go Asterisk ARI call routing for caller/callee paths and stream
   only processed PCM through the existing Python RVC worker.
4. Port currently deployed conference-listener and self-monitor application
   behavior to Go, preserving existing interfaces and tests where applicable.
5. Deploy behind the existing private boundary, run synthetic and physical
   acceptance, verify rollback, then retire superseded Python runtime services
   and integrate the verified result on `main`.

Verification: Go tests and race checks; React typecheck/build/unit tests;
synthetic SIP/ARI media matrix and fail-closed fault injection; private live
RVC audio; real phone calls in both directions; health/log/permission checks;
VM209 before/after and scoped rollback; final runtime-language inventory.

Risks and escalation conditions: Any uncertainty about current PJSIP ownership,
raw-audio bridge isolation, simultaneous RVC capacity, ARI reachability, admin
authentication, preservation of dirty files, or rollback completeness blocks
the affected deployment step. Do not fall back to raw voice or touch VM208.
