# Go phone roles and React admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a private React admin app for phone voice profiles and move application web, admin, SIP/ARI, conference-listener, and self-monitor runtime services to Go, retaining Python only for GPT v2 RVC inference.

**Architecture:** Extend the existing Go `voice-web` candidate to serve the React SPA and authenticated versioned API. A Go call controller owns Asterisk ARI channels and applies the configured profile to every call where an assigned SIP endpoint participates; only the processed media channel reaches an audible bridge. It streams to the existing loopback Python RVC v2 service and never falls back to raw audio.

**Tech Stack:** Go 1.22 module already present in the candidate; React + TypeScript + Vite; Vitest + Testing Library for UI tests; Gorilla WebSocket for Asterisk ARI/media and RVC WebSocket connections; Asterisk 22.11 ARI/`chan_websocket`; GPT v2 Python/PyTorch inference; Docker Compose; Caddy; VM209 systemd and scoped deployment/rollback.

**Spec:** `docs/superpowers/specs/2026-09-24-per-phone-voice-role-admin-design.md`

**Checkout rule:** Tasks 1–11 inspect and implement in `/home/oleg/.codex/worktrees/go-web-service/voice-changer` on `codex/go-web-service`, preserving its existing untracked candidate files. Paths in the file map are relative to that worktree unless explicitly called out. Task 12 integrates the reviewed, verified commits into `/home/oleg/Documents/voice-changer` on `main`, preserving its unrelated untracked files.

## Global Constraints

- Target delivery is integrated on `main`; preserve the pre-existing untracked Go candidate in worktree `codex/go-web-service` and inspect it before editing.
- Use Go for application web, admin API, SIP/ARI control, conference listener, and self-monitor runtime services; keep Python RVC inference and Python test/deployment/research helpers unchanged.
- Preserve GPT v2 at `ws://127.0.0.1:8090/ws/rvc-v2`; use 48 kHz mono PCM16LE, 20 ms / 1,920-byte input frames, and one-second / 96,000-byte output blocks.
- Roles are `original` and `phone-guy`. Resolve caller and callee identities; role changes apply only to calls admitted after the config update.
- No unknown-extension default. Unknown identity, invalid config, RVC busy/failure, malformed audio, timeout, or cancellation must reject/terminate the affected processed leg without raw-audio fallback.
- RVC permits one active AI session. Do not create a second model copy or route around a busy worker.
- Keep SIP/RTP/ARI and the admin UI private to VPN/LAN; use a separate admin credential outside Git. Do not expose secrets in browser code, API responses, or logs.
- Do not store caller audio or transcripts. Do not change VM208/Frigate, its GPU, storage, or configuration.
- Change VM209 only after a fresh read-only state capture and an exact, tested rollback plan. Do not restart or enable the intentionally stopped legacy HTTP container as a shortcut; deploy the Go replacement as a separate, owned release.
- The Proactive Monitoring frontend is a visual reference only. Do not edit it, copy its source, or add a cross-repository dependency.
- Preserve all unrelated dirty files and worktrees. Do not remove old Python deployment/code paths until Go parity is live-verified and rollback is proven.

## Review Focus

1. A caller or callee using `phone-guy` must never be audible from the raw channel; exercise both SIP call directions and conference entry.
2. Unknown or unmapped extensions and malformed routing config must fail closed, not become implicit `original` routes.
3. Concurrent AI calls, including two processed participants in one conference, must reject the excess session without raw passthrough.
4. RVC/ARI disconnects, busy, wrong metadata, frame gaps, timeout, and cancellation must clean up only owned resources and never leak raw audio.
5. Simultaneous admin writes, stale config revisions, failed disk replacement, unauthenticated requests, and cross-origin writes must not silently alter effective routes.

---

## File and Interface Map

