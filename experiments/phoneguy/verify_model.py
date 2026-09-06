"""Offline acceptance probe; run with the VM-only experiment venv."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parent
os.environ.update(
    TORCH_FORCE_WEIGHTS_ONLY_LOAD="1",
    RVC_CUDA_GRAPH="0",
    OMP_NUM_THREADS="2",
    OPENBLAS_NUM_THREADS="1",
    HF_HUB_OFFLINE="1",
)
sys.path.insert(0, str(ROOT / "upstream"))

import numpy as np
import soundfile as sf
import torch

torch.set_num_threads(2)
model = ROOT / "models/PhoneGuyfnaf1V1.pth"
index = ROOT / "models/added_IVF359_Flat_nprobe_1_PhoneGuyfnaf1V1_v2.index"
archive = ROOT / "models/PhoneGuyfnaf1V1.zip"
digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
assert digest == "1ff846db9b9ab508b15dff3891da113d004047a378662e2acfe1de0abff109de"
unsafe = torch.serialization.get_unsafe_globals_in_checkpoint(model)
assert not unsafe, unsafe
checkpoint = torch.load(model, map_location="cpu", weights_only=True)
metadata = {
    "version": checkpoint.get("version", "v1"),
    "sample_rate": checkpoint["config"][-1],
    "f0": checkpoint.get("f0", 1),
    "speakers": checkpoint["weight"]["emb_g.weight"].shape[0],
    "unsafe_globals": unsafe,
    "sha256": digest,
}
del checkpoint
assert torch.cuda.is_available(), "GPU required for this experiment"
from infer.cli import create_config
from infer.vc.modules import VC

os.environ["weight_root"] = str(model.parent)
config = create_config()
assert config.device.startswith("cuda") and not config.is_half
print(json.dumps({"model": metadata, "device": config.device,
                  "gpu": torch.cuda.get_device_name()}, ensure_ascii=False), flush=True)
vc = VC(config)
vc.get_vc(model.name)
results = []
output_dir = ROOT / "outputs"
output_dir.mkdir(exist_ok=True)
for input_path in sorted((ROOT / "samples/input").glob("*.wav")):
    source, source_sr = sf.read(input_path)
    torch.cuda.synchronize()
    started = time.perf_counter()
    status, converted = vc.vc_single(
        0, str(input_path), 0, "rmvpe", str(index), 0.6, 0, 1.0, 0.33
    )
    torch.cuda.synchronize()
    seconds = time.perf_counter() - started
    print(status, flush=True)
    assert converted and converted[0] and converted[1] is not None, status
    sample_rate, samples = converted
    output_path = output_dir / input_path.name
    sf.write(output_path, samples, sample_rate, subtype="PCM_16")
    audio, sr = sf.read(output_path)
    assert np.isfinite(audio).all() and np.max(np.abs(audio)) > 0.001
    duration = len(source) / source_sr
    assert abs(len(audio) / sr - duration) < 0.25
    result = {
        "input": input_path.name,
        "input_seconds": duration,
        "output_seconds": len(audio) / sr,
        "wall_seconds": seconds,
        "rtf": seconds / duration,
        "peak": float(np.max(np.abs(audio))),
        "rms": float(np.sqrt(np.mean(audio ** 2))),
        "clipping_fraction": float(np.mean(np.abs(audio) >= 0.999)),
        "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
        "gpu_peak_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
    }
    results.append(result)
    print(json.dumps(result), flush=True)
assert len(results) == 4, "Expected English/Russian originals and repeats"
report = {"model": metadata, "torch": torch.__version__, "cuda": torch.version.cuda,
          "gpu": torch.cuda.get_device_name(), "precision": str(config.dtype),
          "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
          "results": results}
(output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print("PASS: four finite, non-silent GPU conversions; listening review still required.", flush=True)
