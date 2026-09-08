# FCPE Canary Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stopped, temporary `voice-claude` RND process with a reproducible, hardened FCPE-canary service that is independent of the production GPT v2 caller and survives VM209 restarts.

**Architecture:** Keep GPT v2 on the existing `voice-rvc.service:8090` as the only production voice path. Run one separate FCPE-only `rvc_service.rt_server` process on loopback `:8093`; Caddy routes the existing private `voice-claude.lan.awesomeio.ru` hostname to it. The canary owns no GPT copy, no `/tmp` runtime source and no Asterisk route; it may fail or stop without touching calls.

**Tech Stack:** Python 3.12, FastAPI/Uvicorn, PyTorch/RVC, systemd, Caddy, Bash, pytest, Node.js built-in test runner, Docker-free scoped deployment.

**Spec:** `docs/superpowers/specs/2026-09-08-target-phone-guy-architecture-design.md`

## Global Constraints

- Production target remains GPT v2 at `127.0.0.1:8090/ws/rvc-v2`.
- Canary is FCPE-only, `block_s=0.3`, `extra_s=1.5`, loopback `127.0.0.1:8093`.
- No `/tmp/rt_demo`, `/tmp/rt_demo_gpt`, `8098`, or a second GPT model process may be a runtime dependency.
- Canary must not restart `voice-rvc.service`, conference containers, Asterisk, DNS, firewall, Proxmox, VM208, or Frigate.
- No cloud inference, no persisted user PCM/WAV/text, no secrets in Git or logs.
- A failed canary deploy restores its exact prior canary release, service state and Caddy fragment; it must leave production GPT v2 healthy.
- Tests are written first and observed failing before each production behavior is added.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `rvc_service/rt_server.py` | FCPE-only FastAPI server; live WebSocket, FCPE comparison and optional TTS; no proxy to a duplicated GPT process. |
| `web-rt/index.html` | Private canary landing page with FCPE and an explicit link to production GPT v2 on `voice.lan`. |
| `web-rt/gpt-live/` | Removed from the canary runtime because it depended on `8098`; production GPT UI stays under `web/`. |
| `deploy/voice-rtrvc-canary.service` | Hardened, restartable loopback systemd unit for `:8093`. |
| `deploy/caddy.d/voice-claude.caddy` | One declarative Caddy site fragment owned by the canary. |
| `deploy/Caddyfile` | Base Caddy template importing `conf.d/*.caddy`; retains existing production routes untouched. |
| `deploy/deploy-rtrvc-canary.sh` | Scoped release, canary health/audio gates, exact backup and rollback. |
| `deploy/requirements-rtrvc.lock` | Fully pinned packages needed only by FCPE canary, installed into `/opt/voice-rtrvc/venv`. |
| `tests/test_rt_server.py` | CPU-only FastAPI contract tests for the FCPE-only surface. |
| `tests/test_deploy_rtrvc_canary.py` | Controlled-root test of install, health gate and rollback behavior. |
| `tests/live-rtrvc.py` | Private live FCPE audio probe: protocol, non-silent PCM, bounded queue, restart count. |
| `docs/RT_DEMO_SONNET_2026-09-07.md` | Replaced by current managed-canary operations document. |
| `docs/LIVE_STATUS.md`, `docs/OPERATIONS.md`, `README.md` | Current URLs, ownership and rollback instructions. |

## Task 1: Make the RND server FCPE-only and remove the duplicate GPT dependency

**Files:**
- Modify: `rvc_service/rt_server.py`
- Modify: `web-rt/index.html`
- Delete: `web-rt/gpt-live/index.html`
- Delete: `web-rt/gpt-live/rtrvc-gpt-live.js`
- Create: `tests/test_rt_server.py`

**Interfaces:**
- Consumes: `create_app(engine_factory)` and `VARIANTS` from `rvc_service.rt_server`.
- Produces: `GET /healthz` with exactly one variant `v2-fcpe`; `WS /ws/rvc` and `WS /ws/rvc/v2-fcpe`; no `WS /ws/rvc-gpt`.