| Path | Responsibility |
| --- | --- |
| `cmd/voice-web/main.go`, `main_test.go` | Go process lifecycle, local HTTP routes, static app serving, health and shutdown. Existing files in the candidate worktree are untracked and must be inspected/reused, not overwritten. |
| `internal/voiceconfig/` | Strict route schema, revisioned snapshot and atomic disk store. |
| `internal/admin/` | Login/session, CSRF/origin checks, and versioned route APIs. |
| `internal/ari/` | Authenticated ARI REST/events and owned Asterisk channel/bridge/media operations. |
| `internal/rvc/` | Go client for the unchanged Python GPT v2 WebSocket protocol and 20 ms / one-second media framing. |
| `internal/calls/` | Endpoint identity resolution, profile routing, raw-channel isolation, call lifecycle and exclusive RVC session gate. |
| `internal/conference/` | Go version of the existing synthetic conference listener contract and lifecycle. |
| `internal/selfmonitor/` | Go version of the `1999` echo-line contract and lifecycle. |
| `admin-ui/` | Independent React/TypeScript/Vite admin source, Vitest/Testing Library tests and build. The build output is served under `/admin/` by Go. |
| `web/` | Existing static voice pages plus generated admin build output; do not hand-edit generated React assets. |
| `Dockerfile`, `Dockerfile.goweb`, `compose.yaml`, `deploy/compose.goweb.yaml` | Keep the current Python Dockerfile/Compose path intact while building and privately staging Go under a separate Compose project; only finalize the default deployment after the rollback-backed cutover. |
| `conference/asterisk/extensions.conf`, `deploy/Caddyfile`, `compose.yaml`, `deploy/compose.conference.yaml` | Route all configured SIP endpoint call legs through Go while preserving private bindings and existing browser paths. |
| `deploy/deploy.sh`, `deploy/deploy-rvc.sh`, `deploy/deploy-conference.sh`, `deploy/deploy-selfmonitor.sh`, new/updated Go deploy scripts | Scoped install, service transition, config bootstrap, rollback and prevention of later Python redeploys or legacy HTTP restarts. |
| `tests/` | Go tests for new packages, React tests, deploy contract tests, and private synthetic live call/audio acceptance. |
| `docs/OPERATIONS.md`, `docs/CONFERENCE.md`, `docs/SELFMONITOR_2026-09-16.md`, `docs/LIVE_STATUS.md`, `README.md` | Current Go runtime, profile operations, measurements, health, rollback and Python inference exception. |

## Interfaces Shared Across Tasks

Define these contracts in their owning task before consumers are implemented:

```go
type Profile string

const (
    ProfileOriginal Profile = "original"
    ProfilePhoneGuy Profile = "phone-guy"
)

type RouteSnapshot struct {
    Revision   uint64             `json:"revision"`
    Extensions map[string]Profile `json:"extensions"`
}

type RouteStore interface {
    Snapshot() (RouteSnapshot, error)
    Update(extension string, profile Profile, expectedRevision uint64) (RouteSnapshot, error)
}
```

The route API is same-origin:

- `POST /admin/api/v1/session` accepts a password and issues a short-lived session.
- `DELETE /admin/api/v1/session` expires the session.
- `GET /admin/api/v1/voice-routes` returns `revision` and configured extension/profile rows.
- `PUT /admin/api/v1/voice-routes/{extension}` accepts `{ "profile": "original" | "phone-guy", "revision": <uint64> }`; stale revisions return `409` without writing.
- `GET /admin/` serves the React single-page app; unknown `/admin/*` paths serve its index only after path validation.
- `GET /healthz` remains secret-free and reports readiness only after config validation and required local dependencies are initialized.

The Go media API separates ownership from bytes:

```go
type RVCStream interface {
    SendFrame(ctx context.Context, pcm16le []byte) error
    Outputs() <-chan []byte
    Close(ctx context.Context) error
}

type RVCClient interface {
    Open(ctx context.Context) (RVCStream, error)
}
```

Every media frame is exactly 1,920 bytes. RVC output blocks are validated against the v2 metadata and split into exactly 50 contiguous 20 ms frames before injection. Interfaces may be refined only if all consumers and contract tests change together.

## Task 1: Reconcile the Go candidate and live topology

**Files:** Read `cmd/voice-web/main.go`, `cmd/voice-web/main_test.go`, `go.mod`, `Dockerfile.goweb`, `compose.yaml`, `conference/`, `selfmonitor/`, `deploy/`, the approved spec, and current VM209 operational docs. Do not edit the Proactive Monitoring repository.

**Interface:** No code or live-state change. Establish exact preconditions before implementation.

