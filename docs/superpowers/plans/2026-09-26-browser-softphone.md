# Browser Softphone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users call configured internal extensions from `/phone/` and ring an active browser client alongside that extension’s physical phone.

**Architecture:** React uses SIP.js over the private VM's `vm-voice-1` WSS route to register a temporary WebRTC endpoint in Asterisk; same-origin HTTPS Go APIs own the browser lease and directory. Go owns extension identity, call admission, ARI orchestration, profile selection, and parallel target-leg cleanup; Asterisk remains private and RVC remains the existing Python service. Asterisk feasibility and lease revocation are a release gate before building the full browser flow.

**Tech Stack:** Go, Asterisk 22.11 PJSIP/ARI, React + TypeScript + SIP.js 0.21.2, Caddy, existing Python RVC service.

**Spec:** `docs/superpowers/specs/2026-09-26-browser-softphone-design.md`

## Global Constraints

- Internal configured extensions only; no PSTN, external SIP URIs, or arbitrary channel names.
- At most one active browser session per extension; heartbeat every 10 seconds; expire after 30 seconds; clean disconnect releases immediately.
- Do not reuse handset SIP credentials. Return only temporary session SIP credentials to that session over HTTPS; never persist or log credentials.
- Route all calls through Go and ARI; map SIP endpoint identity to a configured logical extension, never trust caller ID text.
- Ring the physical phone and active browser endpoint concurrently; connect one winner and clean up all losing legs.
- `phone-guy` must fail closed through RVC; `original` remains unprocessed; preserve the one simultaneous processed-call limit.
- Do not record calls or retain PCM/transcripts; keep WSS, RTP, ARI, and RVC private to VPN/LAN; do not open public firewall paths.
- Preserve unrelated dirty work. The initial owner-transfer script refuses an existing Go owner; use the owner-aware `deploy/update-goweb.sh` and its snapshot rollback for updates.

## Review Focus

- Asterisk dynamic endpoint creation or deletion fails or leaves a live contact: lease reuse must stay blocked until registration and owned channels are confirmed gone. Pin with `TestLeaseNotReusableUntilAsteriskRevoked` and Asterisk integration checks in Task 1/2.
- Two concurrent claims race for one extension: only one session and credential may be issued. Pin with `TestClaimIsAtomicPerExtension` in Task 3.
- Browser and handset answer near-simultaneously: exactly one leg wins and every other leg is canceled. Pin with `TestParallelRingSingleWinnerOnConcurrentAnswers` in Task 4.
- A browser submits forged caller identity, target, origin, or channel values: reject it and route only using the session mapping and configured directory. Pin with API/router rejection tests in Tasks 3/4.
- WebRTC media cannot traverse private VPN/Caddy or RVC fails during a processed call: show bounded failure, expose no raw-audio fallback, and keep public SIP/RTP closed. Pin with private acceptance checks in Tasks 1/5.

---

## File Map

- `conference/asterisk/modules.conf`, `conference/asterisk/Dockerfile`, `conference/asterisk/pjsip.conf.template`, `conference/asterisk/sorcery.conf`, `conference/asterisk/healthcheck.sh`: load and require WebSocket transport support, define a static WSS transport, and route dynamic auth/AOR/endpoint objects to volatile Sorcery memory while retaining static PJSIP objects from `pjsip.conf`.
- `internal/ari/client.go`: narrow validated ARI dynamic-PJSIP create/delete and channel operations; never log response bodies containing secrets.
- `internal/webphone/`: session leases, directory validation, temporary SIP credential lifecycle, and lease cleanup coordination.
- `internal/admin/handlers.go`, new `internal/webphone/handlers.go`, `cmd/voice-web/main.go`: same-origin session and directory APIs, session liveness, identity mapping, and route wiring.
- `internal/calls/router.go`, `internal/calls/controller.go`: trusted logical caller resolution and multi-target parallel dialing/winner cleanup while preserving the existing RVC gate and callback path.
- `admin-ui/package.json`, `admin-ui/src/phone/`, `admin-ui/vite.config.ts` or a dedicated phone entry, `cmd/voice-web/main.go`: SIP.js browser client and `/phone/` static route; keep `/admin/` intact.
- `deploy/Caddyfile.goweb`, `deploy/compose.goweb.yaml`, `deploy/compose.goweb.stage.yaml`, `deploy/deploy-goweb.sh`, `deploy/rollback-goweb-production.sh`, `Dockerfile.goweb`: private VM WSS proxy, signaling URL, and build/release changes through the current production owner path.
- `docs/VOICE_ADMIN_REQUIREMENTS.md` and `docs/OPERATIONS.md`: replace completed backlog items with the shipped behavior and operational constraints.

