#!/usr/bin/env python3
"""Nimmt die uhrzeitgesteuerte Agentenbeauftragung wieder heraus.

Der Dienstplan liess fuenf Mitarbeiter zu festen Uhrzeiten loslaufen – ohne dass
jemand entschied, ob die Arbeit gerade gebraucht wird. Beauftragt wird
stattdessen von Jude, und der entscheidet im Zusammenhang.

Systemaufgaben ohne Agentenbezug (naechtliche Sicherung) bleiben unberuehrt;
entfernt wird ausschliesslich, was ``delegate_to_agent`` ausloest. Vor dem
Schreiben legt das Skript eine Kopie der Datei neben das Original.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/dienstplan_entfernen.py            # zeigt an
    ../.venv/bin/python scripts/dienstplan_entfernen.py --apply    # entfernt
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Agenten-Dienstplan entfernen.")
    parser.add_argument("--apply", action="store_true", help="wirklich entfernen")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from core.paths import DATA_DIR

    datei = DATA_DIR / "scheduled_tasks.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))

    print("Dienstplan entfernen  —  " + ("SCHREIBMODUS" if args.apply else "Trockenlauf"))
    print(f"Datei: {datei}\n")

    raus = [tid for tid, a in daten.items() if a.get("tool") == "delegate_to_agent"]
    for tid, aufgabe in sorted(daten.items(), key=lambda x: x[1].get("schedule", {}).get("at", "")):
        uhr = aufgabe.get("schedule", {}).get("at", "?")
        marke = "ENTFERNEN" if tid in raus else "bleibt"
        agent = (aufgabe.get("tool_args") or {}).get("name", "")
        print(f"  {uhr}  {aufgabe['name'][:30]:32s} {marke:10s} {agent}")

    print(f"\n{len(raus)} von {len(daten)} Aufgaben betroffen.")
    if not args.apply:
        print("Trockenlauf. Mit --apply entfernen.")
        return 0

    kopie = datei.with_suffix(".json.vor-entfernung")
    shutil.copy2(datei, kopie)
    for tid in raus:
        daten.pop(tid, None)
    datei.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Entfernt: {len(raus)}. Verbleibend: {len(daten)}.")
    print(f"Sicherungskopie: {kopie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