- [x] From both checkouts, record `git status --short`, `HEAD`, branch, and worktree list. Record the exact untracked Go candidate paths and hashes; do not stage them yet.
- [x] Capture VM209 read-only state: Go/Python containers and units, Asterisk/ARI app and dialplan, PJSIP endpoints, Caddy routes, RVC health and active/queue state, port ownership, available RAM/disk/GPU, and the intentionally stopped HTTP container state. Never print credentials or user audio.
- [x] Compare the live endpoint inventory and dialplan with the illustrative `201–203` schema. Produce the explicit list of configured endpoint IDs that the admin may assign; stop on unexpected call-route ownership. All extension IDs used in synthetic fixtures are test-only and must not be treated as production inventory.
- [x] Check the controller's current Asterisk media protocol and the Python RVC v2 handshake against `conference/asterisk.py`, `conference/rvc.py`, and `selfmonitor/ari.py`. Record every media-control JSON message, frame size, ownership cleanup, busy response and timeout the Go port must preserve.
- [x] Reconcile the live mirror implementation with the older Python source on `main`; amend Task 10 to preserve the installed behavior before implementation.

**Gate:** Do not start the retired Python HTTP container. Do not alter Caddy, Asterisk, the Python RVC worker, or any service during this task.

## Task 2: Harden and package the existing Go web candidate

**Files:** Modify `cmd/voice-web/main.go`, `cmd/voice-web/main_test.go`, and `go.mod`; update `Dockerfile.goweb`; add `deploy/compose.goweb.yaml`. Leave the production Python `Dockerfile` and `compose.yaml` unchanged until the rollback-backed cutover.

**Consumes:** Task 1's actual local/private bind addresses and preserved candidate source.
**Produces:** A Go HTTP process serving the existing static pages and `/admin/` without any Python dependency; `newHandler(webRoot string) http.Handler` remains testable.

- [x] Add failing Go tests that `/admin/` serves the SPA index, `/admin/assets/<file>` serves only files beneath the admin build root, unknown assets return 404, client-side `/admin/*` routes serve the SPA index only after path validation, and non-GET methods cannot mutate static content. Add a route test for the existing `web/live/` application so Go explicitly serves `/live/` and its microphone permission remains enabled.

```go
func TestAdminStaticRoutesStayInsideWebRoot(t *testing.T) {
    handler := newHandler(testWebRoot(t))
    for _, path := range []string{"/admin/", "/admin/assets/app.js"} {
        recorder := httptest.NewRecorder()
        handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
        if recorder.Code != http.StatusOK {
            t.Fatalf("GET %s status=%d", path, recorder.Code)
        }
    }
    recorder := httptest.NewRecorder()
    handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/admin/%2e%2e/app.js", nil))
    if recorder.Code == http.StatusOK {
        t.Fatal("admin static route escaped its root")
    }
}
```

- [x] From the candidate worktree, run `go test ./cmd/voice-web` and confirm the new admin-route test fails before implementation.
- [x] Add validated admin asset routing and SPA fallback; add the missing explicit `/live/` index route. Preserve `/conference/`, CSP, permissions, cache and path-traversal behavior.
- [x] Add a Go binary `healthcheck` mode and update the Go candidate image health check to invoke it; do not depend on Python, a shell, or `curl` inside the distroless image.
- [x] Build the Go binary with the existing multi-stage distroless/nonroot pattern. Stage Go under a separate Compose project using host networking only if Task 1 confirms this is needed to reach loopback ARI/RVC; bind the HTTP listener only to the selected loopback/private address and do not publish ARI/RVC ports. Leave the existing Python `Dockerfile` and `compose.yaml` deployment path unchanged until Task 11.
- [x] Add `deploy/compose.goweb.yaml` as a separate candidate project definition with the Go image, host networking if confirmed, loopback/private HTTP binding, no ARI/RVC port publication, non-root/read-only runtime and a binary health check. Do not replace the existing Python Compose project in this task.
- [x] Run `gofmt -w cmd/voice-web/*.go`, `go test ./cmd/voice-web`, `go test ./...`, and `go build ./...`. Validate the separate Compose file locally. Because the local Docker daemon is unavailable, build the exact candidate context on VM209 under a unique temporary image tag; run a temporary loopback-only container and verify its native healthcheck plus `/live/` and `/conference/`, then remove only that container and temporary build context. Preserve the existing Python Dockerfile and services.
- [x] Commit only the tested Go HTTP/container changes.

