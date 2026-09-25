# Load Monitoring and Capacity Estimate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Show current Go/RVC processing load in the admin and report both the enforced concurrent-call limit and an evidence-based potential capacity estimate.

**Architecture:** The Go service aggregates bounded RVC block-processing measurements and call-gate occupancy, polls the existing private Python RVC health endpoint, and reads VM/GPU metrics only through a verified least-privilege local source. A same-origin admin API provides freshness-aware summaries to a Load page. A controlled isolated candidate benchmark may update a secret-free capacity record; it never changes production admission limits.

**Tech Stack:** Go standard library, existing React/TypeScript/Vite app, Python RVC worker `/healthz`, current Asterisk/Go/RVC deployment and existing GPU tooling if its access can be constrained.

**Spec:** `docs/superpowers/specs/2026-09-25-phone-discovery-load-capacity-design.md`; canonical requirements: `docs/VOICE_ADMIN_REQUIREMENTS.md`.

## Global Constraints

- The current Go gate and production Python worker each allow one active processed session; the effective live limit is one.
- Keep **Allowed now** separate from **Measured estimate**. Missing or stale benchmark data means “not measured.”
- Metrics must not collect audio, transcripts, SIP/ARI secrets, provisioning responses, or unbounded per-block histories.
- Read-only metrics failure must never block or change SIP call processing.
- Do not grant the Go service Docker-socket access, broad host privileges, or a new unauthenticated network listener.
- Benchmark-only multi-session behavior must run in an isolated candidate and must not alter production admission configuration.
- Preserve dirty user files, especially `internal/rvc/stream.go`, `internal/calls/gate.go`, `cmd/voice-web/main.go`, `admin-ui/src/App.tsx`, and `deploy/compose.goweb.yaml`.
- Do not add or run automated tests unless the user separately asks for testing. Use formatting, build, configuration, and read-only runtime checks.

## Review Focus

- Malformed or extreme `processingMs` values cannot corrupt or grow telemetry without bound.
- Failed RVC polling is “unknown/stale,” not a false report that the worker is down or idle.
- Gate occupancy and Python worker active state are distinct inputs; API must not double-count them as two calls.
- Missing host/GPU metric access must degrade the page rather than cause privileged mounts.
- A benchmark from the experimental/demo worker or historical notes cannot be shown as production capacity.

## File Map

- `internal/telemetry/snapshot.go`, `collector.go`: bounded rolling RVC latency/error summaries, health source status, and capacity estimate persistence/read.
- `internal/rvc/stream.go`, `client.go`: pass validated `processingMs` observations through an optional narrow observer interface.
- `internal/calls/gate.go`, `router.go`: expose safe active-lease occupancy without exposing gate internals or allowing metric consumers to mutate admission.
- `internal/rvc/health.go`: timed private HTTP polling and strict decoding for the production RVC health schema.
- `internal/hostmetrics/`: host/service CPU, RAM, and GPU provider only if Task 1 proves a read-only, least-privilege data source.
- `internal/admin/metrics_handlers.go`, `handlers.go`, `cmd/voice-web/main.go`: same-origin snapshot endpoint using existing admin protections.
- `admin-ui/src/api.ts`, `App.tsx`, `styles.css`: Load navigation and bounded freshness-aware panels.
- `tools/capacity-bench.py` or a similarly scoped existing operations tool: paced concurrent sessions using the production model on an isolated candidate, without audio artifacts.
- `deploy/compose.goweb.yaml`, candidate-only Compose/config, and deployment scripts only if a narrow read-only metric mount or isolated benchmark target is required.
- `docs/OPERATIONS.md`: describe metric meanings, freshness thresholds, benchmark command, rollback, and the rule that capacity data never changes admission.

## Execution Tasks

### Task 1: Verify metric access and capacity benchmark prerequisites

**Files:** No edits.

**Produces:** A non-secret source map for RVC health polling, Go container/call gate metrics, host CPU/RAM/GPU access, and isolated candidate operation.

