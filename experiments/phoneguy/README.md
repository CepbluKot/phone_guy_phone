> Historical isolated trial. Integration is now deployed; current state is in
> [LIVE_STATUS](../../docs/LIVE_STATUS.md). Do not run a second GPU experiment
> alongside the live model without intentionally stopping the live service.

# PhoneGuyfnaf1V1: isolated model trial

## Scope and acceptance criteria

Requested 2026-09-06: test this specific ready-made RVC model before integrating
it. Keep the existing DSP demo and configurable playback delay unchanged.
All inference runs on VM 209 (192.168.20.70), never on the laptop or Frigate.
Laptop source directory mirrors `/opt/voice-changer/experiments/phoneguy`.

1. Verify archive SHA256, exact members, and weights-only loading.
2. Confirm GPU inference using GTX 1050 Ti 4 GB, FP32.
3. Convert Russian and English speech, validate finite non-silent audio.
4. Measure cold and warm conversion speed; do not equate file throughput
   with verified live microphone latency.
5. Keep before/after samples for listening. Synthetic samples establish a
   functional baseline, not proof of resemblance or quality on the user's voice.

## Sources

- Model: https://huggingface.co/C0ttontheBunny/Fnafucnmodels/resolve/main/PhoneGuyfnaf1V1.zip
- SHA256: `1ff846db9b9ab508b15dff3891da113d004047a378662e2acfe1de0abff109de`
- Members: `PhoneGuyfnaf1V1.pth` and
  `added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index`.
- Engine: https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
  commit `81eed5e8f68b6bed1789f682fe78cdd324495afc`.
- HuBERT and RMVPE: https://huggingface.co/lj1995/VoiceConversionWebUI
- Russian source: https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/denis/medium/samples/speaker_0.mp3
- English source: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ryan/medium/samples/speaker_0.mp3

Upstream code, weights, venv, and audio are excluded from git. Dependencies
are separate from the production app. No training, new public service,
GPU reassignment, or automatic model replacement is part of this trial.

## Initial profile

RVC v2; RMVPE pitch extraction; pitch shift 0; index rate 0.6;
protect 0.33; RMS mix 1; native output rate; no additional telephone filter.
Disable CUDA graphs for the initial baseline (`RVC_CUDA_GRAPH=0`).
Force safe tensor loading (`TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`).

## Results

Verified 2026-09-06 on VM 209. Four conversions passed in a transient sandboxed
systemd service running as ubuntu; MemoryMax=2700M, no swap, CPUQuota=250%,
no network access. Production container remained healthy and both private
HTTPS `/healthz` endpoints returned `{"status":"ok"}` after the run.

Model: v2, F0 enabled, 32000 Hz. Weights-only load succeeded, unsafe globals
scan returned an empty list. The embedding has 109 rows (legacy model layout;
not evidence of 109 trained voices), speaker ID 0 was used.

| Input (8.00 seconds each) | Conversion wall time | RTF | Output duration |
| --- | --- | --- | --- |
| English, first/cold call | 28.482 s | 3.560 | 7.98 s |
| English repeat | 1.362 s | 0.170 | 7.98 s |
| Russian | 1.350 s | 0.169 | 7.98 s |
| Russian repeat | 1.348 s | 0.168 | 7.98 s |

The cold call includes lazy loading/initialization after the RVC synthesizer
was loaded; it is **not** total process startup time. Entire service runtime
was 51.830 s. Steady-state throughput on these files is approximately 5.9x
real time. This is not a measured streaming latency guarantee.

Peak PyTorch tensor allocation: 1594.44 MiB; reserved allocator memory:
2346 MiB (not total driver VRAM). Peak process RSS: 1947.78 MiB; systemd
reported cgroup peak 2.3 GiB. GPU was released after the process exited.
The VM remains at 4 GiB RAM; no Proxmox/Frigate resources were changed.

All outputs are finite, non-silent PCM16; peaks 0.75–0.80, clipping fraction
0. Actual character resemblance, pronunciation quality, and live microphone
behavior remain unverified. These inputs are synthetic Piper demo voices,
not recordings of the user's voice.

Listen to `outputs/ru-before-after.wav` and `outputs/en-before-after.wav`:
8 seconds of the source, a 0.7-second pause, then the RVC output.
Raw measurements are saved in `result-2026-09-06.json` and `outputs/report.json`.
Audio artifacts are on both the laptop and VM; model assets/venv are VM-only.

## Reproduction

Use the pinned upstream commit above under `upstream/`. Install
`python3.12-venv` and `ffmpeg` on the VM; create `venv/` there. Initial trial
also installed `espeak-ng`, but the actual inputs are Piper samples and do
not use it. Install `requirements-resolved.txt` with the official PyTorch
cu118 extra index. Do not install these dependencies in the main app venv.

Place model members in `models/`, and the official assets at
`upstream/assets/hubert_base/{config.json,preprocessor_config.json,pytorch_model.bin}`
and `upstream/assets/rmvpe/rmvpe.pt`. Keep the original ZIP for verification.
Convert the first 8 seconds of each source MP3 to mono 24000 Hz WAV in
`samples/input/en.wav` and `samples/input/ru.wav`; duplicate each with
`_repeat.wav` suffix for a same-process warm comparison.

Run on the VM (existing result files are replaced by this explicit rerun):

```sh
sudo systemd-run --unit=phoneguy-trial --uid=ubuntu --wait --collect --pipe \
  -p MemoryMax=2700M -p MemorySwapMax=0 -p CPUQuota=250% \
  -p NoNewPrivileges=yes -p PrivateTmp=yes -p ProtectHome=yes \
  -p ProtectSystem=strict \
  -p ReadWritePaths=/opt/voice-changer/experiments/phoneguy \
  -p IPAddressDeny=any \
  /opt/voice-changer/experiments/phoneguy/venv/bin/python \
  /opt/voice-changer/experiments/phoneguy/verify_model.py
```

Next decision: listen to the comparison, then try a short real microphone
recording before integrating the chosen preset into the live app.
