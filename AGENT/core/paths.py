"""Zentrale, betriebssystemunabhängige Pfadauflösung.

Jude läuft von derselben NTFS-Datenplatte unter Debian und Windows. Die Wurzel
(AI-Data) wird in dieser Reihenfolge bestimmt:

1. Umgebungsvariable ``AI_DATA_ROOT`` (bzw. ``JUDE_DATA_ROOT``)
2. Linux-Standardmountpunkt ``/media/d4sd1ng/AI-Data``
3. Windows: Laufwerksbuchstaben D:–M: mit ``Jude/``- und ``Projects/``-Ordner
4. Notanker: vier Ebenen über dieser Datei (…/AI-Data/Projects/Jude/AGENT/core)

Alle anderen Pfade leiten sich hiervon ab; kein Modul außer diesem darf
absolute Datenpfade enthalten.
"""

from __future__ import annotations

import os
from pathlib import Path


def _sieht_aus_wie_ai_data_root(kandidat: Path) -> bool:
    """Bloss ``is_dir()`` reicht nicht: ein alter Einhaengepunkt kann als leerer,
    root-eigener Verzeichnisstumpf liegen bleiben, wenn das Laufwerk woanders
    neu eingehaengt wird (gemessen 06.09.2026, /media/d4sd1ng/AI-Data bestand
    nach einem Wechsel auf /mnt/ai-data als leeres 0755-root:root-Verzeichnis
    mit fremdem Inhalt weiter - is_dir() lieferte True, jeder Schreibzugriff
    darunter (z.B. CALENDAR_DIR.mkdir()) scheiterte dann mit PermissionError,
    weit entfernt von dieser Stelle und ohne ersichtlichen Zusammenhang).
    Verlangt stattdessen einen Beweis, der nur auf der echten Platte existiert:
    genau die Datei, von der aus dieser Code selbst gerade laeuft, muss unter
    dem Kandidaten wiederzufinden sein."""
    return (kandidat / "Projects" / "Jude" / "AGENT" / "core" / "paths.py").is_file()


def _detect_root() -> Path:
    env = os.getenv("AI_DATA_ROOT") or os.getenv("JUDE_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    linux_default = Path("/media/d4sd1ng/AI-Data")
    if _sieht_aus_wie_ai_data_root(linux_default):
        return linux_default.resolve()
    if os.name == "nt":
        for letter in "DEFGHIJKLM":
            candidate = Path(f"{letter}:/")
            if (candidate / "Jude").is_dir() and (candidate / "Projects").is_dir():
                return candidate.resolve()
    return Path(__file__).resolve().parents[4]


AI_DATA_ROOT = _detect_root()
# Judes eigener Bestand (Daten, Modelle, Sicherungen, tmp) liegt seit 2026-08
# im Projektordner selbst – ein Ort für Code UND Daten. Ableitung über
# __file__ statt AI_DATA_ROOT, damit das auf Debian und Windows greift.
JUDE_DIR = Path(os.getenv("JUDE_DIR") or Path(__file__).resolve().parents[2])
DATA_DIR = Path(os.getenv("JUDE_DATA_DIR") or (JUDE_DIR / "data"))
MODELS_DIR = JUDE_DIR / "models"
MEALS_DIR = AI_DATA_ROOT / "Essensplan"
CALENDAR_DIR = AI_DATA_ROOT / "Kalender"
IMAGES_DIR = JUDE_DIR / "images"
TEST_DATA_DIR = JUDE_DIR / "test-data"
TEST_REPOS_DIR = JUDE_DIR / "test-repos"
GENERATED_TOOLS_DIR = Path(os.getenv("JUDE_GENERATED_TOOLS_DIR") or (JUDE_DIR / "generated_tools"))
ICT_PROMPT_FILE = AI_DATA_ROOT / "Projects" / "ICT_SNIPER" / "ict_trading_bot_systemprompt_1.txt"
