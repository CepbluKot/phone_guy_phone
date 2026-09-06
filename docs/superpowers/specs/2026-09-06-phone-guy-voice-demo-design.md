# Private Phone Guy-like Voice Demo — Design Specification

**Status:** approved direction, pending specification review  
**Date:** 2026-09-06  
**Owner:** Oleg

## 1. Goal

Deploy a self-hosted, private, real-time voice-effect demo. A browser connected
through the homelab VPN opens a minimal web UI, grants microphone access, and
hears the processed result in the style of the in-universe telephone/radio sound
of FNaF's Phone Guy.

The first release creates a recognisable *Phone Guy-like communication-channel
effect*, not an exact synthetic imitation of a particular actor. It must not
need a downloaded character voice model or transmit microphone audio to a third
party.

## 2. Scope and non-goals

### In scope for release 1

- A dedicated GPU VM, source-controlled provisioning, and a reproducible
  Docker deployment.
- HTTPS UI at `https://voice.lan.awesomeio.ru`, reachable through the existing
  WireGuard/private-DNS path and unreachable from the VPS public listener.
- Start/stop microphone controls, input/output level indicators, a live latency
  indicator, and controls for effect intensity, line noise, pitch, and output
  gain.
- A bidirectional WebSocket audio session: browser captures mono PCM, the
  service processes it in short chunks, and the browser plays the result.
- No audio recording, database, account system, cloud inference, or external
  API key.
- Tests for DSP configuration validation, WebSocket framing, the health route,
  and reverse-proxy/private-DNS configuration checks.

### Explicitly out of scope for release 1

- Exact cloning of an actor or use of an unlicensed character model.
- Training a voice-conversion model.
- A virtual microphone consumable by Discord, Telegram, OBS, or games. That
  requires a separate PipeWire bridge on the workstation and becomes release 2.
- Public Internet access, public DNS records, or a public listener for this
  application.
- Any modification to Frigate's VM, cameras, storage, or runtime.

## 3. Live infrastructure allocation

| Item | Release-1 decision | Rationale |
| --- | --- | --- |
| Proxmox VM | `209`, `voice-changer` | IDs `200` and `208` are used; `209` is available. |
| Guest OS | Ubuntu 24.04 cloud image | Existing homelab provisioning standard. |
| LAN address | `192.168.20.70/24` | Not documented or responsive in source/live probes. |
| CPU | 4 vCPU | Enough for the API and audio chunking without moving Frigate. |
| RAM | 4 GiB, no ballooning | Keeps committed VM memory within host capacity. |
| System disk | 64 GiB on `local-lvm` | `local-lvm` had roughly 110 GiB free at design time. |
| GPU | NVIDIA GTX 1050 Ti (`03:00.0`) passthrough, exclusive to VM 209 | GPU is not presently attached to VM 208; no GPU sharing with Frigate. |
| VM network | `vmbr0`, gateway `192.168.20.1`, DNS `192.168.20.12` and `10.19.87.1` | Matches the homelab LAN and private-DNS topology. |
| Host DNS name | `vm-voice-1.lan.awesomeio.ru` -> `192.168.20.70` | Canonical inventory naming convention. |
| Service name | `voice.lan.awesomeio.ru` -> `10.19.87.1` | Caddy terminates private TLS on the WireGuard VPS. |
| Backend port | `8080/tcp` on `192.168.20.70` | Not exposed directly to the Internet. |

The separate VM prevents sharing guest CPU, RAM, storage, Docker runtime, and
GPU with Frigate. Both guests still share the Proxmox host's physical CPU; this
release does not change Frigate CPU affinity because doing so is unrelated and
riskier than the demo.

## 4. Architecture

```text
VPN/LAN browser
  -- HTTPS / WSS --> Caddy at 10.19.87.1
  -- HTTP / WSS --> voice-changer VM 192.168.20.70:8080
  -- WebSocket PCM --> self-written FastAPI audio service
  -- processed PCM --> browser speakers
```

The browser is served by the same FastAPI application as static files. HTTPS is
essential: browsers allow microphone access only in a secure context. Caddy
terminates the existing private wildcard certificate and forwards HTTP and
WebSocket upgrade traffic to the VM. The VM accepts requests only from the
homelab/VPN side; no port is added to the VPS public listener.

The initial effect is deterministic DSP, implemented in the project rather than
using a hosted voice-changing service:

1. Downmix microphone input to 48 kHz mono 16-bit PCM.
2. Apply input gate and gain control.
3. Apply gentle pitch shift with a bounded semitone control.
4. Band-limit the signal to approximately 350–3,400 Hz.
5. Apply light saturation/compression and optional low-level line noise.
6. Limit output amplitude and return chunked PCM to the browser.

Chunk size is 20 ms. The server reports processing time for every response so
the UI can calculate and display end-to-end latency. A single active session is
the release-1 operational limit; a new session receives a clear busy response.

## 5. Components and interfaces

| Component | Responsibility | Interface |
| --- | --- | --- |
| `infra/terraform/` | Provision VM 209, disk, NIC, cloud-init, and exclusive GPU passthrough. | Terraform outputs VM IP and SSH command. |
| `deploy/compose.yaml` | Start the container with the least needed Linux capabilities and a health check. | Exposes `127.0.0.1:8080` within VM plus LAN listener for Caddy. |
| `app/main.py` | Serve UI, `/healthz`, and `/ws/audio`; own active-session lifecycle. | JSON control frames and binary PCM frames. |
| `app/dsp.py` | Validate effect settings and transform signed 16-bit PCM chunks. | `process_pcm16(chunk: bytes, settings: EffectSettings) -> bytes`. |
| `web/` | Browser capture, WebSocket client, AudioWorklet playback, controls, and status. | Sends exactly 960 PCM samples per audio frame at 48 kHz. |
| `dns-awesomeio/records.tsv` | Add `vm-voice-1` and service record `voice`. | Authoritative private A records. |
| `dns-awesomeio/caddy/Caddyfile` | Add the VPN-only reverse-proxy host. | `voice.lan.awesomeio.ru` to `http://192.168.20.70:8080`. |
| `new-vpn/OPERATIONS.md` | Add the VM and private endpoint to current topology. | Current-state documentation only. |

Control frames are UTF-8 JSON. Before binary audio begins, the browser must
send:

```json
{"type":"start","sampleRate":48000,"channels":1,"sampleFormat":"s16le","settings":{"pitchSemitones":-1.5,"effectMix":0.85,"noiseMix":0.04,"outputGainDb":0}}
```

Each audio frame is exactly 1,920 bytes: 960 little-endian signed 16-bit mono
samples, representing 20 ms at 48 kHz. The matching reply is the processed
PCM frame. `stop` ends the session; malformed frames close the socket with an
explicit protocol error.

## 6. Security and privacy

- The service DNS record points to `10.19.87.1`; it is not published in the
  public `awesomeio.ru` zone.
- The existing Caddy `private_site` protection and public-SNI abort rule must
  apply to the new host.
- The app must not write microphone samples, effect frames, or text transcripts
  to disk or logs. Logs may contain only session lifecycle, route, and timing
  metadata.
- The container runs as a non-root user, with a read-only root filesystem and
  no Docker socket, host networking, privileged mode, or mounted secrets.
- The UI must reject use from a non-HTTPS origin and display a clear microphone
  permission failure.

## 7. Verification and acceptance criteria

1. `terraform plan` is deterministic, and applying it creates only VM 209 and
   its resources.
2. The VM boots automatically, obtains `192.168.20.70`, and reports the
   passed-through GTX 1050 Ti through `nvidia-smi`.
3. The container health endpoint returns HTTP 200 locally and through
   `https://voice.lan.awesomeio.ru` from a VPN client.
4. A browser microphone permission prompt appears at the private HTTPS URL;
   speaking produces audible transformed output with no saved audio files.
5. The UI displays a measured live latency and must stay below 150 ms in a
   five-minute LAN/VPN test without WebSocket reconnects or browser audio
   underruns.
6. Requests using `voice.lan.awesomeio.ru` against public `94.102.89.13` are
   rejected; no direct public port exists for the VM backend.
7. Frigate remains healthy: VM 208 running, container healthy, recordings
   mapper mounted, route to camera LAN present, all four RTSP sources readable,
   and a fresh recording segment valid.
8. The VM is rebooted once after deployment; the UI, GPU, Caddy route, and
   Frigate smoke checks are repeated successfully.

## 8. Release 2 decision gate

After release 1 passes, run a measured GPU feasibility spike for RVC inference
inside VM 209. It may be added only if it fits within the 4 GiB GPU memory
budget, remains below the 150 ms end-to-end latency target, and uses a model
whose source and permission are documented. The optional workstation PipeWire
bridge is a separate release-2 specification after the browser demo works.