**Gate:** Static path traversal, unexpected methods, security headers, and container health must pass before the frontend/API work builds on the server.

## Task 3: Add strict versioned voice-route storage

**Files:** Create `internal/voiceconfig/config.go`, `store.go`, `store_test.go`; add an example non-secret config under `deploy/voice-routing.example.json`; update `go.mod` only if a new dependency is needed.

**Consumes:** Task 1's explicit PJSIP endpoint inventory.
**Produces:** `RouteStore` and `RouteSnapshot` defined above, persisted at a configurable path outside the image.

- [x] Write tests for valid `original`/`phone-guy` rows; reject empty or malformed extension IDs, unknown profiles, duplicate/unknown JSON fields, unsupported schema versions, missing profiles, and empty configs.
- [x] Write a stale-revision test: updating with revision `n-1` returns a conflict and leaves the exact file bytes and active snapshot unchanged.
- [x] Write an atomic-write failure test using an injected filesystem/rename function: a failed temp write, sync, or rename leaves the prior valid config readable and never publishes a partial file.
- [x] Run `go test ./internal/voiceconfig -run 'Test(Load|Update|Reject|Revision|Atomic)' -count=1`; confirm failure before implementation.
- [x] Implement strict JSON decode with `DisallowUnknownFields`, schema version `1`, monotonically increasing `uint64` revision, explicit extension allowlist, profile validation, and a mutex-protected `Snapshot`/`Update` store.
- [x] Persist by writing a mode-0600 temporary sibling file, syncing it, renaming it over the active file, then syncing the containing directory. Inject the filesystem operations so failure behavior remains testable.
- [x] Ensure configuration contains no SIP/ARI/RVC credentials; log only extension count, revision, and stable error class.
- [x] Run the focused package tests and `go test ./...`; commit the package and example config.

## Task 4: Implement authenticated admin API in Go

**Files:** Create `internal/admin/auth.go`, `session.go`, `routes.go`, `handlers.go`, `handlers_test.go`; modify `cmd/voice-web/main.go`, `main_test.go`, `Dockerfile`, `compose.yaml`.

**Consumes:** Task 2 handler/static layout and Task 3 `RouteStore`.
**Produces:** Same-origin login/logout and read/update APIs from the interface section.

- [ ] Add handler tests for: anonymous GET/PUT denied; valid login succeeds; wrong password returns a generic `401`; logout expires the cookie; missing/mismatched Origin and missing CSRF token reject writes; successful GET returns no secrets; successful update changes one extension and increments revision; stale revision returns `409`; unknown extension/profile returns `422` without a disk write.
- [ ] Add a secret-redaction test that sends a known password and proves it is absent from every response body and captured application log.
- [ ] Run `go test ./internal/admin -count=1` and confirm the authorization and stale-write tests fail before implementation.
- [ ] Load the one operator password from `VOICE_ADMIN_PASSWORD_FILE`, mounted read-only outside Git. Use constant-time comparison, short-lived signed session cookie (`Secure`, `HttpOnly`, `SameSite=Strict`), server-side expiry, same-origin validation, and a session-bound CSRF token required on `PUT` and logout.
- [ ] Implement `POST /admin/api/v1/session`, `DELETE /admin/api/v1/session`, `GET /admin/api/v1/voice-routes`, and `PUT /admin/api/v1/voice-routes/{extension}`. Cap request bodies at 4 KiB, reject unknown JSON fields, and use the route snapshot revision as the optimistic concurrency token.
- [ ] Return bounded semantic error codes only (`unauthorized`, `invalid_route`, `stale_revision`, `config_unavailable`); never expose filesystem paths, credentials, stack traces, or audio.
- [ ] Run `gofmt`, `go test ./internal/admin ./cmd/voice-web`, and `go test ./...`; commit API/auth after all handler tests pass.

## Task 5: Build the React phone-profile admin app

**Files:** Create `admin-ui/package.json`, lockfile, `index.html`, `vite.config.ts`, `tsconfig.json`, `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/styles.css`, and component tests; update `Dockerfile` build stage and `cmd/voice-web/main_test.go` for the built output.

**Consumes:** Task 4 API/session contract.
**Produces:** A React SPA compiled to `web/admin/`, served at `/admin/` from the same origin.

