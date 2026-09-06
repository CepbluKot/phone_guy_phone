# Voice Changer Operations

## Runtime

- VM: `209` / `voice-changer`, `192.168.20.70`, Ubuntu 24.04.
- Allocation: 4 vCPU, 4 GiB RAM, 64 GiB local-lvm; NVIDIA GTX 1050 Ti is
  attached exclusively as PCI `03:00.0`.
- Private UI: `https://voice.lan.awesomeio.ru`; the name resolves only in the
  `lan.awesomeio.ru` private zone and Caddy listens on the VPN address.
- Guest firewall: inbound traffic is denied by default. SSH is allowed from
  the LAN and VPN; port `8080` is accepted only from the LAN and the VPN Caddy
  host `10.19.87.1`.
- Runtime source is copied from this laptop to `/opt/voice-changer` on the VM.
  The Git source of truth remains this local project.

## Deploy an application update

```bash
rsync -a --delete --exclude .git --exclude .venv --exclude infra --exclude tests --exclude docs \
  /home/oleg/Documents/voice-changer/ ubuntu@192.168.20.70:/tmp/voice-changer/
ssh ubuntu@192.168.20.70 \
  'sudo rm -rf /opt/voice-changer && sudo mv /tmp/voice-changer /opt/voice-changer && \
   sudo docker compose -f /opt/voice-changer/compose.yaml up -d --build'
```

## Core checks

```bash
curl -fsS https://voice.lan.awesomeio.ru/healthz
ssh ubuntu@192.168.20.70 'sudo docker compose -f /opt/voice-changer/compose.yaml ps'
ssh ubuntu@192.168.20.70 nvidia-smi
```

No microphone audio is persisted by the application. The current release uses
deterministic telephone/radio DSP; the GPU is reserved for an optional later
RVC inference experiment.
