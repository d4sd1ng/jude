"""Optionale lokale Sprachausgabe über Piper (betriebssystemunabhängig).

Piper liefert rohe 16-Bit-PCM-Daten; abgespielt wird über sounddevice, damit
derselbe Code unter Debian und Windows läuft.

Klang lässt sich per Umgebungsvariable einstellen, ohne den Code zu ändern:

- ``PIPER_MODEL``        Pfad zum Stimmenmodell (Standard: Thorsten-High, klarer)
- ``JUDE_TTS_PITCH``    Tonhöhe, 1.0 = neutral, >1 heller/weniger tief (Standard 1.08)
- ``JUDE_TTS_SPEED``    Sprechtempo, 1.0 = neutral, <1 langsamer
- ``PIPER_NOISE_SCALE`` / ``PIPER_NOISE_W`` feinkörnige Aussprachevariation
- ``PIPER_SPEAKER``     Sprecher-ID bei Mehrsprecher-Modellen

Die Tonhöhe wird ohne Tempoänderung erreicht: Piper erzeugt entsprechend
gedehnt (``length_scale``), die Wiedergabe läuft passend schneller ab – netto
gleiche Dauer, höhere Tonlage.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_SAMPLE_RATE = 22050


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _piper_binary() -> str | None:
    name = "piper.exe" if os.name == "nt" else "piper"
    sibling = Path(sys.executable).with_name(name)
    return shutil.which("piper") or (str(sibling) if sibling.is_file() else None)


def _default_model() -> str | None:
    from core.paths import MODELS_DIR
    piper_dir = MODELS_DIR / "piper"
    for name in ("de_DE-thorsten-high.onnx", "de_DE-thorsten-medium.onnx"):
        candidate = piper_dir / name
        if candidate.is_file():
            return str(candidate)
    return None


def speak(text: str, should_stop=None) -> None:
    """Spricht *text*. Ist ``should_stop`` gesetzt (Callable -> bool), wird die
    Wiedergabe abgebrochen, sobald es True liefert – das ermöglicht das
    Überspringen einzelner Briefing-Abschnitte."""
    model = os.getenv("PIPER_MODEL") or _default_model()
    piper = _piper_binary()
    if not text or not model:
        return
    if not piper:
        raise RuntimeError("Piper wurde nicht gefunden.")

    pitch = max(0.5, min(2.0, _env_float("JUDE_TTS_PITCH", 1.08)))
    speed = max(0.5, min(2.0, _env_float("JUDE_TTS_SPEED", 1.0)))
    # length_scale > 1 dehnt die Sprache; die schnellere Wiedergabe hebt sie
    # wieder auf und verschiebt dabei nur die Tonhöhe nach oben.
    length_scale = pitch / speed
    playback_rate = int(BASE_SAMPLE_RATE * pitch)

    command = [piper, "--model", model, "--output-raw", "--length-scale", f"{length_scale:.3f}"]
    for flag, env in (("--noise-scale", "PIPER_NOISE_SCALE"), ("--noise-w-scale", "PIPER_NOISE_W")):
        value = os.getenv(env, "").strip()
        if value:
            command += [flag, value]
    speaker = os.getenv("PIPER_SPEAKER", "").strip()
    if speaker:
        command += ["--speaker", speaker]

    result = subprocess.run(command, input=text.encode("utf-8"), capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Sprachausgabe ist fehlgeschlagen: "
                           + result.stderr.decode("utf-8", "replace")[:300])
    try:
        import numpy as np
        import sounddevice as sd
        audio = np.frombuffer(result.stdout, dtype=np.int16)
        sd.play(audio, playback_rate)
        if should_stop is None:
            sd.wait()
        else:
            stream = sd.get_stream()
            while stream is not None and stream.active:
                if should_stop():
                    sd.stop()
                    break
                sd.sleep(40)
    except ImportError as exc:
        raise RuntimeError("Sprachausgabe benötigt numpy und sounddevice.") from exc
