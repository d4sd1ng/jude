#!/usr/bin/env python3
"""Exportiert Judes Gespräche als Trainingsdaten im JSONL-Chatformat.

Das Format passt sowohl auf lokales LoRA-Feintuning als auch auf die
Fine-Tuning-Schnittstelle von OpenAI — eine Datei, beide Wege.

Was ausgeschlossen wird und warum:

* ``training_allowed = 0`` — bereits beim Speichern gesperrt.
* **negativ bewertete Runden** — Antworten, die Tino als unzureichend markiert
  hat, sollen nicht antrainiert werden. Genau das würde sonst passieren.
* **zu kurze Antworten** — „ok" lehrt nichts.
* **Dubletten** — dieselbe Frage mehrfach verzerrt die Gewichtung.
* **Runden mit Geheimnissen** — Schlüssel, Passwörter, Token. Es wird nicht
  geschwärzt, sondern die ganze Runde verworfen: eine halb geschwärzte Zeile
  ist gefährlicher als keine, weil sie unbemerkt durchrutscht.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/training_export.py            # nur Bericht
    ../.venv/bin/python scripts/training_export.py --write    # Datei schreiben
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

#: Muster, bei denen eine Runde komplett verworfen wird.
GEHEIMNISSE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|ntn_[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}"
    r"|AIza[A-Za-z0-9_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|passwor[dt]\s*[:=]|api[_-]?key\s*[:=]|secret\s*[:=]|token\s*[:=])",
    re.IGNORECASE,
)

MIN_ANTWORT = 40          # Zeichen; darunter ist nichts zu lernen
MIN_FRAGE = 5


def systemprompt() -> str:
    """Derselbe Systemprompt wie im Betrieb – ohne Zeitstempel, der wäre
    beim Training nur Rauschen."""
    from core.agent import Agent
    from core.model_router import ModelRouter
    from core.tool_registry import ToolRegistry
    return Agent(ModelRouter(), ToolRegistry())._base_system


def sammeln() -> tuple[list[dict], Counter]:
    from services.database import connection

    grund = Counter()
    beispiele: list[dict] = []
    gesehen: set[str] = set()
    system = systemprompt()

    with connection() as db:
        # Negativ bewertete Modellläufe über die Route ausschließen.
        schlecht = {row["prompt_hash"] for row in db.execute(
            "SELECT prompt_hash FROM route_decisions WHERE user_feedback = -1")}
        zeilen = db.execute(
            "SELECT created_at, user_text, assistant_text, model, training_allowed, exclusion_reason "
            "FROM conversation_turns ORDER BY id"
        ).fetchall()

    for zeile in zeilen:
        frage = (zeile["user_text"] or "").strip()
        antwort = (zeile["assistant_text"] or "").strip()

        if not zeile["training_allowed"]:
            grund[zeile["exclusion_reason"] or "gesperrt"] += 1
            continue
        if len(frage) < MIN_FRAGE or len(antwort) < MIN_ANTWORT:
            grund["zu kurz"] += 1
            continue
        if GEHEIMNISSE.search(frage) or GEHEIMNISSE.search(antwort):
            grund["Geheimnis erkannt"] += 1
            continue
        if hashlib.sha256(frage.encode()).hexdigest() in schlecht:
            grund["negativ bewertet"] += 1
            continue
        schluessel = hashlib.sha256(f"{frage}\n{antwort}".encode()).hexdigest()
        if schluessel in gesehen:
            grund["Dublette"] += 1
            continue
        gesehen.add(schluessel)
        beispiele.append({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": frage},
            {"role": "assistant", "content": antwort},
        ]})
    return beispiele, grund


def main() -> int:
    parser = argparse.ArgumentParser(description="Trainingsdaten exportieren (JSONL).")
    parser.add_argument("--write", action="store_true", help="Datei wirklich schreiben")
    parser.add_argument("--out", default=None, help="Zieldatei (Standard: AI-Data/Jude/training/)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    beispiele, grund = sammeln()

    print("Trainingsdaten-Export  —  " + ("SCHREIBMODUS" if args.write else "nur Bericht"))
    print(f"\nBrauchbare Beispiele: {len(beispiele)}")
    if grund:
        print("\nVerworfen:")
        for name, anzahl in grund.most_common():
            print(f"  {anzahl:>4}  {name}")

    if beispiele:
        zeichen = sum(len(m["content"]) for b in beispiele for m in b["messages"])
        print(f"\nUmfang: rund {zeichen // 4:,} Token, "
              f"Antwort im Schnitt {sum(len(b['messages'][2]['content']) for b in beispiele) // len(beispiele)} Zeichen")

    # Einordnung, damit niemand mit 65 Beispielen ein Training startet.
    if len(beispiele) < 200:
        print(f"\nHinweis: {len(beispiele)} Beispiele sind für ein Feintuning zu wenig. "
              "Brauchbar wird es ab etwa 500; darunter lernt das Modell den Tonfall, "
              "nicht die Aufgabe.")
    elif len(beispiele) < 500:
        print(f"\nHinweis: {len(beispiele)} Beispiele sind ein Anfang, aber noch dünn. "
              "Ab etwa 500 wird ein Durchlauf sinnvoll.")

    if not args.write:
        print("\nMit --write schreiben.")
        return 0

    from core.paths import DATA_DIR
    ziel = Path(args.out) if args.out else (
        DATA_DIR / "training" / f"jude-chat-{datetime.now():%Y%m%d-%H%M}.jsonl")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with ziel.open("w", encoding="utf-8") as datei:
        for beispiel in beispiele:
            datei.write(json.dumps(beispiel, ensure_ascii=False) + "\n")
    print(f"\nGeschrieben: {ziel}  ({ziel.stat().st_size:,} Bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
