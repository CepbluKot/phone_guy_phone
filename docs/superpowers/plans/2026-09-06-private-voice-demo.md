# Private Phone Guy-like Voice Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a private browser-based live telephone/radio voice-effect demo on isolated VM 209, reachable only at `https://voice.lan.awesomeio.ru`.

**Architecture:** FastAPI serves a vanilla browser UI and WebSocket endpoint. The browser streams 48 kHz mono S16LE frames; the server applies deterministic DSP and returns playback frames. Terraform creates VM 209, Ansible deploys the container, and the existing private DNS/Caddy stack publishes it.

**Tech Stack:** Python 3.12, FastAPI, NumPy, Pedalboard, pytest, AudioWorklet, Docker Compose, Terraform bpg/proxmox, Ansible, Caddy, PowerDNS.

**Spec:** `docs/superpowers/specs/2026-09-06-phone-guy-voice-demo-design.md`

## Global Constraints

- Local source of truth is `/home/oleg/Documents/voice-changer`; server keeps runtime artifacts only.
- VM 209: `192.168.20.70`, 4 vCPU, 4 GiB RAM, 64 GiB local-lvm, exclusive GPU `03:00.0`.
- Do not modify VM 208, Frigate configuration, storage, cameras, or runtime.
- `voice.lan.awesomeio.ru` is private-only: DNS points to `10.19.87.1`, Caddy imports `private_site`, and public SNI is rejected.
- Do not persist microphone PCM, audio files, or transcripts.
- Binary WebSocket frames are mono 48 kHz S16LE, 960 samples / 1,920 bytes.

---

### Task 1: Scaffold and test the web service

**Files:**
- Create: `.gitignore`, `README.md`, `pyproject.toml`, `requirements.txt`
- Create: `app/__init__.py`, `app/main.py`, `tests/test_health.py`

- [ ] **Step 1: Write the failing health test**

```python
from fastapi.testclient import TestClient
from app.main import app

def test_healthz_reports_service_ready():
    response = TestClient(app).get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_health.py -q`  
Expected: import failure for missing `app.main`.

- [ ] **Step 3: Implement the minimal health application**

Create Python 3.12 metadata and requirements for FastAPI, Uvicorn, NumPy,
Pedalboard and pytest. Add `.gitignore` for `.venv/`, bytecode, `.env`,
Terraform state and `.terraform/`. Implement `GET /healthz` returning exactly
`{"status":"ok"}`. README states the private URL and no-audio-storage rule.

- [ ] **Step 4: Verify green and commit**

Run: `python -m pytest -q`  
Expected: PASS.

```bash
git add . && git commit -m 'chore: scaffold private voice demo'
```

### Task 2: Implement transient telephone DSP with tests

**Files:**
- Create: `app/dsp.py`, `tests/test_dsp.py`

**Interfaces:** `EffectSettings`, `validate_settings(data)`, and
`process_pcm16(frame, settings, sample_rate=48000) -> bytes`.

- [ ] **Step 1: Write failing tests**

```python
import pytest
from app.dsp import EffectSettings, process_pcm16, validate_settings

def test_process_preserves_20ms_frame_size():
    result = process_pcm16(b'\x00\x10' * 960, EffectSettings())
    assert len(result) == 1920
    assert result != b'\x00\x10' * 960

@pytest.mark.parametrize(('key', 'value'), [('pitchSemitones', 7), ('effectMix', 1.1), ('noiseMix', -0.1)])
def test_settings_reject_out_of_range_values(key, value):
    with pytest.raises(ValueError):
        validate_settings({key: value})
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_dsp.py -q`  
Expected: missing `app.dsp` import.

- [ ] **Step 3: Implement minimal bounded DSP**

Decode S16LE as float32; use high/low-pass telephone filters, bounded pitch
shift, compression, soft clipping and optional low-level seeded noise; clamp
and encode S16LE. Reject non-1,920-byte frames. Bounds: pitch `-6..6`, effect
mix `0..1`, noise `0..0.15`, gain `-18..12` dB.

- [ ] **Step 4: Verify green and commit**

Run: `python -m pytest -q`  
Expected: PASS.

```bash
git add app/dsp.py tests/test_dsp.py && git commit -m 'feat: add telephone voice effect'
```

### Task 3: Add WebSocket protocol and minimal browser UI

**Files:**
- Create: `web/index.html`, `web/app.js`, `web/style.css`
- Create: `tests/test_websocket.py`, `tests/test_static_ui.py`
- Modify: `app/main.py`

**Interfaces:** `WS /ws/audio` first receives JSON start frame then 1,920-byte
binary frames. It replies `{"type":"ready"}`, binary output frames, or JSON
`{"type":"error","code":"invalid_frame"}`.

- [ ] **Step 1: Write failing protocol test**

```python
from fastapi.testclient import TestClient
from app.main import app

def test_audio_socket_returns_processed_frame():
    with TestClient(app).websocket_connect('/ws/audio') as socket:
        socket.send_json({'type':'start','sampleRate':48000,'channels':1,'sampleFormat':'s16le','settings':{}})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_bytes(b'\x00\x10' * 960)
        assert len(socket.receive_bytes()) == 1920
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_websocket.py -q`  
Expected: WebSocket route failure.

- [ ] **Step 3: Implement server and UI**

