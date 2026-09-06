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
- NVIDIA driver installation was started in the guest; final GPU verification
  and VM reboot evidence are appended after DKMS completes.
