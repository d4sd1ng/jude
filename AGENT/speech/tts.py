"""Optionale lokale Sprachausgabe über Piper."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def speak(text: str) -> None:
    default_model = Path("/media/d4sd1ng/AI-Data/Jude/models/piper/de_DE-thorsten-medium.onnx")
    model = os.getenv("PIPER_MODEL") or (str(default_model) if default_model.is_file() else None)
    sibling_piper = Path(sys.executable).with_name("piper")
    piper = shutil.which("piper") or (str(sibling_piper) if sibling_piper.is_file() else None)
    aplay = shutil.which("aplay")
    if not text or not model:
        return
    if not piper or not aplay:
        raise RuntimeError("Piper oder aplay wurde nicht gefunden.")
    piper_process = subprocess.Popen([piper, "--model", model, "--output-raw"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=False)
    if piper_process.stdin is None or piper_process.stdout is None:
        piper_process.kill()
        raise RuntimeError("Piper-Audiopipeline konnte nicht geöffnet werden.")
    audio_process = subprocess.Popen([aplay, "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                                     stdin=piper_process.stdout)
    piper_process.stdin.write(text.encode("utf-8"))
    piper_process.stdin.close()
    audio_process.wait()
    if piper_process.wait() != 0 or audio_process.returncode != 0:
        raise RuntimeError("Sprachausgabe ist fehlgeschlagen.")
