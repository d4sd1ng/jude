"""Optionale lokale Sprachausgabe über Piper (betriebssystemunabhängig).

Piper liefert rohe 16-Bit-PCM-Daten mit 22050 Hz; abgespielt wird über
sounddevice, damit derselbe Code unter Debian und Windows funktioniert.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SAMPLE_RATE = 22050


def _piper_binary() -> str | None:
    name = "piper.exe" if os.name == "nt" else "piper"
    sibling = Path(sys.executable).with_name(name)
    return shutil.which("piper") or (str(sibling) if sibling.is_file() else None)


def speak(text: str) -> None:
    from core.paths import MODELS_DIR
    default_model = MODELS_DIR / "piper" / "de_DE-thorsten-medium.onnx"
    model = os.getenv("PIPER_MODEL") or (str(default_model) if default_model.is_file() else None)
    piper = _piper_binary()
    if not text or not model:
        return
    if not piper:
        raise RuntimeError("Piper wurde nicht gefunden.")
    result = subprocess.run([piper, "--model", model, "--output-raw"],
                            input=text.encode("utf-8"), capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Sprachausgabe ist fehlgeschlagen: "
                           + result.stderr.decode("utf-8", "replace")[:300])
    try:
        import numpy as np
        import sounddevice as sd
        audio = np.frombuffer(result.stdout, dtype=np.int16)
        sd.play(audio, SAMPLE_RATE, blocking=True)
    except ImportError as exc:
        raise RuntimeError("Sprachausgabe benötigt numpy und sounddevice.") from exc