- [ ] Add Vitest and Testing Library to the Vite project, then write component tests for loading phone profiles, displaying current roles, selecting `original` or `phone-guy`, successful save with the current revision, stale save conflict/refetch, service unavailable state, and login-required state.
- [ ] Run the UI unit suite and confirm these tests fail before writing the components.
- [ ] Build one focused application screen: a compact sidebar with a single “Phones” destination, page heading and private-service status, then a table of configured SIP extensions with profile selector and explicit save feedback. Do not add unrelated settings, dashboard metrics, account management, or voice-model editing.
- [ ] Match the reference's light slate background, white bordered surfaces, blue primary action, compact typography, status badges, focus rings, and responsive table/card behavior. Implement the styling locally; do not import files or package dependencies from Proactive Monitoring.
- [ ] Keep credentials in the login form only; keep the session cookie HttpOnly and CSRF token in memory, never local storage. Send API requests same-origin with `credentials: "same-origin"` and the current config revision.
- [ ] Add English and Russian copy in a local typed dictionary; ensure labels/errors are accessible and all controls have keyboard/focus support.
- [ ] Add the Vite production build to the Docker build stage, outputting generated files into `web/admin/`; run `npm ci`, `npm test -- --run`, `npm run build`, `go test ./cmd/voice-web`, and the container build. Commit source, lockfile, and build integration, but not generated output if `.gitignore` already treats it as build-only.

## Task 6: Port the Python RVC v2 client to Go without changing inference

**Files:** Create `internal/rvc/client.go`, `stream.go`, `client_test.go`; update `go.mod` and `go.sum`; read `conference/rvc.py` and `rvc_service/server.py` without changing the Python worker.

**Consumes:** Task 1's captured handshake and exact v2 status metadata.
**Produces:** `RVCClient.Open`, `RVCStream.SendFrame`, `Outputs`, and idempotent `Close`.

- [ ] Add WebSocket protocol tests for start/ready, warmup, 20 ms frame pacing, ordered one-second output blocks, stop, busy, malformed ready metadata, invalid block size/sample indexes, text control messages, disconnect, and backpressure.
- [ ] Assert sent input frames are exactly 1,920 bytes and accepted output blocks exactly 96,000 bytes with contiguous metadata; no malformed block is published to the caller.
- [ ] Run `go test ./internal/rvc -count=1` and confirm the protocol tests fail before implementation.
- [ ] Add `github.com/gorilla/websocket` as the single Go WebSocket dependency; pin it in `go.mod`/`go.sum`. Keep one reader and one writer per connection, bounded queues, context-aware deadlines, disabled compression, and loopback-only URL validation.
- [ ] Implement the existing Python v2 handshake/control contract verbatim. On `busy`, `overloaded`, `stalled`, bad metadata, timeout, or socket close, invalidate the stream and return a safe typed error; never synthesize a raw output.
- [ ] Run `gofmt`, `go test ./internal/rvc -count=1`, `go test -race ./internal/rvc`, and `go test ./...`; commit the adapter.

## Task 7: Port Asterisk ARI and channel-media ownership to Go

**Files:** Create `internal/ari/client.go`, `events.go`, `bridge.go`, `media_ws.go`, `client_test.go`; read `conference/asterisk.py`, `conference/media.py`, `selfmonitor/ari.py`; modify the Go HTTP mux only for the private incoming media path.

**Consumes:** Task 1's ARI deployment state and Task 6's RVC stream for later tasks.
**Produces:** An injectable ARI client and owned media-channel adapter; no route policy yet.

- [ ] Add fake-transport tests for authenticated REST calls; ARI event subscription/reconnect; `StasisStart`/`ChannelDestroyed`; bridge create/add/delete; channel create/answer/dial/hangup; WebSocket `MEDIA_START`, `MEDIA_XOFF`, `MEDIA_XON`, `HANGUP`; and exact binary PCM frame validation.
- [ ] Add partial-failure tests proving cleanup deletes only IDs created by this session; a channel-create collision never deletes another caller's resource.
- [ ] Add backpressure tests proving XOFF/XON bounds the receive queue and the service does not accumulate unbounded audio.
- [ ] Run `go test ./internal/ari -count=1` and confirm the fake ARI tests fail before implementation.
- [ ] Implement authenticated ARI REST/events against loopback `127.0.0.1:8092` using a read-only mounted ARI secret file. The application must not accept ARI URLs or credentials from the browser.
- [ ] Implement Asterisk incoming `chan_websocket` media handling with explicit channel-ID ownership, `slin48` negotiation, control-message validation, a bounded frame queue, deadlines, and idempotent close/hangup/delete operations.
- [ ] Run `gofmt`, package tests and `go test -race ./internal/ari`; commit the adapter.

