# Phone Discovery and Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Let the administrator see enrolled phones separately from newly discovered/unassigned devices, explicitly assign a configured SIP extension, and provision only verified phones through Asterisk.

**Architecture:** Go owns a versioned, secret-free MAC-to-extension mapping and merges Asterisk PJSIP contacts with a read-only DHCP candidate source when available. The React UI has separate Existing phones and New / unassigned views. Asterisk's phone provisioning modules render per-model configuration for an already configured endpoint; Caddy exposes only the credential-bearing phoneprov path to explicitly approved handset IPs.

**Tech Stack:** Go standard library, existing React/TypeScript/Vite app, Asterisk 22/PJSIP, Asterisk `res_phoneprov` and `res_pjsip_phoneprov_provider`, Caddy, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-25-asterisk-phone-provisioning-design.md` and `docs/superpowers/specs/2026-09-25-phone-discovery-load-capacity-design.md`; canonical scope: `docs/VOICE_ADMIN_REQUIREMENTS.md`.

## Global Constraints

- Never infer MAC, phone ownership, or model from an IP address alone.
- Keep voice profile keyed by SIP extension and separate from MAC-to-extension assignment.
- Do not modify DHCP, perform active subnet scans, reboot phones, or change extension credentials.
- Never return SIP secrets from the admin API or write them into the Go assignment file.
- Only an assigned MAC with a verified model/profile may receive its corresponding phone configuration.
- Keep ARI and the existing admin private; allow the provisioning path only from individually verified phone source addresses.
- Do not grant Docker socket access or broad Asterisk CLI privilege to the Go container.
- Preserve all existing uncommitted user changes, especially `admin-ui/src/App.tsx`, `admin-ui/src/api.ts`, `cmd/voice-web/main.go`, `internal/admin/handlers.go`, and `deploy/compose.goweb.yaml`.
- Do not add or run automated tests unless the user separately asks for testing. Use the listed formatting, build, configuration, and read-only runtime checks.

## Review Focus

- A DHCP lease and a PJSIP contact have different identity fields; only join them when an authoritative MAC exists.
- A stale lease or reused IP must never move or reassign an enrolled phone.
- An enrolled but offline phone remains in Existing phones; registration is not the same as enrollment.
- A new phone with an unknown model or untrusted HTTPS certificate must not receive endpoint credentials.
- Failed persistence, stale revision, or Asterisk reload must leave the last accepted mapping and served configuration intact.

## File Map

- `internal/phoneconfig/config.go`, `store.go`: strict device/assignment schema, normalization, revision and atomic persistence.
- `internal/phoneinventory/types.go`, `service.go`, `pjsip.go`, `dhcp.go`: source-aware inventory, PJSIP contact adapter, optional read-only DHCP client adapter, and deterministic merge without IP-based identity guesses.
- `internal/ami/client.go`: narrowly scoped authenticated contact/reload operations only if Task 1 proves the AMI privilege boundary safe. Do not expose an arbitrary command method.
- `internal/admin/phones_handlers.go`: inventory and revision-checked assignment APIs; wire into `internal/admin/handlers.go` and `cmd/voice-web/main.go`.
- `internal/provisioning/manager.go`: generate only non-secret PJSIP phoneprov associations from validated assignments and apply them through the verified fixed control interface.
- `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`, `pjsip.conf.template`, `phoneprov.conf`, and verified model template directories: build and serve supported phone configurations.
- `admin-ui/src/api.ts`, `App.tsx`, `styles.css`: separate Existing phones / New-unassigned views, assignment, status, and model-specific manual provisioning instructions.
- `deploy/compose.conference.yaml`, `compose.goweb.yaml`, `Caddyfile.goweb`, deployment scripts and an example assignment file: provide least-privilege runtime wiring and scoped rollback.

## Execution Tasks

### Task 1: Verify live prerequisites without changing services

**Files:** No edits.

**Produces:** A non-secret prerequisite record for VM209 service ownership, exact Asterisk image/version and endpoint inventory, safe fixed-command reload channel, phone models/firmware/source IPs/certificate trust, router lease-read availability, and deployment rollback paths.

- [ ] Read current deployment scripts, Compose labels/config files, Caddy routes, and Asterisk config ownership before choosing a target service to rebuild.
- [ ] Confirm whether the AX12 supports a documented, read-only client/lease query reachable from VM209. Record the supported query and the least-privilege auth mode without saving credentials in Git.
- [ ] Confirm each candidate phone's exact model/firmware and current IP through its device UI or label; confirm the existing HTTPS certificate is trusted by that firmware.
- [ ] Confirm phone IP stability/reservation before considering it for a credential-bearing source allowlist.
- [ ] Verify Asterisk 22's provisioning modules, HTTP path, and a constrained authenticated reload interface. Reject an AMI `Command` design if the configured account would have general CLI authority or the credential would be exposed to the admin API.
- [ ] If a read-only DHCP interface, safe reload path, supported model, certificate trust, or stable permitted source address is missing, record that path as blocked and stop before opening a provisioning route. Do not substitute an active scan or wildcard allowlist.

### Task 2: Add the secret-free phone assignment store

**Files:** Create `internal/phoneconfig/config.go`, `internal/phoneconfig/store.go`, and `deploy/phone-assignments.example.json`.

**Consumes:** Task 1's confirmed endpoint allowlist and the verified model/profile registry.

**Produces:** `phoneconfig.Snapshot{Revision uint64, Devices []phoneconfig.Device}` and revision-checked `Store.Assign(mac, extension string, expectedRevision uint64) (Snapshot, error)`. Device values include normalized MAC, verified model/profile, assigned endpoint, allowed source IP, and assignment state; they contain no credentials.

- [ ] Define schema version, revision, device fields, and exact normalized MAC/IP/endpoint rules. An endpoint must be a member of the verified configured endpoint inventory; an active extension may belong to only one MAC.
- [ ] Implement strict JSON parsing: reject unknown fields, duplicate MACs/endpoints, invalid models/IPs, unsupported schema versions, and malformed revisions.
- [ ] Implement revision-checked assignment and explicit unassignment/update operations; stale revision or duplicate assignment returns a bounded conflict without mutating state.
- [ ] Persist atomically with mode `0600`, sibling temporary file, file sync, rename, and directory sync. On any pre-rename failure, retain the previous snapshot.
- [ ] Keep the example file empty of real device data and secrets. Format Go files with `gofmt` and confirm `go build ./internal/phoneconfig` succeeds.

### Task 3: Build provenance-aware phone discovery

**Files:** Create `internal/phoneinventory/types.go`, `service.go`, `pjsip.go`, and `dhcp.go`; create `internal/ami/client.go` only if Task 1 verified its authorization boundary.

**Consumes:** Task 1 inventory/source evidence and Task 2's assignment snapshot.

**Produces:** `phoneinventory.Snapshot{Revision uint64, Sources []SourceStatus, Existing []Device, Unassigned []Candidate}`. Each record includes source and observation time. PJSIP contacts provide endpoint/IP/status; DHCP leases provide MAC/IP/lease time. Merge only by MAC.

- [ ] Define bounded `Device`, `Candidate`, `SourceStatus`, and `Snapshot` types. Use explicit values for registered, unregistered/unknown, discovered, assigned, and source unavailable.
- [ ] Implement one fixed read-only AMI operation for `PJSIPShowContacts`; set dial/read deadlines and response size limits. Do not add arbitrary AMI actions or CLI execution.
- [ ] Implement the DHCP adapter only for the exact authenticated read API proven in Task 1. Parse bounded response fields and reject malformed MAC/IP/lease values. If the API is absent, return a typed unavailable status rather than probing the subnet.
- [ ] Merge persisted assignments with current PJSIP contacts and DHCP leases by MAC only. If PJSIP has endpoint/IP but no MAC join, show it as source-only registration evidence; never pair it with a lease by IP alone.
- [ ] Keep all persisted assignments in Existing even when a source goes offline. Put newly discovered and otherwise known-but-unassigned devices into New / unassigned. Do not mutate assignment state during discovery.
- [ ] Run `gofmt` and `go build ./internal/phoneinventory` after wiring package dependencies.

### Task 4: Expose bounded inventory and assignment APIs

**Files:** Create `internal/admin/phones_handlers.go`; modify `internal/admin/handlers.go` and `cmd/voice-web/main.go`.

**Consumes:** `phoneconfig.Store`, `phoneinventory.Service`, existing origin/session protections, and verified endpoint/model registries.

**Produces:** Same-origin `GET /admin/api/v1/phones` returning revision, source freshness/status, existing devices, and unassigned candidates; `PUT /admin/api/v1/phones/{mac}/assignment` accepting `{extension, revision}`; a bounded success snapshot or generic validation/conflict/service error. No response contains a SIP password.

- [ ] Add handler dependencies through constructor options without breaking the existing voice-profile routes or auth-disabled private deployment mode.
- [ ] Implement an inventory GET with request timeout, no-store cache policy, stable JSON schema, and explicit unknown/stale source status.
- [ ] Implement the revision-checked assignment PUT; validate exact MAC, configured free extension, verified provisioning model, and permitted IP before storing.
- [ ] If the verified Asterisk reload/apply step fails, return failure and restore the prior accepted file/snapshot; do not report success while Asterisk serves stale or mismatched state.
- [ ] Return only bounded error codes. Do not log response bodies, device credentials, DHCP tokens, AMI frames, or provisioning paths containing device identifiers.
- [ ] Run `gofmt` and `go build ./cmd/voice-web`.

### Task 5: Enable Asterisk phone provisioning for verified models

**Files:** Modify `conference/asterisk/Dockerfile`, `modules.conf`, `healthcheck.sh`, `pjsip.conf.template`; create `phoneprov.conf`, verified model templates and `internal/provisioning/manager.go`; modify conference runtime generation only as required.

**Consumes:** Task 1's exact model/certificate/reload results and Task 2's assignments.

**Produces:** Asterisk phoneprov associations mapping assigned MACs to existing endpoints and verified profiles; a manager that atomically applies these associations through the approved restricted control path.

- [ ] Build/load `res_phoneprov` and `res_pjsip_phoneprov_provider` in dependency order with the repository's explicit module-selection rules.
- [ ] Add templates only for models and firmware confirmed in Task 1. Do not serve firmware or generic filesystem paths.
- [ ] Generate association objects from MAC, endpoint ID, and allowlisted profile only. Let Asterisk resolve `USERNAME`, `SECRET`, and caller ID from its existing endpoint config; never copy secrets to Go state.
- [ ] Implement manager `Apply(snapshot) error`: validate snapshot, atomically write a mode-`0600` generated include, run only the fixed operation through the verified restricted interface, and restore the prior include if Asterisk does not accept it.
- [ ] Determine a trustworthy successful-fetch signal. If Asterisk/Caddy cannot report fetch success without logging secrets or sensitive path data, show “last fetch unknown” rather than falsely marking a phone provisioned.
- [ ] Verify a synthetic assigned MAC receives only its endpoint's expected SIP username/secret, while unknown MACs, unassigned devices, unsupported paths, and unapproved source IPs receive no config. Keep ARI unreachable from handset sources.

### Task 6: Build the two phone workflows in React

**Files:** Modify `admin-ui/src/api.ts`, `App.tsx`, and `styles.css`.

**Consumes:** Task 4's inventory/assignment API and Task 2's verified endpoint/model registry.

**Produces:** Separate navigation entries **Existing phones** and **New / unassigned phones**. Existing shows all enrolled devices, including offline ones; New / unassigned shows discovered candidates and permits explicit selection of an available extension.

- [ ] Add typed API responses for sources, timestamps, candidate/existing device states, assignments, and bounded errors.
- [ ] Add the two distinct navigation and content views. Do not classify an enrolled phone as new merely because registration is down; label registration unknown when Asterisk cannot be polled.
- [ ] Add explicit assignment confirmation and free-extension selector; after success, move the device to Existing and show the selected extension's current voice profile separately.
- [ ] Show DHCP/Asterisk source freshness, model verification state, provisioning-needed/fetch status, and concise source-unavailable states. Do not infer model from the IP/MAC prefix as confirmed fact.
- [ ] Show the per-device provisioning URL and verified model instructions without rendering SIP credentials. Keep passwordless admin behavior exactly as configured for the private deployment.
- [ ] Run `npm --prefix admin-ui run build`.

### Task 7: Restrict routes and deploy with rollback

**Files:** Modify `deploy/Caddyfile.goweb`, `deploy/compose.goweb.yaml`, `deploy/compose.conference.yaml`, relevant scoped deployment scripts, and safe non-secret runtime examples.

**Consumes:** Tasks 1-6 and the existing deployment ownership map.

**Produces:** Private admin inventory APIs and exact `/phoneprov/*` routing, with the credential-bearing path denied to all but individually verified phone sources; a scoped rollback snapshot.

- [ ] Mount only non-secret assignment/config state required by Go and Asterisk. Do not add Docker socket, broad host filesystem access, or public listeners.
- [ ] Configure the exact HTTPS provisioning route and deny all other Asterisk HTTP paths. Keep ARI loopback-only.
- [ ] Build and inspect candidate images/config before changing live services. Use the matching Compose env file and deployment owner documented by current labels/scripts.
- [ ] Capture scoped rollback copies and verify no active calls before reload/restart. Abort if the service owner, call-idle check, backup, or reload guard fails.
- [ ] After deployment, verify existing phones remain listed, a DHCP candidate appears when the router supports lease reads, API assignment preserves revision rules, a non-phone source is denied provisioning, and one manually configured verified phone fetches only its assigned account and registers.
- [ ] Confirm rollback restores prior service/config if provisioning or registration fails. Do not change VM208, Frigate, public DNS/firewall, or DHCP settings.

## Implementation boundary

The earlier `2026-09-25-asterisk-phone-provisioning.md` is a narrower unapproved predecessor. This plan adds DHCP-based candidates and the separate Existing / New views. Keep that older untracked file unchanged until the user approves a replacement/cleanup.
