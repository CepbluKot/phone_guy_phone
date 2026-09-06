# Deployment log — 2026-09-06

- Created Proxmox VM 209 `voice-changer` with `192.168.20.70`, 4 vCPU, 4 GiB
  RAM, 64 GiB local-lvm, autostart and exclusive GTX 1050 Ti passthrough.
- Deployed the Dockerized FastAPI telephone-effect demo from the laptop source
  tree. Guest-local `/healthz` returned `{"status":"ok"}` and the container
  became healthy.
- Added private DNS `vm-voice-1.lan.awesomeio.ru` and
  `voice.lan.awesomeio.ru`, plus a Caddy VPN-only reverse proxy to
  `192.168.20.70:8080`. The VPS Caddy config validated and the private HTTPS
  health route returned `{"status":"ok"}`.
- Rebooted VM 209 after driver installation. `nvidia-smi` reports
  `NVIDIA GeForce GTX 1050 Ti` with driver `580.173.02`; the Docker container
  returned to `healthy` after the reboot.
- Performed a private end-to-end WebSocket check: the `wss://` endpoint
  accepted a 48 kHz S16LE start packet and returned a processed 1,920-byte PCM
  frame. No microphone recording was used for this check.
- Enabled the guest firewall. It denies inbound traffic by default, allows SSH
  from LAN/VPN, and allows the application port only from the LAN and the VPN
  Caddy host. The private HTTPS health route and WebSocket check remained
  healthy after this change.
- Rechecked Frigate VM 208: it is running and `healthy`, has no `hostpci`
  entry, and a recent `cam_112` recording is readable (`hevc`).