- [ ] **Step 1: Write failing server-surface tests**

```python
def test_health_advertises_only_the_fcpe_canary() -> None:
    app = create_app(FakeRtEngine)
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["variants"] == {
        "v2-fcpe": {
            "label": "FCPE canary, block 0.3 s",
            "blockS": 0.3,
            "extraS": 1.5,
            "f0method": "fcpe",
        }
    }


def test_removed_gpt_relay_is_not_registered() -> None:
    app = create_app(FakeRtEngine)
    with TestClient(app) as client:
        response = client.get("/ws/rvc-gpt")
    assert response.status_code == 404
```

`FakeRtEngine` implements `convert_block`, `convert_block_48k` and
`reset_pitch_cache`; it returns finite zero-filled NumPy audio at the requested
output length. It never imports Torch or RVC.

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `.venv/bin/pytest -q tests/test_rt_server.py`

Expected: the health assertion fails because the current module exposes a
different FCPE label and `WS /ws/rvc-gpt` is registered.

- [ ] **Step 3: Remove only the obsolete GPT relay**

In `rvc_service/rt_server.py`:

1. Delete `GPT_ROOT`, `GPT_PYTHON`, `GPT_TIMEOUT_S`, `GPT_LIVE_WS_URL` and
   `GPT_LIVE_ORIGIN`.
2. Set the FCPE label exactly to `"FCPE canary, block 0.3 s"`.
3. Delete only the `@app.websocket("/ws/rvc-gpt")` handler and its relay code.
4. Keep `/ws/rvc` as the FCPE default and `/ws/rvc/v2-fcpe` as the named route.
5. Keep user-initiated `compare` only if it can run FCPE in-process. Remove
   entries that invoke external GPT/GLM subprocesses or directories.
6. Replace the landing-page GPT live link with
   `https://voice.lan.awesomeio.ru/low-latency/`, labelled as production GPT v2.

- [ ] **Step 4: Run focused tests and browser static tests**

Run:

```bash
.venv/bin/pytest -q tests/test_rt_server.py tests/test_rt_chunks.py
node --test tests/*.test.cjs
```

Expected: all pass; the landing page has no link to `/gpt-live/` and no code
uses ports `8098` or `/tmp/rt_demo_gpt`.

- [ ] **Step 5: Commit the self-contained behavior change**

```bash
git add rvc_service/rt_server.py web-rt tests/test_rt_server.py
git commit -m "refactor: make FCPE RVC canary independent"
```

## Task 2: Add a reproducible FCPE runtime and hardened service unit

**Files:**
- Create: `deploy/requirements-rtrvc.lock`
- Create: `deploy/voice-rtrvc-canary.service`
- Create: `tests/test_rtrvc_service_config.py`

**Interfaces:**
- Consumes: existing `/opt/voice-changer/experiments/phoneguy` model assets and
  upstream checkout read-only.
- Produces: a venv at `/opt/voice-rtrvc/venv` and a unit whose only listener is
  `127.0.0.1:8093`.

- [ ] **Step 1: Write a unit-file parser test**

```python
def test_canary_unit_is_loopback_only_and_isolated_from_production() -> None:
    unit = parse_systemd_unit(ROOT / "deploy/voice-rtrvc-canary.service")
    assert "--host 127.0.0.1 --port 8093" in unit["Service"]["ExecStart"]
    assert "/opt/voice-rtrvc/current" in unit["Service"]["WorkingDirectory"]
    assert unit["Service"]["MemorySwapMax"] == "0"
    assert "voice-rvc.service" not in unit["Unit"].get("After", "")
```

The helper normalizes multi-space values but does not assert raw source text.

- [ ] **Step 2: Run it and verify the expected failure**

Run: `.venv/bin/pytest -q tests/test_rtrvc_service_config.py`

