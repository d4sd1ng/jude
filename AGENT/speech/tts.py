"""Optionale lokale Sprachausgabe über Piper (betriebssystemunabhängig).

Piper liefert rohe 16-Bit-PCM-Daten; abgespielt wird über sounddevice, damit
derselbe Code unter Debian und Windows läuft.

Klang lässt sich per Umgebungsvariable einstellen, ohne den Code zu ändern:

- ``PIPER_MODEL``        Pfad zum Stimmenmodell (Standard: Thorsten-High, klarer)
- ``JUDE_TTS_PITCH``    Tonhöhe, 1.0 = neutral, >1 heller/weniger tief (Standard 1.06)
- ``JUDE_TTS_SPEED``    Sprechtempo, 1.0 = neutral, <1 langsamer
- ``JUDE_TTS_RATE``     Ausgabe-Samplerate der Soundkarte (Standard 44100)
- ``JUDE_TTS_PAUSE``    Pause zwischen Sätzen in Sekunden (Standard 0.1)
- ``JUDE_TTS_HEADROOM`` Aussteuerungsreserve, 0.89 = 1 dB unter Vollausschlag
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
    # Piper gibt mit 100 % Vollaussteuerung aus. Wird das ungedämpft an die
    # Soundkarte gereicht, übersteuert jede weitere Stufe (PulseAudio mischt und
    # rechnet auf die Geräterate um) – hörbar als Kratzen und Rauschen.
    # 3 dB Reserve lassen die Kette sauber arbeiten.
    headroom = max(0.1, min(1.0, _env_float("JUDE_TTS_HEADROOM", 0.89)))
    peak = float(np.abs(source).max()) if len(source) else 0.0
    limit = 32767.0 * headroom
    if peak > limit:
        source *= limit / peak
    return np.clip(source, -32768.0, 32767.0).astype(np.int16)


# --------------------------------------------------------------- Aussprache

_ONES = ("null", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
         "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn",
         "achtzehn", "neunzehn")
_TENS = ("", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig")
_MONTHS = ("", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember")


def _below_hundred(value: int) -> str:
    if value < 20:
        return _ONES[value]
    tens, ones = divmod(value, 10)
    return _TENS[tens] if not ones else f"{_ONES[ones]}und{_TENS[tens]}"


def number_words(value: int) -> str:
    """Ganze Zahl bis 999.999 als deutsches Zahlwort."""
    if value < 0:
        return "minus " + number_words(-value)
    if value < 100:
        return _below_hundred(value)
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = ("hundert" if hundreds == 1 else _ONES[hundreds] + "hundert")
        return head + (_below_hundred(rest) if rest else "")
    if value < 1_000_000:
        thousands, rest = divmod(value, 1000)
        head = ("tausend" if thousands == 1 else number_words(thousands) + "tausend")
        return head + (number_words(rest) if rest else "")
    return str(value)


def spoken_german(text: str) -> str:
    """Schreibt Uhrzeiten, Datumsangaben und Zahlen aus.

    Piper spricht Ziffern und Satzzeichen wörtlich: aus „21:29 Uhr“ wurde
    Kauderwelsch. Die Umschrift geschieht vor der Synthese, damit die Ausgabe
    klingt wie gesprochene Sprache.
    """
    import re

    def time_repl(m):
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return m.group(0)
        if minute == 0:
            return f"{number_words(hour)} Uhr"
        return f"{number_words(hour)} Uhr {number_words(minute)}"

    def date_repl(m):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        day_word = _below_hundred(day) + ("ster" if day in (1, 3, 7, 8) else "ter")
        day_word = {"einster": "erster", "dreister": "dritter", "siebenster": "siebter",
                    "achtster": "achter"}.get(day_word, day_word)
        return f"{day_word} {_MONTHS[month]} {number_words(year)}"

    def plain_number(m):
        raw = m.group(0)
        digits = raw.replace(".", "")            # Tausenderpunkte
        whole, _, frac = digits.partition(",")
        try:
            spoken = number_words(int(whole))
        except ValueError:
            return raw
        if frac:
            spoken += " Komma " + " ".join(_ONES[int(d)] for d in frac if d.isdigit())
        return spoken

    text = re.sub(r"\b(\d{1,2}):(\d{2})(?:\s*Uhr)?", time_repl, text)
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", date_repl, text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b|\b\d+(?:,\d+)?\b", plain_number, text)
    text = text.replace("°C", " Grad").replace("%", " Prozent").replace("€", " Euro")
    return text


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

    # Standard neutral: Piper 1.6 dehnt bei length-scale nicht mehr zuverlaessig;
    # jede Verschiebung machte die Stimme messbar ~6 % zu schnell und gepresst.
    pitch = max(0.5, min(2.0, _env_float("JUDE_TTS_PITCH", 1.0)))
    speed = max(0.5, min(2.0, _env_float("JUDE_TTS_SPEED", 1.0)))
    # length_scale > 1 dehnt die Sprache; das anschließende Stauchen beim
    # Resampling hebt die Dehnung wieder auf und hebt dabei die Tonhöhe.
    length_scale = pitch / speed

    command = [piper, "--model", model, "--output-raw", "--length-scale", f"{length_scale:.3f}"]
    pause = _env_float("JUDE_TTS_PAUSE", 0.1)
    if pause > 0:
        command += ["--sentence-silence", f"{pause:.2f}"]
    for flag, env in (("--noise-scale", "PIPER_NOISE_SCALE"), ("--noise-w-scale", "PIPER_NOISE_W")):
        value = os.getenv(env, "").strip()
        if value:
            command += [flag, value]
    speaker = os.getenv("PIPER_SPEAKER", "").strip()
    if speaker:
        command += ["--speaker", speaker]

    spoken = spoken_german(text)
    result = subprocess.run(command, input=spoken.encode("utf-8"), capture_output=True)
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