## Task 8: Route every phone call by its SIP identity

**Files:** Create `internal/calls/router.go`, `session.go`, `bridge.go`, `router_test.go`; extend route config/API tests if an interface change is required; add synthetic Asterisk call fixtures under `tests/`.

**Consumes:** Tasks 3, 6, and 7.
**Produces:** One Go call controller that resolves both caller and callee endpoint IDs and applies the route snapshot to every call.

- [ ] Write fake-ARI tests using synthetic fixture identities for caller and callee, direct calls and conference entry. For each `phone-guy` source, assert its channel is added to the bridge with source-to-bridge audio muted before bridge media can flow, the processed-media channel is audible, and no raw-source frame is delivered to other participants. Also cover the live `1900` callback route to a configured SIP endpoint. These fixture IDs are not production endpoints.
- [ ] Write an `original` route test proving ordinary calls retain their existing bidirectional audio without opening RVC.
- [ ] Write fail-closed tests for missing/unknown endpoints, invalid profile, RVC busy, RVC unavailable, malformed blocks, timeout, channel destruction during warmup, and cancellation. In every case assert no raw source ever entered an audible bridge and all owned ARI resources were cleaned.
- [ ] Write concurrency tests proving a second processed speaker receives an explicit busy/reject result and cannot enter the audible bridge while the first RVC session is active.
- [ ] Run `go test ./internal/calls -count=1` and verify all tests fail before the routing implementation.
- [ ] Snapshot the route revision before call admission. For `original`, bridge normally. For `phone-guy`, attach the raw source with its source-to-bridge audio muted at the moment it joins, capture only its incoming 20 ms PCM16LE frames, feed them to `RVCClient`, split each validated one-second response into 50 frames, and inject only the processed channel as audible speech. The source channel still receives normal bridge audio. Do not apply mid-call admin edits to active sessions.
- [ ] Reject calls involving unmapped endpoint IDs instead of guessing a default. For capacity or processing errors, tear down/reject the affected processed call leg without moving raw media to another bridge.
- [ ] Implement call teardown as one idempotent owner operation; it cancels tasks, closes RVC, removes only owned media channels/bridges, and records a bounded safe outcome without audio or secrets.
- [ ] Run `go test ./internal/calls`, `go test -race ./internal/calls`, and `go test ./...`; commit the route controller.

## Task 9: Preserve browser conference listening in Go

**Files:** Create `internal/conference/session.go`, `server.go`, `session_test.go`, `server_test.go`; read `conference/session.py`, `server.py`, `scenario.py`, `web/conference/`; modify `cmd/voice-web/main.go` route registration.

**Consumes:** Task 7 ARI/media adapters and the current Go static server.
**Produces:** Existing `/ws/conference` listener and `/conference/` page behavior, implemented in Go.

- [ ] Port the wire-contract tests: exact first `listen` control, exact later `stop`, ready metadata, binary-only 20 ms frames, allowed origin, timeout, listener limit, one shared converted C source, and no microphone permission on the listener page.
- [ ] Port session tests for last-listener cleanup, a new listener not closing an older run, expiry, bounded slow-listener removal, busy model, stalled output, and partial ARI cleanup.
- [ ] Run the new Go package tests and the unchanged `node --test tests/conference-worklet.test.cjs tests/conference.test.cjs` contract tests; confirm new Go tests fail before implementation.
- [ ] Implement the same status/error wire schema and bounded session lifecycle in `internal/conference`; use the shared Go ARI and RVC adapters rather than a second model connection.
- [ ] Run `go test ./internal/conference`, `go test -race ./internal/conference`, Go full tests, and the focused Node tests; commit the Go listener before retiring Python deployment.

## Task 10: Port the live `1999` browser-audio mirror service to Go