Expected: FAIL because the unit does not exist.

- [ ] **Step 3: Add the FCPE lock and unit**

1. On VM209, use only read-only package metadata to export exact installed
   FCPE dependency versions from the known working RND environment.
2. Write each requirement as `name==version` to
   `deploy/requirements-rtrvc.lock`; do not include Torch or CUDA packages
   already pinned by `rvc_service/requirements.lock`.
3. Create a dedicated `/opt/voice-rtrvc/venv` from the existing known-good
   Python environment. Verify both locks with `importlib.metadata` and
   `pip check`; never mutate `/opt/voice-rvc/venv`.
4. Implement the unit with `User=voice-rvc`, `Group=voice-rvc`,
   `SupplementaryGroups=video render`, `WorkingDirectory=/opt/voice-rtrvc/current`,
   `ExecStart=/opt/voice-rtrvc/venv/bin/python -m uvicorn rvc_service.rt_server:app --host 127.0.0.1 --port 8093 --workers 1 --ws-max-size 4096 --ws-max-queue 4 --no-access-log`,
   `Restart=on-failure`, `RestartSec=5`, `MemoryMax=2200M`, `MemorySwapMax=0`,
   `CPUQuota=250%`, and the same filesystem/network hardening pattern as
   `voice-rvc.service`.
5. Set `RVC_ASSETS_ROOT`, `HF_*_OFFLINE=1`, `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`,
   `RVC_CUDA_GRAPH=1`, thread limits and a canary-specific writable
   `NUMBA_CACHE_DIR` under `/var/cache/voice-rtrvc`.

- [ ] **Step 4: Run configuration and import verification**

Run:

```bash
.venv/bin/pytest -q tests/test_rtrvc_service_config.py
systemd-analyze verify deploy/voice-rtrvc-canary.service
```

Expected: unit test passes and systemd emits no unit syntax error.

- [ ] **Step 5: Commit the reproducible runtime contract**

```bash
git add deploy/requirements-rtrvc.lock deploy/voice-rtrvc-canary.service tests/test_rtrvc_service_config.py
git commit -m "feat: add hardened FCPE canary service"
```

## Task 3: Isolate Caddy ownership with a canary site fragment

**Files:**
- Modify: `deploy/Caddyfile`
- Create: `deploy/caddy.d/voice-claude.caddy`
- Create: `tests/test_caddy_fragments.py`

**Interfaces:**
- Consumes: Caddy base template and certificate paths already used by production.
- Produces: Caddy imports `/etc/caddy/conf.d/*.caddy`; the canary fragment owns
  the single `voice-claude.lan.awesomeio.ru → 127.0.0.1:8093` mapping.

- [ ] **Step 1: Write failing Caddy adaptation test**

```python
def test_canary_fragment_adapts_with_the_production_base(tmp_path: Path) -> None:
    config = materialize_caddy_tree(ROOT / "deploy", tmp_path)
    result = subprocess.run(
        ["caddy", "adapt", "--config", str(config), "--adapter", "caddyfile"],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:8093" in result.stdout
```

`materialize_caddy_tree` copies the base and fragments into an isolated temp
tree and rewrites only the absolute import directory for that tree.

- [ ] **Step 2: Run it and verify the expected failure**

Run: `.venv/bin/pytest -q tests/test_caddy_fragments.py`

Expected: FAIL because `deploy/caddy.d/voice-claude.caddy` and the import are
absent.

- [ ] **Step 3: Move only the canary site block to a fragment**

1. Add `import /etc/caddy/conf.d/*.caddy` to `deploy/Caddyfile`.
2. Remove the direct `voice-claude` block from that file.
3. Create `deploy/caddy.d/voice-claude.caddy` with the existing private bind,
   certificate paths and `reverse_proxy 127.0.0.1:8093`.
4. Do not alter `vm-voice-1`, `/ws/rvc`, `/ws/rvc-v2`, `/ws/conference`, TLS
   paths or Caddy global options.