## Task 1: Prove Private Asterisk WebRTC and Revocation Path

**Files:**
- Modify: `conference/asterisk/modules.conf`
- Modify: `conference/asterisk/Dockerfile`
- Modify: `conference/asterisk/pjsip.conf.template`
- Create: `conference/asterisk/sorcery.conf`
- Modify: `conference/asterisk/healthcheck.sh`
- Test: `tests/test_browser_webrtc_config.py` (new static config contract checks)
- Test: private VM integration procedure recorded in `docs/OPERATIONS.md`

**Interfaces:**
- Produces: Asterisk modules for PJSIP WebSocket transport and WebRTC, a static WSS PJSIP transport on the existing HTTP service, a private host-loopback HTTP publication, and an ARI dynamic-object contract whose auth/AOR/endpoint can be provisioned and revoked for one extension session.
- Constraint: Do not proceed to Tasks 3–5 unless create/register/call/revoke succeeds and revocation is confirmed before endpoint reuse.

- [ ] **Step 1: Add failing config contract checks** named `test_websocket_transport_module_is_loaded`, `test_wss_transport_uses_http_websocket_server`, `test_dynamic_pjsip_objects_use_memory_with_static_fallback`, `test_ari_host_publication_stays_loopback`, and `test_rtp_range_remains_private`; require both new modules in the Asterisk health-check list.
- [ ] **Step 2: Run the new config checks** with `uv run --quiet --no-project --with pytest pytest -q tests/test_browser_webrtc_config.py`; expect failures for missing WebRTC transport support.
- [ ] **Step 3: Add `res_crypto` and `res_pjsip_transport_websocket` to the pinned build/runtime module sets and define only the static WSS transport in `pjsip.conf.template`.** Map `endpoint`, `auth`, and `aor` to `memory` first and the corresponding `config,pjsip.conf,criteria=type=...` source second in `sorcery.conf`; keep static transports/global config in the config driver. Browser credentials remain session-scoped and volatile. Keep UDP handset transport, physical auth, private RTP range, and host HTTP publication `127.0.0.1:8092:8092` unchanged.
- [ ] **Step 4: Run config checks and Asterisk validation** with `uv run --quiet --no-project --with pytest pytest -q tests/test_browser_webrtc_config.py` and the repository's Asterisk config validation command; expect all checks to pass and no public bind/firewall changes.
- [ ] **Step 5: Run a reversible private VM proof**: create a temporary endpoint through ARI, register a browser via private WSS, complete a test call/media path, delete endpoint objects, confirm registration/contact and channels are gone, and confirm a fresh lease can then be created. Record exact commands/results in the operations doc without secrets.
- [ ] **Step 6: Gate the rest of implementation.** If registration revocation or VPN media is unreliable, stop and update the design with the observed constraint; do not proceed by weakening privacy or bypassing Go/RVC.
- [ ] **Step 7: Commit** the Asterisk proof/config changes and its evidence separately.

## Task 2: Add Bounded ARI Dynamic-PJSIP Operations

**Files:**
- Modify: `internal/ari/client.go`
- Test: `internal/ari/client_test.go`

**Interfaces:**
- Consumes: Task 1 object shape and cleanup order.
- Produces: `PutDynamicPJSIP(ctx context.Context, kind, id string, fields map[string]string) error` and `DeleteDynamicPJSIP(ctx context.Context, kind, id string) error`, with allowlisted kinds (`auth`, `aor`, `endpoint`), identifier validation, bounded response bodies, and redacted errors.