**Files:** Create `internal/selfmonitor/relay.go`, `service.go`, and tests; read `selfmonitor/service.py`, `selfmonitor/ari.py`, `selfmonitor/live_check.py`, the installed VM209 `/opt/voice-selfmonitor/app/selfmonitor/service.py`, `deploy/voice-selfmonitor.service`, `deploy/deploy-selfmonitor.sh`, `web/live/`, and `docs/SELFMONITOR_2026-09-16.md`.

**Consumes:** Task 7's ARI/media ownership; existing `1999@phoneguy-sip → Stasis(selfmonitor)` and loopback `/ws/live-mirror` publisher behavior.
**Produces:** A Go-owned ARI `selfmonitor` app and same-origin `/ws/live-mirror` relay, preserving the live mirror behavior and eliminating the standalone Python self-monitor runtime.

- [ ] Write relay tests for one publisher, allowed origin and exact `/ws/live-mirror` path, binary-only 1,920-byte PCM frames, a bounded 50-frame FIFO, eight-frame prebuffer, paced 20 ms injection, silence before prebuffer/after underrun, publisher disconnect and cleanup.
- [ ] Write ARI session tests for answering `1999`, creating the owned injection channel and bridge, feeding only mirror frames to the SIP line, caller hangup, busy second caller, partial setup, service shutdown and idempotent cleanup. Assert this mirror path does not open RVC or forward caller microphone audio.
- [ ] Preserve the loopback readiness listener on 8096 and mirror publisher on 8097 until callers are verified to use the same-origin Go routes; do not silently remove an existing client contract.
- [ ] Run `go test ./internal/selfmonitor -count=1` and confirm the relay/session tests fail before implementation.
- [ ] Implement the mirror as a Go ARI app with the shared ARI/media adapter and bounded relay state; it does not acquire the RVC session gate.
- [ ] Keep the `1999` dialplan and browser `/live/` publisher behavior stable. Run package and race tests plus the synthetic live-check protocol test before changing its deployed unit.
- [ ] Commit the Go mirror implementation; keep the old Python unit/script available for rollback until Task 12 acceptance passes.

## Task 11: Route configured endpoints into Go and package one private Go runtime

**Files:** Modify `conference/asterisk/extensions.conf`, `deploy/Caddyfile`, root `compose.yaml`, `deploy/compose.goweb.yaml`, `deploy/compose.conference.yaml`, `Dockerfile`, `Dockerfile.goweb`, `deploy/deploy.sh`, `deploy/deploy-rvc.sh`, `deploy/deploy-conference.sh`, `deploy/deploy-selfmonitor.sh`, and deployment contract tests.

**Consumes:** Tasks 2–10; Task 1's verified PJSIP inventory and port/interface ownership.
**Produces:** A scoped Go runtime that serves HTTP/React/API and owns conference, self-monitor, and SIP call control. The only Python production runtime is the unchanged RVC worker.

- [ ] Add deploy-script tests for exact VM209-only target, read-only preflight, existing dirty/source preservation, backup creation, restore of prior Compose/image/Caddy/dialplan/service states, and nonzero status for any incomplete rollback.
- [ ] Add Asterisk config tests that every configured endpoint's incoming and outbound call route reaches `Stasis(voice-control)` before native `Dial`/ConfBridge media can become audible. Preserve `1999`'s self-monitor destination and existing no-recording behavior.
- [ ] Run the targeted Go and deployment contract tests; verify all deployment tests fail closed before changing the script.
- [ ] Consolidate the reviewed Go build into the production Dockerfile; the image contains the Go app and generated React assets but no Python interpreter, RVC packages, model weights, or ARI password.
- [ ] Run the Go runtime with the minimum network visibility needed to reach loopback ARI and RVC. Bind the web listener only to the explicitly verified VM LAN/private address; do not publish ARI, RVC, SIP, or RTP ports. Mount admin/ARI/config secrets read-only except the narrowly scoped route-config directory.
- [ ] Add the route config bootstrap from the verified endpoint inventory with each phone explicitly set to its approved initial profile. Never import example extensions blindly and never include SIP credentials in the role file.
- [ ] Extend the existing scoped deployment/rollback mechanism to back up the exact prior HTTP image/source, Go/Compose version, Caddy file, dialplan, route config, and active/enabled unit/container states. A failure must restore the exact previous live state and verify Go/Python RVC readiness; do not start the intentionally stopped legacy HTTP image as a fallback shortcut. Update every routine web/RVC/conference/self-monitor deployment entry point so it cannot restart or redeploy the retired Python HTTP/controller runtime after cutover.
- [ ] Stage the Go runtime privately on VM209, run its loopback health/config check and synthetic no-call test, then switch Caddy only after those gates pass. Do not restart the RVC inference service or Asterisk while calls are active.
- [ ] Verify the admin URL and API remain VPN-only, role config survives a Go restart, and live profile changes affect only newly admitted calls.
- [ ] Keep the Python conference and self-monitor units stopped/disabled only after the Go equivalents pass. Retain exact rollback artifacts. Update deploy scripts so future routine deploys cannot restart Python conference/self-monitor or restore the old HTTP container.
- [ ] Run deployment tests and verify a controlled rollback in the isolated deploy harness before enabling the Go call route for a real phone.
- [ ] Commit only the deployment and Asterisk configuration changes covered by the passing rollback contract.

