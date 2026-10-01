# Public Phone Cookie Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the browser-native Basic Auth prompt with a branded login dialog and persistent owner cookie session.

**Architecture:** The Go service protects requests for the exact public phone host, serves a same-origin login screen, validates the owner credential, and issues a signed 90-day rolling cookie. Caddy proxies the phone-only public allowlist directly to Go; private phone origins and admin behavior remain unchanged.

**Tech Stack:** Go 1.22, React/TypeScript, Caddy, Python deployment utilities, Go standard crypto primitives.

**Spec:** [2026-09-30-public-phone-cookie-login-design.md](../specs/2026-09-30-public-phone-cookie-login-design.md)

## Global Constraints

- Protect exact `phone.awesomeio.ru` host only; retain current private-origin behavior.
- Use a `Secure`, `HttpOnly`, `SameSite=Strict`, host-only `__Host-` cookie with 90-day rolling expiry.
- Require exact public Origin for login/logout; fail closed when auth config is absent.
- Keep public Caddy path allowlist limited to phone UI, its protected `/admin/assets/*` bundle files, and signaling; strip Authorization and keep the admin page/service routes 404.
- Never persist or log the plaintext password; mount auth configuration read-only and restrict host permissions.

## Review Focus

- A private or spoofed host must not activate or bypass public-host auth → test exact host matching.
- Missing, expired, malformed, or forged cookies must fail closed → test middleware rejection.
- Login/logout CSRF and password guesses must not change state → test exact Origin and invalid credentials.
- A persistent cookie must work across normal browser restarts and renew while active → test MaxAge/expiry renewal.
- WSS and media config must require the same session → test auth guard around `/ws/phone-signaling` and phone APIs.

---

### Task 1: Go cookie-auth handler

**Files:** create `internal/publicauth/auth.go`, `internal/publicauth/auth_test.go`; modify `cmd/voice-web/main.go` and `cmd/voice-web/main_test.go`.

- [x] Add failing tests for exact public host protection, login success/failure, Origin rejection, cookie flags/90-day MaxAge, sliding renewal, logout, API 401, and WebSocket 401.
- [x] Run `go test ./internal/publicauth ./cmd/voice-web` and confirm expected failures.
- [x] Implement a PBKDF2-HMAC-SHA256 owner verifier and HMAC-signed stateless session cookies; protect only the configured public host; serve login asset routes only before authentication.
- [x] Wire startup to fail closed when `VOICE_PHONE_PUBLIC_ORIGIN` is set without a valid auth file.
- [x] Rerun focused Go tests, then `go test ./...` and `go vet ./...`.

### Task 2: Login UI and logout

**Files:** create embedded login HTML/CSS/JS under `internal/publicauth/assets/`; modify `admin-ui/src/phone/PhoneApp.tsx`, its tests, and phone styles as required.

- [x] Implement the responsive branded login dialog and generic error state; successful POST redirects to `/phone/`.
- [x] Add a public-host-only “Выйти” control. Private phone UI remains unchanged.
- [x] Run the focused React tests and production UI build (19 React tests passed).

### Task 3: Secret delivery and edge migration

**Files:** modify `deploy/compose.goweb.yaml`, both Go deployment scripts, `deploy/Caddyfile.goweb`, `tests/test_public_phone_access.py`, and operations/status docs; add `deploy/configure-public-phone-auth.py`.

- [x] Add a deployment test for a read-only auth-file mount, direct Caddy phone allowlist, no Basic Auth, and no legacy 8181 listener.
- [x] Add a secret provisioning utility that reads the password from stdin, writes only a verifier plus random session key, and enforces owner/mode `10001:10001:0400`.
- [x] Update deployment env generation to mount the secret read-only and keep it through rollbacks.
- [x] Run Python deployment tests, Go/UI suites, Compose config validation, and candidate Caddy validation.
- [x] Back up VPS Caddy/auth snippet and VM release/config; deploy Go with rollback safeguard; validate public/private smoke checks; retire the unused Basic Auth snippet only after cookie login passes.

### Task 4: Live owner acceptance

- [x] Verify the public login page, bad password/Origin rejection, 90-day cookie persistence across page/API requests, phone API/WSS access, and logout cookie clearing. The open Chrome tab now shows the branded login form; owner sign-in and browser restart were not performed.
- [x] Verify public `/admin/` and `/healthz` return 404 and anonymous API access is rejected; other SIP/ARI/RVC ports are not proxied by the public Caddy route.
- [x] Verify private VPN phone/admin smoke checks pass. Public two-browser call/audio acceptance remains incomplete and is recorded separately in `BROWSER_CALL_ACCEPTANCE.md`.