- [ ] **Step 4: Validate the actual assembled Caddy config**

Run:

```bash
.venv/bin/pytest -q tests/test_caddy_fragments.py
tmpdir=$(mktemp -d)
cp deploy/Caddyfile "$tmpdir/Caddyfile"
mkdir "$tmpdir/conf.d"
cp deploy/caddy.d/voice-claude.caddy "$tmpdir/conf.d/"
sed -i "s#/etc/caddy/conf.d/\*.caddy#$tmpdir/conf.d/*.caddy#" "$tmpdir/Caddyfile"
caddy validate --config "$tmpdir/Caddyfile"
```

Expected: the production and canary routes both adapt successfully.

- [ ] **Step 5: Commit the ownership boundary**

```bash
git add deploy/Caddyfile deploy/caddy.d/voice-claude.caddy tests/test_caddy_fragments.py
git commit -m "feat: isolate voice claude Caddy configuration"
```

## Task 4: Build a scoped deploy and rollback path for the canary

**Files:**
- Create: `deploy/deploy-rtrvc-canary.sh`
- Create: `tests/test_deploy_rtrvc_canary.py`
- Create: `tests/live-rtrvc.py`

**Interfaces:**
- Consumes: source tree, locked canary venv, Caddy fragment and unit from Tasks 1–3.
- Produces: `/opt/voice-rtrvc/releases/<UTC stamp>`, `current` symlink,
  `/opt/voice-rtrvc/backups/<UTC stamp>`, enabled service and private live probe.

- [ ] **Step 1: Write failing controlled-root deployment tests**

```python
def test_failed_canary_health_gate_restores_prior_release_and_never_calls_voice_rvc(tmp_path):
    root, env = make_canary_fixture(tmp_path, health="fail")
    result = subprocess.run([SCRIPT], cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert os.readlink(root / "opt/voice-rtrvc/current") == "/opt/voice-rtrvc/releases/old"
    assert read_calls(env)["systemctl"].count("restart voice-rvc.service") == 0
    assert (root / "etc/caddy/conf.d/voice-claude.caddy").read_text() == "old fragment"


def test_live_probe_rejects_wrong_fcpe_contract_before_audio(tmp_path):
    result = run_probe(fake_websocket(ready={"blockSeconds": 0.15}))
    assert result.code == "protocol"
    assert result.audio_blocks == 0
```

The fixture implements only `rsync`, `systemctl`, `caddy`, `curl`, `ssh`,
`journalctl` and the canary probe. It records commands; no production service
or real SSH is used in unit tests.

- [ ] **Step 2: Run tests and verify the expected failure**

Run: `.venv/bin/pytest -q tests/test_deploy_rtrvc_canary.py`

Expected: FAIL because neither the deploy script nor the live probe exists.

- [ ] **Step 3: Implement the scoped script and probe**

`deploy-rtrvc-canary.sh` must:

1. Refuse any target other than `ubuntu@192.168.20.70`.
2. Run all Python and Node tests before SSH.
3. Create `stamp=$(date -u +%Y%m%dT%H%M%SZ)` and stage exactly
   `rvc_service`, `web-rt`, FCPE lock, canary unit and Caddy fragment.
4. Back up the old current symlink, canary unit, canary fragment, enable/active
   state and bounded journals under `/opt/voice-rtrvc/backups/$stamp`.
5. Create the immutable release, atomically update `current`, install only the
   canary unit/fragment, run `systemctl daemon-reload`, validate Caddy, reload
   Caddy and restart only `voice-rtrvc-canary.service`.
6. Wait for `/healthz` to become `{status:"ready"}`; run `tests/live-rtrvc.py`
   first through `ws://127.0.0.1:8093/ws/rvc`, then through
   `wss://voice-claude.lan.awesomeio.ru/ws/rvc` with the approved Origin.