- [ ] **Step 1: Add failing tests** `TestPutDynamicPJSIPRejectsUnknownKindOrID`, `TestPutDynamicPJSIPEncodesFieldsAndNeverLeaksResponseBody`, `TestDeleteDynamicPJSIPUsesExpectedResource`, and `TestDynamicPJSIPHonorsContextTimeout` using an `httptest` ARI server.
- [ ] **Step 2: Run** `go test ./internal/ari`; expect the new API/tests to fail before implementation.
- [ ] **Step 3: Implement** the two methods using the existing loopback ARI base URL and auth; cap/discard response bodies and never include credentials or response payloads in errors/logs.
- [ ] **Step 4: Run** `go test ./internal/ari`; expect all existing and new tests to pass.
- [ ] **Step 5: Commit** as `feat: add scoped dynamic pjsip ari operations`.

## Task 3: Implement Browser Session Leases and Directory APIs

**Files:**
- Create: `internal/webphone/store.go`, `internal/webphone/session.go`, `internal/webphone/handlers.go`
- Create: `internal/webphone/session_test.go`, `internal/webphone/handlers_test.go`
- Modify: `cmd/voice-web/main.go`
- Modify: `internal/admin/handlers.go` only for the secret-free directory/status view integration

**Interfaces:**
- Consumes: Task 2 ARI operations and existing configured phonebook extension inventory.
- Produces: `Claim(ctx, nickname, extension) (SessionView, TemporarySIPCredentials, error)`, `Heartbeat(ctx, sessionID) error`, `Release(ctx, sessionID) error`, `ActiveBrowser(extension) (BrowserTarget, bool)`, and `Directory() []DirectoryEntry`.
- `SessionView` and `DirectoryEntry` must contain no SIP password/auth secret; `TemporarySIPCredentials` are returned only once in the claim response.

- [ ] **Step 1: Add failing lease tests** `TestClaimIsAtomicPerExtension`, `TestConfiguredHandsetDoesNotBlockBrowserClaim`, `TestNicknameCanChangeOnNextSession`, `TestHeartbeatRenewsAtTenSecondCadence`, `TestExpiredLeaseIsNotReusableBeforeRevocation`, and `TestReleaseRevokesBeforeFreeingExtension` with fake clock and fake ARI.
- [ ] **Step 2: Add failing handler tests** `TestClaimRejectsUnknownExtensionAndMalformedNickname`, `TestSecondClaimReturnsConflict`, `TestClaimResponseAloneContainsTemporaryCredential`, `TestDirectoryAndAdminNeverExposeSecrets`, `TestHeartbeatRequiresSameOriginSession`, and `TestDisconnectReleasesLease`.
- [ ] **Step 3: Run** `go test ./internal/webphone`; expect failures before implementation.
- [ ] **Step 4: Implement** atomic in-memory leases with 10-second renewal and 30-second expiry, configured-extension validation, server-generated random session endpoint/auth values, ordered ARI revoke-before-release, and same-origin claim/heartbeat/release/directory handlers. Do not add persistent browser identity or credentials.
- [ ] **Step 5: Wire handlers** in `cmd/voice-web/main.go`; map the opaque server session to one logical extension and create no new public listener.
- [ ] **Step 6: Run** `go test ./internal/webphone ./internal/admin ./cmd/voice-web`; expect all relevant existing and new tests to pass.
- [ ] **Step 7: Commit** as `feat: add temporary browser phone sessions`.

## Task 4: Route Browser Identity and Ring Multiple Devices

**Files:**
- Modify: `internal/calls/router.go`, `internal/calls/controller.go`
- Test: `internal/calls/router_test.go`, `internal/calls/controller_test.go`
- Modify: `cmd/voice-web/main.go` for trusted browser ingress into the existing Stasis app