- [ ] Inspect actual service ownership and ports from deployment scripts/Compose, not old docs alone. Confirm RVC health URL is loopback-only and Go's deployment origin is private.
- [ ] Identify whether the Go container can safely see only its own CPU/RAM cgroup counters and whether a read-only host/GPU provider already exists. Do not use Docker socket or host PID namespace as a shortcut.
- [ ] Confirm candidate RVC can load the exact production model/profile on the VM GPU while the normal production route is idle; record a safe way to isolate ports/process/config.
- [ ] If safe VM/GPU access is unavailable, keep those metric cards unavailable. If an isolated multi-session candidate cannot be run without affecting live calls, do not publish a measured estimate above the current limit.

### Task 2: Add bounded RVC and call-gate telemetry

**Files:** Create `internal/telemetry/snapshot.go` and `collector.go`; modify `internal/rvc/stream.go`, `internal/rvc/client.go`, `internal/calls/gate.go`, and `internal/calls/router.go`.

**Consumes:** Task 1 source map and existing RVC `processingMs` contract.

**Produces:** A concurrency-safe `Collector` with `ObserveRVCBlock(processing time.Duration)`, `ObserveRVCError(code string)`, `SetRVCHealth(HealthSample)`, and `Snapshot(now time.Time) TelemetrySnapshot`. Snapshot contains active processing calls, the configured enforced limit, bounded recent p50/p95 processing latency, bounded error counts, RVC active/running/queue state, and per-source freshness.

- [ ] Define `HealthSample`, source status, and JSON API types without credential/audio fields. Store only a fixed-size rolling window or histogram; evict observations older than the documented window.
- [ ] Expose gate occupancy as read-only `Active()`/`Limit()` methods. Keep Acquire/Release semantics private to call admission; metrics cannot reserve or release sessions.
- [ ] Add an optional RVC observer interface implemented by the collector. Invoke it only after existing metric validation succeeds; do not change WebSocket protocol or use callback failures to affect audio processing.
- [ ] Record only bounded error codes already classified by the Go RVC client. Avoid endpoint, caller ID, channel name, raw WebSocket control frame, and PCM data.
- [ ] Run `gofmt` and `go build ./internal/telemetry ./internal/rvc ./internal/calls`.

### Task 3: Poll the production RVC health endpoint

**Files:** Create `internal/rvc/health.go`; modify `cmd/voice-web/main.go` and `deploy/compose.goweb.yaml` only if an explicit health URL is needed.

**Consumes:** Task 2 collector and `rvc_service/server.py` health schema.

**Produces:** A poller that reads `status`, `active`, `running`, and `queuedWindows` from `http://127.0.0.1:8090/healthz` with a short timeout, records the last success, and marks the sample stale after the documented threshold. Poll interval: 5 seconds; request timeout: 2 seconds; stale after 15 seconds.

- [ ] Strictly decode the known fields, reject invalid status/count types and unexpectedly large bodies, and never include body text in logs or admin responses.
- [ ] Run the poller under a cancellable service lifecycle. Poll failures update source freshness/error class but do not stop Go routes or call control.
- [ ] Wire health state into `telemetry.Collector`; health readiness, active, running, and queue remain separate values.
- [ ] Keep health requests private loopback and outside the browser's direct network access.
- [ ] Run `gofmt` and `go build ./cmd/voice-web`.

### Task 4: Add safe host and GPU metrics where available

**Files:** Create `internal/hostmetrics/` and add an interface implementation selected by Task 1; modify Compose only for narrowly scoped read-only paths if the provider requires them.

**Consumes:** Task 1 verified host metric capabilities and Task 2 snapshot types.

**Produces:** Optional CPU, RAM, GPU utilization, and VRAM samples with source timestamp and explicit unavailable state. If the least-privilege source is not proven, no provider or mount is added.

- [ ] Implement the provider using only the verified source: own cgroup/proc counters where visible and an existing restricted GPU metrics path/tool if available. Do not elevate Go capabilities or grant access to `/var/run/docker.sock`.
- [ ] Validate numeric ranges, timestamps, sample age, and missing GPU data; report unknown rather than zero when unsupported.
- [ ] Keep polling bounded and cancellable; source failure cannot affect call admission.
- [ ] Run `gofmt` and `go build ./internal/hostmetrics ./cmd/voice-web` when this provider is included.

### Task 5: Expose metrics and build the Load page

