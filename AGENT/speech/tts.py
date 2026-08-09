"""Optionale lokale Sprachausgabe über Piper (betriebssystemunabhängig).

Piper liefert rohe 16-Bit-PCM-Daten; abgespielt wird über sounddevice, damit
derselbe Code unter Debian und Windows läuft.

Klang lässt sich per Umgebungsvariable einstellen, ohne den Code zu ändern:

- ``PIPER_MODEL``        Pfad zum Stimmenmodell (Standard: Thorsten-High, klarer)
- ``JUDE_TTS_PITCH``    Tonhöhe, 1.0 = neutral, >1 heller/weniger tief (Standard 1.06)
- ``JUDE_TTS_SPEED``    Sprechtempo, 1.0 = neutral, <1 langsamer
- ``JUDE_TTS_RATE``     Ausgabe-Samplerate der Soundkarte (Standard 44100)
- ``JUDE_TTS_PAUSE``    Pause zwischen Sätzen in Sekunden (Standard 0.25)
- ``PIPER_NOISE_SCALE`` / ``PIPER_NOISE_W`` feinkörnige Aussprachevariation
- ``PIPER_SPEAKER``     Sprecher-ID bei Mehrsprecher-Modellen

Die Tonhöhe wird ohne Tempoänderung erreicht: Piper erzeugt entsprechend
gedehnt (``length_scale``), anschließend wird das Signal um denselben Faktor
gestaucht – netto gleiche Dauer, höhere Tonlage.

Wichtig für die Klangqualität: gestaucht wird mit einem echten Polyphase-Filter
auf eine Samplerate, die die Soundkarte nativ beherrscht. Früher wurde das
Signal stattdessen mit einer krummen Rate (z. B. 23814 Hz) an das Gerät
gegeben; PulseAudio musste dann bei jedem Satz nachresampeln, was hörbar
scheppert. Ohne SciPy fällt der Code auf lineare Interpolation zurück.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

BASE_SAMPLE_RATE = 22050
TARGET_SAMPLE_RATE = 44100


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


def _model_sample_rate(model: str) -> int:
    """Native Samplerate aus der Piper-Konfiguration; medium und high liefern
    unterschiedliche Raten, deshalb nicht raten."""
    import json
    config = Path(f"{model}.json")
    if not config.is_file():
        return BASE_SAMPLE_RATE
    try:
        rate = json.loads(config.read_text("utf-8")).get("audio", {}).get("sample_rate")
        return int(rate) if rate else BASE_SAMPLE_RATE
    except Exception:
        return BASE_SAMPLE_RATE


def _resample(audio, source_rate: int, target_rate: int):
    """Bandbegrenzt auf *target_rate* umrechnen. Ein Polyphase-Filter hält die
    Höhen sauber; die lineare Notlösung klingt dumpfer, aber nie kaputt.

    Piper normalisiert seine Ausgabe bereits auf Vollaussteuerung. Jeder Filter
    schwingt an Transienten darüber hinaus, und ``astype(int16)`` würde diese
    Überschwinger umklappen lassen – aus einem Spitzenwert wird dann ein
    Knacken. Deshalb wird in float gerechnet und anschließend hart begrenzt.
    """
    import numpy as np
    source = np.asarray(audio, dtype=np.float32)
    if source_rate != target_rate:
        try:
            from scipy.signal import resample_poly
            ratio = Fraction(target_rate, source_rate).limit_denominator(1000)
            source = resample_poly(source, ratio.numerator, ratio.denominator)
        except ImportError:
            count = max(1, round(len(source) * target_rate / source_rate))
            positions = np.linspace(0, len(source) - 1, count)
            source = np.interp(positions, np.arange(len(source)), source)
    peak = float(np.abs(source).max()) if len(source) else 0.0
    if peak > 32767.0:  # Überschwinger zurückskalieren statt abschneiden
        source *= 32767.0 / peak
    return np.clip(source, -32768.0, 32767.0).astype(np.int16)


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

    pitch = max(0.5, min(2.0, _env_float("JUDE_TTS_PITCH", 1.06)))
    speed = max(0.5, min(2.0, _env_float("JUDE_TTS_SPEED", 1.0)))
    # length_scale > 1 dehnt die Sprache; das anschließende Stauchen beim
    # Resampling hebt die Dehnung wieder auf und hebt dabei die Tonhöhe.
    length_scale = pitch / speed

    command = [piper, "--model", model, "--output-raw", "--length-scale", f"{length_scale:.3f}"]
    pause = _env_float("JUDE_TTS_PAUSE", 0.25)
    if pause > 0:
        command += ["--sentence-silence", f"{pause:.2f}"]
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
    except ImportError as exc:
        raise RuntimeError("Sprachausgabe benötigt numpy und sounddevice.") from exc

    native_rate = _model_sample_rate(model)
    output_rate = int(_env_float("JUDE_TTS_RATE", TARGET_SAMPLE_RATE))
    # Piper hängt bei --sentence-silence gelegentlich ein einzelnes Byte an
    # (die Stille wird in Bytes statt in Samples gerechnet). Ohne das Kappen
    # bricht np.frombuffer die gesamte Ausgabe ab.
    raw = result.stdout[:len(result.stdout) - len(result.stdout) % 2]
    audio = np.frombuffer(raw, dtype=np.int16)
    # Die Tonhöhenverschiebung steckt im Verhältnis: aus native_rate * pitch
    # gelesen, aber mit output_rate ausgegeben.
    audio = _resample(audio, round(native_rate * pitch), output_rate)

    sd.play(audio, output_rate)
    if should_stop is None:
        sd.wait()
        return
    stream = sd.get_stream()
    while stream is not None and stream.active:
        if should_stop():
            sd.stop()
            break
        sd.sleep(40)