**Interfaces:**
- Consumes: Task 3 `ActiveBrowser(extension)` and `Directory()`; existing physical endpoint inventory, processing gate, profile store, and ARI client.
- Produces: trusted endpoint-to-extension resolution plus a managed call with a set of target channels and exactly one winning target; UI state events keyed by opaque call ID.

- [ ] **Step 1: Add failing router tests** `TestBrowserEndpointMapsOnlyFromActiveSession`, `TestRejectUnknownOrForgedBrowserIdentity`, `TestRouteSnapshotsSourceProfile`, and `TestExistingPhysicalRoutingRemainsUnchanged`.
- [ ] **Step 2: Add failing controller tests** `TestParallelRingSingleWinnerOnConcurrentAnswers`, `TestCancelAndCleanLosingTargetLeg`, `TestHangupCleansAllOwnedChannelsAndMedia`, `TestProcessedLeaseReleasedExactlyOnce`, `TestPhoneGuyFailsClosedWhenRVCUnavailable`, and `TestCallback1900PathUnchanged`.
- [ ] **Step 3: Run** `go test ./internal/calls`; expect the new browser/multi-leg cases to fail.
- [ ] **Step 4: Implement** session-backed logical source identity and configured target expansion (physical plus active browser). On first answer atomically select winner, cancel all remaining legs, and clean channels/bridges/media; on simultaneous answer events retain one winner and tear down every loser.
- [ ] **Step 5: Preserve routing invariants**: profile is snapshotted by logical source extension; `phone-guy` obtains the existing single processing token and fails closed; `original` remains unprocessed; callback-1900 and unrelated physical-only routes stay unchanged.
- [ ] **Step 6: Run** `go test ./internal/calls ./internal/webphone ./internal/ari`; expect all tests to pass with race-sensitive controller tests run under `go test -race ./internal/calls`.
- [ ] **Step 7: Commit** as `feat: ring browser and physical phone targets`.

## Task 5: Build the `/phone/` React Client and Private Signaling Route

**Files:**
- Create: `admin-ui/src/phone/PhoneApp.tsx`, `admin-ui/src/phone/sipSession.ts`, `admin-ui/src/phone/phone.css`, `admin-ui/phone.html`
- Modify: `admin-ui/package.json`, `admin-ui/vite.config.ts`, `Dockerfile.goweb`, `cmd/voice-web/main.go`
- Modify: `deploy/Caddyfile.goweb`, `deploy/compose.goweb.yaml`, `deploy/compose.goweb.stage.yaml`
- Test: `admin-ui/src/phone/PhoneApp.test.tsx`, `cmd/voice-web/main_test.go`

**Interfaces:**
- Consumes: Task 3 session/directory HTTP contract and Task 4 call-state contract; use SIP.js 0.21.2 pinned in the lockfile.
- Produces: `/phone/` page with `startSession`, `heartbeat`, `release`, `register`, `call`, `answer`, `decline`, and `hangup` flows; Go returns a configured, non-secret WSS URL and the VM Caddy proxies only `/ws/phone-signaling` to Asterisk HTTP `/ws`. UI/API remain on either existing web origin; SIP signaling goes directly to the private VM host so it also works when the UI is opened through the VPN VPS proxy.

- [ ] **Step 1: Add failing UI/API tests** `PhoneAppRequiresNicknameAndConfiguredExtension`, `PhoneAppShowsMicrophoneAndRegistrationState`, `PhoneAppCanCallOnlyDirectoryTargets`, `PhoneAppClearsCredentialOnRelease`, and `TestPhoneAssetsAndExactWSSProxyRoute`.
- [ ] **Step 2: Run** `cd admin-ui && npm test -- --run src/phone/PhoneApp.test.tsx`; expect missing page/API behavior failures before implementation.
- [ ] **Step 3: Add and pin SIP.js 0.21.2**, create the `/phone/` Vite entry, and implement readable states for permission denied, connecting, ringing, in call, busy, and failures. Keep SIP credentials only in memory and clear on disconnect/release.
- [ ] **Step 4: Add a same-origin read-only phone config API containing the fixed WSS URL, and add the exact VM Caddy route `/ws/phone-signaling` → `127.0.0.1:8092/ws`.** Preserve strict origin checks and CSP for Go APIs; do not proxy ARI, expose Asterisk’s HTTP API, or add public SIP/RTP ports.
- [ ] **Step 5: Run** `cd admin-ui && npm test -- --run src/phone/PhoneApp.test.tsx && npm run build`, then `go test ./cmd/voice-web`; expect tests/build to pass and both `/admin/` and `/phone/` assets to resolve.
- [ ] **Step 6: Commit** as `feat: add private browser softphone`.

