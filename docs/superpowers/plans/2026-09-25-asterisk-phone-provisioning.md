# Asterisk Phone Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the admin assign a configured SIP extension to a verified physical phone and have Asterisk provision its SIP account through a restricted HTTPS path.

**Architecture:** The Go control plane owns a versioned MAC-to-extension mapping and exposes it through the existing React admin. Asterisk `res_phoneprov` plus `res_pjsip_phoneprov_provider` renders model templates using the existing PJSIP endpoint credentials; Caddy publishes only the provisioning path to explicitly allowed phone IPs. A loopback-only authenticated control path reloads the generated PJSIP provisioning associations.

**Tech Stack:** Go standard library, React/TypeScript/Vite already in `admin-ui`, Asterisk 22, PJSIP, `res_phoneprov`, Caddy, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-25-asterisk-phone-provisioning-design.md`

## Global Constraints

- Use Asterisk's `res_phoneprov` and `res_pjsip_phoneprov_provider`; do not introduce another provisioning product.
- Use the existing Go admin/control plane and React SPA.
- Store only normalized MAC, verified model/profile, assigned extension, and allowed source IP in the Go mapping; never copy SIP credentials there.
- Set provisioning URLs manually on handsets; do not modify DHCP or router configuration.
- Expose only `/phoneprov/*` through Caddy and allow only verified handset source IPs; keep ARI loopback-only.
- Support only a verified device model and firmware; unknown models cannot be assigned.
- Preserve passwordless admin mode on its private admin surface; it does not grant provisioning access.
- Do not reboot phones, alter active calls, change SIP passwords, change public DNS/firewall, or modify VM208.
- Keep all existing uncommitted user changes; stage and commit only files belonging to this feature.
- If Asterisk 22 cannot reload provisioning associations through a fixed-command, loopback-only authenticated control path, stop and revise the design. Do not add Docker-socket access or an unauthenticated control port.

## Review Focus

- An unknown MAC, malformed path, or unassigned device must never receive any endpoint's configuration or secret.
- A malformed or duplicate MAC, duplicate endpoint assignment, unsupported extension, unsupported model, or invalid IP must leave the current mapping unchanged.
- A stale revision or a failed atomic write/reload must preserve the last accepted mapping and provisioning response.
- A non-phone source IP must be denied for `/phoneprov/*`; ARI and unrelated Asterisk HTTP paths must remain inaccessible from phone IPs.
- Provisioning template output must contain only the assigned endpoint's SIP username and secret; passwords and response bodies must not enter API or access logs.

## File Map

- `internal/phoneconfig/config.go`, `store.go`: validated, versioned device assignments and atomic persistence.
- `internal/admin/phones_handlers.go`: versioned JSON API for inventory and assignment updates.
- `internal/ami/client.go`: fixed, authenticated AMI actions for PJSIP contact inventory and the single allowed reload command.
- `internal/provisioning/manager.go`: deterministic generation of Asterisk phoneprov PJSIP associations and fixed-command reload interface.
- `cmd/voice-web/main.go`: load the phone store, wire the inventory/assignment API, and provision manager.
- `admin-ui/src/api.ts`, `App.tsx`, `styles.css`: phone inventory, assignment selector, model and registration state, provision status, and copyable URL/instructions.
- `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`, `pjsip.conf.template`: compile/load/health-check provisioning modules and include generated association config.
- `conference/asterisk/phoneprov.conf` and `conference/asterisk/phoneprov/<profile>/`: Asterisk profile routes and verified per-model templates.
- `deploy/compose.conference.yaml`, `deploy/compose.goweb.yaml`, `deploy/Caddyfile.goweb`, `deploy/phone-assignments.example.json`: mount generated non-secret state, provide a least-privilege reload channel, and restrict HTTP routing.
- `deploy/deploy-conference.sh`, `deploy/deploy-goweb.sh`: stage, back up, deploy, verify, and roll back only the files and services owned by this feature.

## Execution Tasks

### Task 1: Verify live device and Asterisk prerequisites

**Files:** No edits.

**Consumes:** Approved design and current deployment scripts.
**Produces:** A recorded, non-secret implementation baseline: exact Asterisk image/version, verified phone model/firmware and stable source IPs, certificate trust, and an approved fixed-command reload path.

- [ ] Read current `deploy/deploy-conference.sh`, `deploy/deploy-goweb.sh`, Compose labels, and Caddy config to confirm current service ownership before any runtime operation.
- [ ] Confirm Asterisk is idle using the existing Go health endpoint and conference health endpoint; record the check output without secrets.
- [ ] Read Asterisk's current PJSIP endpoint/contact inventory and match only the known handset source IPs; confirm source addresses are stable or have reservations.
- [ ] Confirm exact model and firmware from each handset's authenticated device UI or device label. Do not infer a model from an OUI or generic HTTP server banner.
- [ ] Verify each candidate handset accepts HTTPS with the certificate served by `vm-voice-1.lan.awesomeio.ru`; do not change its configuration during this check.
- [ ] Build the pinned Asterisk 22 image in an isolated candidate tag and verify `res_phoneprov` and `res_pjsip_phoneprov_provider` are present and loadable before changing production configuration.
- [ ] Verify `module reload res_pjsip.so` is supported by this Asterisk 22 image and that a dedicated AMI account can invoke only the fixed reload operation through a host-loopback published port. If this cannot be enforced, stop and revise the spec before Task 2.
- [ ] Stop before any live rollout if a device model, trusted HTTPS path, stable source IP, idle runtime, or safe reload condition is missing.

### Task 2: Add strict device-assignment storage and bounded AMI client

**Files:** Create `internal/phoneconfig/config.go`, `internal/phoneconfig/store.go`, `internal/ami/client.go`, `deploy/phone-assignments.example.json`.

**Consumes:** Task 1's verified MAC/model/IP/endpoint inventory.
**Produces:** `phoneconfig.Snapshot{Revision uint64, Devices []phoneconfig.Device}`, `Store.Assign(mac, extension string, expectedRevision uint64) (Snapshot, error)`, and `ami.Client.PJSIPShowContacts(ctx) ([]ami.Contact, error)` plus `ami.Client.ReloadPJSIP(ctx) error`. The AMI client has no arbitrary-action or arbitrary-command method.

`phoneconfig.Device` contains `MAC string`, `Model string`, `ProvisionProfile string`, `SourceIP netip.Addr`, and `Extension string`. The on-disk schema also has `schemaVersion` and `revision`; it contains no passwords. Enforce a verified provisioning-profile allowlist and the configured endpoint allowlist `1983`, `1987`, `1988`, `2014`.

- [ ] Define the JSON schema and normalization rules: MACs are 12 lowercase hex digits without separators; extensions are exact members of the configured endpoint allowlist; IPs must be unicast IPv4 addresses inside `192.168.20.0/24`; models must be in the verified profile registry.
- [ ] Implement strict JSON loading that rejects unknown fields, duplicate JSON keys, duplicate MACs, duplicate assigned extensions, empty inventories, and unsupported schema versions.
- [ ] Implement `Snapshot` and revision-checked `Assign`; assigning an occupied endpoint returns a conflict without changing the file.
- [ ] Persist with a mode-0600 sibling temporary file, file sync, atomic rename, and directory sync; preserve the current file on any pre-rename failure.
- [ ] Add `deploy/phone-assignments.example.json` with the schema and an empty device list; do not include production MACs, IPs, or secrets in the example.
- [ ] Implement the AMI login/session framing needed for the two fixed operations only: `PJSIPShowContacts` and the exact `module reload res_pjsip.so` command. Set bounded dial/read deadlines, reject AMI error responses, and never log frames or credentials.
- [ ] Confirm the client refuses arbitrary command strings by exposing only the two fixed methods in its API.
- [ ] Run `gofmt -w internal/phoneconfig/*.go` and `go build ./internal/phoneconfig` (or `go build ./...` after the package is wired).

### Task 3: Add assignment API and live phone status

**Files:** Create `internal/admin/phones_handlers.go`; modify `internal/admin/handlers.go`, `internal/ari/client.go` or create `internal/pjsip/inventory.go`, and `cmd/voice-web/main.go`.

**Consumes:** Task 2 `phoneconfig.Store` and `ami.Client`; existing `admin.NewHandler` and current admin origin/auth policy.
**Produces:** `GET /admin/api/v1/phones` returning `{revision, devices}` and `PUT /admin/api/v1/phones/{mac}/assignment` accepting `{extension, revision}` and returning the accepted snapshot. Device response rows include `mac`, `model`, `provisionProfile`, `extension`, `sourceIP`, `registration`, `provisionStatus`, `provisionedAt`, and `voiceProfile`; no credential field exists.

- [ ] Add GET handler behavior: use the assignment snapshot, read live PJSIP endpoint/contact state through `ami.Client.PJSIPShowContacts`, and return only known devices with a bounded registration status.
- [ ] Add PUT handler behavior: require the existing exact allowed Origin and current passwordless/auth mode behavior, reject malformed bodies and MACs, use optimistic revision, and return 409 for stale/occupied assignments.
- [ ] Make Asterisk-reload failure a failed API update: do not acknowledge an assignment until both durable mapping and accepted provisioning configuration agree; restore the prior mapping and generated include if Asterisk rejects the reload.
- [ ] Return generic bounded error codes and keep usernames, secrets, provisioning bodies, filesystem paths, and AMI frames out of responses/logs.
- [ ] Wire the store path as `VOICE_PHONE_CONFIG_FILE` defaulting to `/etc/voice-changer/phone-assignments.json`; fail closed when invalid or unreadable.
- [ ] Run `gofmt -w internal/admin/phones_handlers.go internal/admin/handlers.go cmd/voice-web/main.go` and `go build ./...`.

### Task 4: Generate Asterisk associations and serve verified phone templates

**Files:** Create `internal/provisioning/manager.go`, `conference/asterisk/phoneprov.conf`, and one verified directory beneath `conference/asterisk/phoneprov/`; modify `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`, `pjsip.conf.template`, and deployment runtime generation.

**Consumes:** Task 2 validated assignments and Task 1's verified Asterisk reload/control facts.
**Produces:** `Manager.Apply(snapshot phoneconfig.Snapshot) error`, an atomically generated non-secret PJSIP `type=phoneprov` include, and model-specific dynamic templates that use the PJSIP provider's `USERNAME`, `SECRET`, and `CALLERID` variables. It consumes `ami.Client.ReloadPJSIP()`; that method sends exactly the Asterisk CLI command `module reload res_pjsip.so` and exposes no arbitrary command parameter.

- [ ] Select `res_phoneprov` and `res_pjsip_phoneprov_provider` in the Asterisk build; load them in dependency order with `autoload=no`; add both to the Asterisk health check.
- [ ] Add a PJSIP include file for phoneprov associations. For each assigned device, emit only `type=phoneprov`, endpoint ID, normalized MAC, and verified profile; never emit endpoint auth values into the generated file.
- [ ] Configure one profile per verified `ProvisionProfile` and only the required dynamic config filename/template for each verified model. Return no firmware or unrelated static files in this release.
- [ ] Add the manager's fixed `Apply` operation: validate the generated bytes against the snapshot, write atomically with mode 0600, invoke only `module reload res_pjsip.so` over the Task 1 verified authenticated loopback channel, and roll back file/state if reload fails.
- [ ] Configure Asterisk HTTP with the existing loopback-only host publication; do not add a phone-facing publication for port 8092.
- [ ] Compile the candidate Asterisk image and use a synthetic MAC to request its expected dynamic config. Confirm the response contains only that assigned endpoint's auth values and the request is rejected for unknown MACs and paths.

### Task 5: Add the React phone assignment workflow

**Files:** Modify `admin-ui/src/api.ts`, `admin-ui/src/App.tsx`, and `admin-ui/src/styles.css`.

**Consumes:** Task 3 phone API and Task 2 model/extension registry.
**Produces:** A phones table showing MAC, model, assigned SIP extension, registration state, the separate voice-processing profile, and provisioning state; an extension selector and save action; copyable provisioning URL and model-specific manual setup instructions.

- [ ] Add typed `Phone` and `PhoneSnapshot` interfaces matching `GET /admin/api/v1/phones`; add `listPhones` and `assignExtension(mac, extension, revision)` API calls.
- [ ] Add the phone inventory view using the existing admin shell and visual conventions; render `provisionProfile` and `voiceProfile` separately and retain the current voice-profile controls without merging profile editing into SIP assignment.
- [ ] Disable occupied extensions, label unknown models as not provisionable, and show pending/applied state after assignment/reload responses.
- [ ] Show a per-device provisioning URL only for assigned, verified devices. Never include the SIP secret in any DOM, API response, clipboard string, or client log.
- [ ] Handle unavailable API, stale revision, reload failure, registration offline, unassigned device, and unsupported model with explicit messages and retry/reload paths.
- [ ] Run the existing production frontend build command from `admin-ui/package.json`; inspect generated assets and confirm no secrets are bundled.

### Task 6: Restrict HTTPS routing and wire deployment state

**Files:** Modify `deploy/Caddyfile.goweb`, `deploy/compose.goweb.yaml`, `deploy/compose.conference.yaml`, `conference/asterisk/ari.conf.template` only if necessary, and deployment scripts; create no new public listeners.

**Consumes:** Task 1 stable phone source IPs; Task 3 API; Task 4 Asterisk phoneprov route; Task 5 UI route format.
**Produces:** HTTPS `/phoneprov/*` proxy with exact handset source-IP allowlist, explicit deny for all other requests under that prefix, and loopback-only authenticated Asterisk reload connectivity.

- [ ] Add a Caddy matcher combining exact `/phoneprov/*` path and the verified phone IP list; proxy only matching requests to `127.0.0.1:8092`; add an explicit 403 handler for all other `/phoneprov/*` requests.
- [ ] Confirm the current Caddy site certificate is used for HTTPS and that `/admin/api/*`, `/ari/*`, and all other paths cannot reach Asterisk through the new matcher.
- [ ] Add only the minimum Asterisk manager binding and dedicated credential required by Task 1; bind host publication to `127.0.0.1`, limit manager ACL to the verified Go host source, mount the credential read-only, and grant only the fixed reload action at the Go application layer.
- [ ] Mount the non-secret assignment store read-write only into Go and the generated Asterisk association include read-only into Asterisk; keep existing SIP/ARI credentials separate.
- [ ] Validate both Compose definitions with `docker compose config` using the matching VM209 env files; validate Caddy with `caddy validate` against the staged config.

### Task 7: Stage, deploy, and verify with rollback ready

**Files:** Modify the relevant deployment scripts only when required to stage the new files and preserve a scoped rollback; do not touch unrelated deployment paths.

**Consumes:** Tasks 1-6.
**Produces:** A deployed admin provisioning flow with a verified restricted route and rollback artifacts; physical phone settings remain unchanged until the user manually configures one handset.

- [ ] Take a fresh read-only VM209 runtime baseline; confirm Go and Asterisk deployment ownership, Caddy configuration, disk/RAM headroom, active-call count, phone IPs, image tags, and current health before writing remote files.
- [ ] Stage the exact build contexts and non-secret mapping example under a unique release directory; back up the current Asterisk image/config, Go config, Caddyfile, and runtime mapping before applying changes.
- [ ] Build the new Asterisk image under a unique candidate tag; validate modules and synthetic template responses before restarting the production Asterisk container.
- [ ] Deploy only when the call controller is idle; restart Asterisk and Go in the order required by Compose health dependencies; reload Caddy only after `caddy validate` succeeds.
- [ ] From each allowed phone IP, fetch only that phone's config and verify registration; from a non-phone LAN source, confirm `/phoneprov/*` returns 403; confirm ARI at host port 8092 remains unreachable from the handset network.
- [ ] Configure the HTTPS URL manually on one verified Yealink handset only after preserving its current config. Reprovision a selected extension, then verify registration and a call; stop before enabling further devices if firmware or account behavior differs.
- [ ] Verify the admin reports assignment, live registration, and provisioning status; verify changing voice profile still affects call routing as before.
- [ ] If any health, ACL, registration, or call check fails, restore only this release's backed-up files/image/Caddy config and verify the prior Go/Asterisk/Caddy health. Do not retry on an active call.

## Self-review

- Coverage: storage/API cover MAC-to-extension state and revision conflicts; Asterisk tasks cover modules, endpoint credential rendering, and reload; UI covers assignment/status/URL; Caddy and deployment tasks cover isolation, rollout, and rollback.
- Placeholder scan: no task uses TODO/TBD or asks an implementer to invent validation behavior. The only unresolved environmental fact is intentionally a Task 1 stop condition: model/firmware, source IP stability, and a safe Asterisk 22 reload path.
- Interface consistency: `phoneconfig.Snapshot` and `phoneconfig.Device` are the single data contract; `Store.Assign` returns the updated snapshot; `Manager.Apply(snapshot)` consumes that same snapshot; API and UI use `revision` consistently.
- Review focus: all five input/security conditions above have an explicit owner and an observable CLI or phone-level check before deployment.
