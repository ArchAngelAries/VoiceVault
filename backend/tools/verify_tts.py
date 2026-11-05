"""
Run a short real generation on every integrated speech model and record the result.

A passing record in data/tts_verification.json moves the model to the TESTED state.
Usage (from backend/):   ..\\venv\\Scripts\\python.exe tools\\verify_tts.py [model-id ...]
VibeVoice needs a voice: set VV_REFERENCE_AUDIO to a reference WAV (3-25 s, 24 kHz).
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import os  # noqa: E402
from core import settings  # noqa: E402
from core.model_paths import model_roots  # noqa: E402
from core.paths import VERIFICATION_FILE  # noqa: E402
from engines.base import ModelState, SynthesisRequest  # noqa: E402
from engines.maya1 import Maya1Engine  # noqa: E402
from engines.omnivoice import OmniVoiceEngine  # noqa: E402
from engines.breeze import BreezeEngine  # noqa: E402
from engines.fish_drama import FishDramaEngine  # noqa: E402
from engines.registry import ModelRegistry  # noqa: E402
from engines.vibevoice_engine import VibeVoiceEngine  # noqa: E402

PHRASE = "VoiceVault speech check. That worked."
REFERENCE = Path(os.environ["VV_REFERENCE_AUDIO"]) if os.environ.get("VV_REFERENCE_AUDIO") else None


def request_for(engine_id: str) -> SynthesisRequest:
    if engine_id == "vibevoice":
        if REFERENCE is None or not REFERENCE.is_file():
            raise ValueError("Set VV_REFERENCE_AUDIO to a valid 3-25 second reference WAV for VibeVoice verification.")
        return SynthesisRequest(text=PHRASE, reference_audio=REFERENCE, seed=0, max_new_tokens=1200)
    return SynthesisRequest(text=PHRASE, seed=0, max_new_tokens=1200)


def main(only: list[str]) -> int:
    engines = [Maya1Engine(), OmniVoiceEngine(), VibeVoiceEngine(), BreezeEngine(), FishDramaEngine()]
    by_id = {e.engine_id: e for e in engines}
    registry = ModelRegistry(engines, model_roots(settings.load()), VERIFICATION_FILE)
    records = json.loads(VERIFICATION_FILE.read_text(encoding="utf-8")) if VERIFICATION_FILE.is_file() else {}

    failures = 0
    for info in registry.discover():
        if only and info.id not in only:
            continue
        if info.state not in (ModelState.INTEGRATED, ModelState.TESTED):
            print(f"skip  {info.id}: {info.state.value}")
            continue
        engine = by_id[info.engine_id]
        print(f"test  {info.id} ...", flush=True)
        record = {"checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "device": "cuda" if torch.cuda.is_available() else "cpu",
                  "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                  "torch": torch.__version__,
                  "transformers": __import__("transformers").__version__}
        try:
            request = request_for(info.engine_id)
            t0 = time.time()
            engine.load(info, device="cuda", dtype="bfloat16")
            load_s = time.time() - t0
            result = engine.synthesize(request)
            seconds = len(result.samples) / result.sample_rate
            rms = float(np.sqrt(np.mean(result.samples ** 2)))
            ok = 1.0 < seconds < 30.0 and rms > 0.01 and result.meta.get("device") == "cuda"
            record.update({
                "status": "passed" if ok else "failed",
                "audio_seconds": round(seconds, 2),
                "rms": round(rms, 4),
                "load_seconds": round(load_s, 1),
                "generate_seconds": result.meta["timings_s"]["generate"],
                "real_time_factor": round(result.meta["timings_s"]["generate"] / seconds, 2),
                "stopped_on_eos": result.meta["stopped_on_eos"],
            })
            if not ok:
                record["error"] = f"audio check failed (seconds={seconds:.2f}, rms={rms:.4f})"
        except Exception as exc:  # record the failure rather than stop the whole run
            record.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            engine.unload()
        records[info.id] = record
        failures += record["status"] != "passed"
        print(f"      -> {record['status']}  {({k: record[k] for k in record if k in ('audio_seconds', 'real_time_factor', 'error')})}")

    VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERIFICATION_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"wrote {VERIFICATION_FILE}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
