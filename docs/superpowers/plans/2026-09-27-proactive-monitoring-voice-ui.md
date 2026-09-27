# Proactive Monitoring Visual System for Voice UI — Implementation Plan

> **For agentic workers:** execute natively in the active `codex/browser-softphone` worktree and preserve the existing dirty SIP/deployment changes.

**Goal:** Rebuild the Voice admin and browser-phone presentation using the Proactive Monitoring frontend's shell, navigation, and reusable visual patterns, while retaining Voice functionality and design tokens.

**Architecture:** Adapt the Monitoring shell patterns into small Voice-owned React components and CSS; do not import its product app, API clients, or server. Keep the Go APIs, separate Vite `/admin/` and `/phone/` entries, and existing SIP session lifecycle unchanged.

**Tech Stack:** React, TypeScript, Vite, CSS, Go static/API server, Vitest, Testing Library.

**Spec:** `docs/VOICE_ADMIN_REQUIREMENTS.md`; visual reference: `/home/oleg/Documents/work/proactive-monitoring/proactive-monitoring-frontend/src/{App.tsx,components/Sidebar.tsx,components/Header.tsx,components/ui/index.tsx,index.css}`.

## Global Constraints

- Preserve all existing dirty work in `codex/browser-softphone`.
- Keep Voice's admin and phone same-origin Go API contracts and routes.
- Preserve passwordless admin configuration as currently deployed, with no SIP credentials in UI.
- Keep the `/phone/` SIP session, media-device selection, active-call modal, and call cleanup semantics.
- Never claim physical-device call acceptance from unit tests or a successful deployment alone.

## Review Focus

- Sidebar resize/collapse must preserve the selected view, accessible labels, and narrow-screen usability.
- Refresh/re-render must not re-register SIP clients or interrupt active browser calls.
- Voice-specific status and unknown/stale data must remain semantically honest.
- Generated Vite assets must retain `/admin/assets/` URLs and the separate phone entry.
- Deployment must report the exact update stamp and pass live health/static checks.

---

### Task 1: Adapt Monitoring application shell to Voice admin

**Files:**
- Create: `admin-ui/src/components/VoiceShell.tsx`
- Modify: `admin-ui/src/App.tsx`, `admin-ui/src/styles.css`, `admin-ui/src/App.test.tsx`, `admin-ui/package.json`, `admin-ui/package-lock.json`

**Interfaces:**
- `VoiceShell` accepts the selected Voice page, navigation callback, service status, locale control, and optional sign-out action.
- Existing page components continue receiving the same metrics, routes, phonebook, and mutation callbacks.

- [x] Add tests for accessible nav/current-page state, compact sidebar toggle, and preserved Voice views.
- [x] Verify those tests fail against the current shell.
- [x] Implement the Monitoring-inspired header/sidebar, status badges, Voice design tokens, responsive layout, and iconography.
- [x] Run `cd admin-ui && npm test -- --run && npm run build`.

### Task 2: Apply the shared visual language to `/phone/` and release

**Files:**
- Modify: `admin-ui/src/phone/PhoneApp.tsx`, `admin-ui/src/phone/phone.css`, `admin-ui/src/phone/PhoneApp.test.tsx`, `docs/VOICE_ADMIN_REQUIREMENTS.md`
- Inspect only unless needed: `admin-ui/vite.config.ts`, `Dockerfile.goweb`, `cmd/voice-web/main.go`

**Interfaces:**
- Keep existing softphone API/session interfaces and both Go-served page URLs unchanged.

- [x] Retain the browser-phone call and device-selector regressions while applying shared tokens.
- [x] Apply shared typography, colors, surfaces, responsive navigation, and status patterns without remounting the SIP session on call-state changes.
- [x] Build and test all admin and phone flows; run relevant Go route tests.
- [x] Deploy via `deploy/update-goweb.sh`; verify `/healthz`, `/admin/`, `/phone/`, and the generated asset paths.