## Task 6: Private End-to-End Acceptance and Release

**Files:**
- Modify: `deploy/update-goweb.sh`, new `deploy/rollback-goweb-update.sh` to build/release assets for the existing Go owner and preserve the previous Go/Asterisk/Caddy state.
- Modify: `docs/OPERATIONS.md`, `docs/VOICE_ADMIN_REQUIREMENTS.md`
- Test: add to existing relevant integration/rollback coverage only where release behavior changes.

**Interfaces:**
- Consumes: Tasks 1–5; existing Go-owner marker, deployment and rollback flow.
- Produces: verified private browser-to-phone and phone-to-browser flows, operational cleanup steps, and updated requirements/backlog.

- [ ] **Step 1: Add/update deployment contract checks** for the Go-owned Asterisk + Go + React build and rollback; verify legacy deployment cannot take ownership from Go production.
- [ ] **Step 2: Run pre-release checks**: `go test ./internal/ari ./internal/webphone ./internal/calls ./internal/admin ./cmd/voice-web`, `cd admin-ui && npm test -- --run && npm run build`, and the relevant deployment rollback test command; capture actual results.
- [ ] **Step 3: Update docs** with private WebRTC ports/routes, lease lifecycle/revocation behavior, no-secret logging rules, call troubleshooting, rollback steps, and mark implemented requirements complete.
- [x] **Step 4: Deploy with `deploy/update-goweb.sh`** after checks passed and the VM reported no calls; Asterisk/Go health checks passed and backup `/opt/voice-go/updates/20260926T180933Z` was created.
- [ ] **Step 5: Run remaining private end-to-end acceptance** for competing browser claim, real calls in both directions, parallel ring and loser cleanup, processed/unprocessed audio, microphone denial, signaling loss, and audio/privacy invariants. The live browser registered and released successfully, but a real call/media check would ring attached physical handsets and was not run.
- [x] **Step 6: Verify automatic rollback on failed releases.** Two candidate updates failed their health/smoke gates and restored the previous healthy Go/Asterisk containers; the third release passed.
- [ ] **Step 7: Commit** documentation and deployment adjustments as `docs: document browser softphone operations`.

## Self-Review

- **Spec coverage:** The six tasks cover session claim/lease/revocation, nickname/directory, browser SIP credentials and UI, both call directions, physical/browser parallel ringing and cleanup, identity/profile snapshots, processed/unprocessed paths, limits, private media/signaling, deployment, rollback, and operations docs. Task 1 also enables ephemeral ARI dynamic PJSIP objects while preserving static endpoint reads.
- **Step scan:** Each task names exact production/test paths, exported contracts, focused test names, and commands; Task 1 is an explicit feasibility gate before dependent implementation.
- **Type consistency:** Task 3’s `ActiveBrowser(extension)` feeds Task 4’s configured target expansion. Task 4’s opaque call ID/state and Task 3’s phone config/directory APIs feed Task 5 UI. Task 2’s constrained ARI methods are consumed by Task 3 session lifecycle.
- **Review Focus:** Atomic lease race, stale Asterisk contact, spoofed identity, concurrent leg answers, and private media/RVC failure each have a named test or acceptance check in the owning task.
- **Scope risk:** The selected WebRTC endpoint pattern, exact caller routing hook, and WSS proxy upgrade must be confirmed against the live Asterisk 22.11 build during Task 1; revise the design before dependent implementation if any assumption fails.