**Files:** Create `internal/admin/metrics_handlers.go`; modify `internal/admin/handlers.go`, `cmd/voice-web/main.go`, `admin-ui/src/api.ts`, `App.tsx`, and `styles.css`.

**Consumes:** `telemetry.Collector.Snapshot(now)` and existing admin auth/origin policy.

**Produces:** `GET /admin/api/v1/metrics` returning a no-store, same-origin bounded snapshot. React adds a separate **Load** navigation item and shows “Allowed now: 1” separately from “Measured estimate: not measured.”

- [ ] Register the API handler with the existing authorization middleware and return no privileged host detail beyond the approved metrics schema.
- [ ] Add TypeScript types for freshness, source status, latency summaries, gate occupancy, worker state, optional host metrics, and optional capacity record.
- [ ] Add a Load page with service/RVC status, calls active/limit, RVC work queue, recent block latency, CPU/RAM/GPU cards where available, last-updated time, and explicit stale/unavailable states.
- [ ] Show the enforced effective limit as one until both admission gates are deliberately changed. Read and display a capacity record only if it meets the schema/version/provenance rules; otherwise show “not measured.”
- [ ] Run `gofmt`, `go build ./cmd/voice-web`, and `npm --prefix admin-ui run build`.

### Task 6: Create and run an isolated multi-session capacity benchmark

**Files:** Create a scoped tool under `tools/` and candidate-only configuration/Compose as needed; update `docs/OPERATIONS.md` with exact invocation and result schema.

**Consumes:** Task 1's isolated target, production worker/model path, and Task 5 telemetry/record schema.

**Produces:** A reproducible non-secret benchmark result with concurrency, run duration, production profile, software/model identity, p50/p95 processing time per one-second block, continuity/lag trend, overloads/queue high-water, process restarts, and CPU/RAM/GPU/VRAM peaks. No audio output or PCM is persisted.

- [ ] Add benchmark-only multi-session admission to a candidate build/config, isolated from live production service and ports. Keep production Go and Python limits at one.
- [ ] Generate synthetic paced PCM in memory, start clients together, validate each session's ordered one-second metrics/output, and discard output buffers after calculating required continuity/finite-value summaries.
- [ ] Increase concurrency in controlled steps and sustain each candidate level for the documented interval. Abort on any active production call, RVC stall, restart, queue growth beyond its bounded limit, or insufficient GPU/RAM headroom.
- [ ] Accept a candidate count only when p95 processing stays below its one-second block budget, lag remains stable, sessions do not overload, no output gaps occur, and no process restarts occur in repeated runs. Apply a stated safety margin below the highest passing count.
- [ ] Store only the summary/timestamp and set measured capacity to “not measured” if any acceptance evidence is missing. Do not modify production call admission as a result.
- [ ] Run this benchmark only as the authorized capacity-measurement operation after a dedicated idle-call preflight; never launch it alongside live processed calls.

### Task 7: Roll out and verify without changing admission behavior

**Files:** Modify only the scoped Go/UI/deploy/docs paths from Tasks 2-6.

**Consumes:** Complete metrics page, verified metric providers, and candidate benchmark result or explicit “not measured” state.

**Produces:** A deployed dashboard that reports current load and separates current service limit from measured model/hardware potential; production remains capped at one.

- [ ] Build candidate images and validate Compose/Caddy configuration before deployment. Save scoped rollback artifacts.
- [ ] Confirm no active processed call before service changes. Deploy the Go/UI metrics feature while leaving production RVC model, worker limit, and Go gate unchanged.
- [ ] Verify health poll freshness, a live call increments then clears the active count, block-latency samples update, and unavailable host/GPU data remains visibly unknown.
- [ ] Run the isolated benchmark only if Task 6's idle and isolation conditions are satisfied. Restore the candidate/production service layout afterward and verify RVC ready and production gates remain one.
- [ ] Confirm the capacity record shows its run timestamp and headroom, or continues to say “not measured.” Preserve exact rollback instructions and current service ownership in `docs/OPERATIONS.md`.

## Scope Boundary

This plan measures and displays possible compute capacity. It does not raise the production Go gate or Python worker limit. Raising either limit requires a separate approved design and rollout after the benchmark supports a safe value.