Allow one active socket, validate the start frame, call `process_pcm16` for
binary frames, and clear session state in `finally`. Log only lifecycle and
duration. UI uses `getUserMedia`, AudioWorklet, a secure-origin check,
WebSocket `/ws/audio`, queued AudioBuffer playback, Start/Stop, level/status,
latency, and pitch/effect/noise/gain controls. Do not use browser storage.

- [ ] **Step 4: Add static UI test, verify green, and commit**

Assert `getUserMedia`, `AudioWorklet`, `/ws/audio`, and all four controls are
present. Run: `python -m pytest -q`. Expected: PASS.

```bash
git add app web tests && git commit -m 'feat: add live voice web UI'
```

### Task 4: Containerize and test its security contract

**Files:**
- Create: `Dockerfile`, `compose.yaml`, `tests/test_container_contract.py`

- [ ] **Step 1: Write failing container contract test**

```python
from pathlib import Path

def test_compose_is_hardened_and_health_checked():
    compose = Path('compose.yaml').read_text()
    assert 'healthcheck:' in compose
    assert 'read_only: true' in compose
    assert 'privileged: true' not in compose
    assert 'network_mode: host' not in compose
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_container_contract.py -q`  
Expected: missing `compose.yaml`.

- [ ] **Step 3: Build minimum hardened package**

Dockerfile uses Python 3.12 slim and non-root `voice` user. Compose starts
Uvicorn on port 8080 with restart unless-stopped, `read_only`, `/tmp` tmpfs,
`cap_drop: [ALL]`, `no-new-privileges`, and `/healthz` healthcheck.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest -q && docker compose up -d --build && curl -fsS http://127.0.0.1:8080/healthz && docker compose down`  
Expected: PASS and `{"status":"ok"}`.

```bash
git add Dockerfile compose.yaml tests && git commit -m 'build: package voice service'
```

### Task 5: Add Terraform and Ansible for VM 209

**Files:**
- Create: `infra/terraform/{main.tf,variables.tf,outputs.tf,terraform.tfvars.example,.gitignore}`
- Create: `infra/ansible/{inventory.ini.example,playbook.yml,group_vars/voice.yml,roles/voice/tasks/main.yml}`
- Create: `tests/test_infra_contract.py`

- [ ] **Step 1: Write failing IaC contract test**

```python
from pathlib import Path

def test_voice_vm_is_isolated_and_has_gpu():
    source = Path('infra/terraform/main.tf').read_text()
    assert 'vm_id       = 209' in source
    assert 'size         = 64' in source
    assert 'id     = "0000:03:00.0"' in source
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_infra_contract.py -q`  
Expected: missing Terraform source.

- [ ] **Step 3: Implement repeatable VM and guest provisioning**

Terraform uses `bpg/proxmox ~> 0.101`, q35/OVMF, local ignored credentials,
4 host-type cores, 4096 MiB, 64 GiB raw `scsi0` on `local-lvm`, virtio `vmbr0`,
static `.70`, onboot, and `hostpci0=0000:03:00.0`. Ansible installs Docker,
NVIDIA driver/toolkit, rsync; synchronizes tracked code to
`/opt/voice-changer`; starts Compose; and checks `nvidia-smi`, container health
and local `/healthz`.

- [ ] **Step 4: Validate and commit**

Run: `python -m pytest -q && terraform -chdir=infra/terraform init && terraform -chdir=infra/terraform validate && ansible-playbook -i infra/ansible/inventory.ini.example infra/ansible/playbook.yml --syntax-check`  
Expected: PASS.

```bash
git add infra tests && git commit -m 'infra: provision isolated GPU voice VM'
```

### Task 6: Publish private route, deploy, and prove it live

**Files:**
- Modify: `/home/oleg/Documents/homelab/dns-awesomeio/{records.tsv,caddy/Caddyfile,README.md,portal/inventory.json}`
- Create: `tests/test_private_route_contract.py`
- Create: `docs/{OPERATIONS.md,DEPLOYMENT_LOG_2026-09-06.md}`

- [ ] **Step 1: Write failing private-route test**

```python
from pathlib import Path

def test_private_voice_route_exists():
    records = Path('/home/oleg/Documents/homelab/dns-awesomeio/records.tsv').read_text()
    caddy = Path('/home/oleg/Documents/homelab/dns-awesomeio/caddy/Caddyfile').read_text()
    assert 'vm-voice-1' in records and '192.168.20.70' in records
    assert 'voice.lan.awesomeio.ru' in caddy and 'import private_site' in caddy
```

- [ ] **Step 2: Verify red, then change source only**

Run: `python -m pytest tests/test_private_route_contract.py -q`  
Expected: assertion failure. Add `vm-voice-1 -> .70` and `voice -> 10.19.87.1`,
the Caddy `private_site` proxy to `http://192.168.20.70:8080`, portal node and
docs. Add no public DNS or public Caddy host.

- [ ] **Step 3: Verify source, apply VM, deploy app and route**

Run tests, `terraform apply`, Ansible playbook, documented DNS deployment,
Caddy validation/reload, then resolve the private name and fetch `/healthz`
through VPN. Confirm public SNI aborts and VM port 8080 is not Internet-exposed.

- [ ] **Step 4: Test audio, Frigate, reboot and document**

Run five-minute browser microphone playback (latency <150 ms, no reconnects,
no audio files). Then check Frigate VM/container, recordings mapper, camera
route, four RTSP/ffprobe streams, fresh recording and errors. Reboot VM 209
only; repeat voice and Frigate checks. Record results and commit source docs.