## Task 12: Complete private acceptance and integrate the result on `main`

**Files:** Update `docs/OPERATIONS.md`, `docs/CONFERENCE.md`, `docs/SELFMONITOR_2026-09-16.md`, `docs/LIVE_STATUS.md`, `README.md`, and final runtime inventory/acceptance tests.

**Consumes:** All previous tasks and a healthy VM209 read-only baseline.
**Produces:** Evidence-backed Go runtime on `main`, Python limited to RVC inference, and a verified rollback.

- [ ] Run unit/type/build suites: `go test ./...`, `go test -race ./...`, `cd admin-ui && npm ci && npm test -- --run && npm run build`, Node browser contract tests, and deployment script/config tests.
- [ ] Run the local Asterisk/ARI fake call matrix for caller/callee, original/processed, direct calls, conference entry, unknown endpoints, concurrent AI participants, model busy/failure, malformed blocks, and disconnect cleanup.
- [ ] On VM209, verify RVC `/healthz` ready before and after Go deployment, Go `/healthz`, config revision persistence, active Go ARI app, exact extension route list, loopback port ownership, memory/GPU headroom, and no Python conference/self-monitor runtime.
- [ ] Use synthetic audio first. For each profile, verify exact sample continuity, non-silent processed output, no raw source in an audible bridge, call teardown, busy outcome, and bounded queues. Do not store WAVs or caller audio.
- [ ] Place a private device call from and to the assigned phone, then repeat through a conference. Verify that every call involving the assigned extension processes its outgoing speech and receives normal return audio; change the profile during a call and verify the active call remains unchanged while the next call uses the new profile.
- [ ] Test a second simultaneous `phone-guy` call and forced RVC unavailability. The call is rejected/terminated; never permit a raw voice fallback.
- [ ] Exercise the scoped rollback on the recorded release stamp and verify the prior service/Caddy/dialplan/config states and Python RVC readiness. If rollback does not fully verify, stop and report `ROLLBACK_FAILED`; do not claim completion.
- [ ] Compare final `systemctl`/container/runtime inventories with the spec. Confirm Go owns web/admin/SIP/ARI/conference/self-monitor services and Python remains only for RVC inference among production application services.
- [ ] Update operations docs with exact login setup location (no password), profile assignment, endpoint inventory, health commands, safe busy/error behavior, deploy/rollback command and unresolved limits. Do not document unverified physical results.
- [ ] Run `git diff --check`, inspect every changed file, verify unrelated dirty files and the Proactive Monitoring repository remain untouched, and commit only the feature files. Integrate the reviewed commits on `main`; do not force-push or stage unrelated untracked files.

## Verification and Reporting

- Report every Go, React, Node, deploy, synthetic call, and physical call check actually run; separate local contract evidence from VM/device evidence.
- Report the final branch/commit, runtime inventory, configured extension/profile map without secrets, deployment stamp, and rollback result.
- Call out any physical phone, VPN, Caddy, or listening check that was unavailable; do not substitute a process health check for audio proof.
- Do not claim all Python is removed: the accepted target explicitly retains Python for the RVC inference service and may retain non-runtime test/deployment tooling.