7. Verify `voice-rvc.service` stayed active and its `NRestarts` did not change.
8. On any nonzero gate, restore current/unit/fragment/enable/active state,
   validate and reload Caddy, then verify production `:8090/healthz` is ready.

`tests/live-rtrvc.py` sends paced 20-ms PCM16 frames until it receives at least
three FCPE blocks. It fails unless `ready.variant == "v2-fcpe"`,
`ready.blockSeconds == 0.3`, `ready.f0method == "fcpe"`, every output is exactly
`14_400 * 2` bytes, non-silent and sample indexes are contiguous.

- [ ] **Step 4: Run deployment tests and shell checks**

Run:

```bash
.venv/bin/pytest -q tests/test_deploy_rtrvc_canary.py
bash -n deploy/deploy-rtrvc-canary.sh
```

Expected: success and explicit proof that a failed canary health gate leaves
the production RVC service untouched.

- [ ] **Step 5: Commit the deploy/rollback contract**

```bash
git add deploy/deploy-rtrvc-canary.sh tests/test_deploy_rtrvc_canary.py tests/live-rtrvc.py
git commit -m "feat: add reversible FCPE canary deployment"
```

## Task 5: Deploy FCPE canary and prove it does not disturb GPT v2

**Files:**
- Modify: `docs/RT_DEMO_SONNET_2026-09-07.md`
- Modify: `docs/LIVE_STATUS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: successful Tasks 1–4.
- Produces: active `voice-rtrvc-canary.service`, HTTP/WSS 200 on
  `voice-claude.lan.awesomeio.ru`, and a documented release/rollback result.

- [ ] **Step 1: Deploy and perform bounded live acceptance**

Run from the isolated worktree:

```bash
./deploy/deploy-rtrvc-canary.sh
curl -fsS https://voice-claude.lan.awesomeio.ru/healthz
.venv/bin/python tests/live-rtrvc.py --url wss://voice-claude.lan.awesomeio.ru/ws/rvc
.venv/bin/python tests/live-rvc.py --profile low-latency --seconds 10
ssh ubuntu@192.168.20.70 'systemctl is-active voice-rvc.service voice-rtrvc-canary.service; nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader'
```

Expected: FCPE reports three non-silent contiguous 0.3-s blocks; GPT v2 remains
ready, production service has no new restart and total VRAM remains below 4096
MiB with measurable headroom.

- [ ] **Step 2: Write current operations documentation**

Document:

1. Purpose and URL of the FCPE canary.
2. Exact start, stop, health and rollback commands.
3. The rule that canary is not the Asterisk path.
4. Evidence from the actual release stamp, live probe and production-v2 check.
5. Removal of stale claims about four running variants, `/tmp` execution and
   the `8098` GPT duplicate.

- [ ] **Step 3: Run the full regression suite and commit**

Run:

```bash
.venv/bin/pytest -q
node --test tests/*.test.cjs
git diff --check
```

Then:

```bash
git add README.md docs/RT_DEMO_SONNET_2026-09-07.md docs/LIVE_STATUS.md docs/OPERATIONS.md
git commit -m "docs: record managed FCPE canary acceptance"
```

## Coverage Review

| Spec requirement | Plan task |
| --- | --- |
| GPT v2 is primary; FCPE is canary only | Tasks 1, 4, 5 |
| No `/tmp`, `8098` or duplicate GPT dependency | Tasks 1, 2, 5 |
| Loopback-only, hardened, restartable canary | Task 2 |
| Canary Caddy ownership does not collide with production | Task 3 |
| Reversible scoped deploy; no production RVC restart | Task 4 |
| Live FCPE plus unaffected GPT v2 proof | Task 5 |
| Current documentation for later agents | Task 5 |

The SIP identity and Asterisk raw/processed bridge requirements are deliberately
not in this plan. They form the next independently testable sub-project after
Task 5 demonstrates that the FCPE canary is a managed, non-disruptive service.
