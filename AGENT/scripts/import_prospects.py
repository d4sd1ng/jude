#!/usr/bin/env python3
"""Übernimmt die Akquise-Tracker aus ``company/outreach`` nach Notion.

Dort liegt geprüfte Handarbeit: 64 Firmen mit Website, E-Mail aus dem Impressum,
Entscheider, Entfernung und passenden Modulen. In der Notion-Datenbank
``kontakte`` standen dagegen zwei Einträge — die Agenten hatten also keine
Arbeitsgrundlage.

Eigenschaften:

* **Trockenlauf ist Standard**, geschrieben wird erst mit ``--apply``.
* **Idempotent**: bereits vorhandene Firmen werden am Namen erkannt und
  übersprungen, nie doppelt angelegt.
* **Zusammenführend**: beide Tracker werden gelesen, Dubletten über den
  Firmennamen entfernt (die 29 Überschneidungen).
* Unbekannte Auswahlwerte werden auf vorhandene abgebildet, nie neu erfunden —
  der Notion-Dienst weist sie sonst ohnehin ab.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/import_prospects.py            # zeigt an
    ../.venv/bin/python scripts/import_prospects.py --apply    # schreibt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

QUELLE = Path("/media/d4sd1ng/AI-Data/Projects/company/outreach")
TRACKER = ["outreach_tracker_marburg_100km.csv", "outreach_tracker.csv"]

#: Freitext aus dem Tracker auf die vorhandenen Branchen-Optionen abbilden.
BRANCHE = {
    "pflegedienst": "Pflege", "hauskrankenpflege": "Pflege", "pflege": "Pflege",
    "ambulante pflege": "Pflege", "seniorenbetreuung": "Pflege",
    "handwerk": "Handwerk", "elektro": "Handwerk", "sanitär": "Handwerk",
    "steuerberat": "Finanzen", "kanzlei": "Beratung", "beratung": "Beratung",
    "it": "IT/Tech", "software": "IT/Tech",
    "gastronomie": "Gastronomie", "restaurant": "Gastronomie",
    "handel": "Handel", "shop": "Handel",
    "schule": "Bildung", "bildung": "Bildung",
}


def branche(wert: str) -> str:
    text = (wert or "").casefold()
    for muster, ziel in BRANCHE.items():
        if muster in text:
            return ziel
    return "Sonstige"


def erste_mail(wert: str) -> str | None:
    """Der Tracker führt teils mehrere Adressen mit Semikolon."""
    for teil in (wert or "").replace(",", ";").split(";"):
        teil = teil.strip()
        if "@" in teil and " " not in teil:
            return teil
    return None


def zahl(wert: str):
    try:
        return float(str(wert).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


#: Die vorderen Spalten sitzen fest, die hinteren auch – dazwischen liegt der
#: beschädigte Bereich.
VORNE = ["id", "name", "city", "plz", "website", "phone", "email", "type"]
HINTEN = ["distance_km", "distance_band"]


def zeile_lesen(rohzeile: str) -> dict | None:
    """Liest eine Trackerzeile trotz beschädigter Feldtrennung.

    In den Dateien stehen echte Tabulatoren *innerhalb* von Feldwerten
    (``2-10 MA (geschätzt<TAB>nicht verifiziert)``) – bei einem Export wurden
    Kommas zu Tabulatoren. 47 von 63 Zeilen sind dadurch verschoben; ein
    normaler CSV-Leser ordnet ab Spalte 9 alles falsch zu und hätte 64
    fehlerhafte Datensätze nach Notion geschrieben.

    Deshalb wird von beiden Enden gelesen: die ersten acht Spalten und die
    letzten zwei sind verlässlich, der beschädigte Rest wandert ungeteilt in
    die Notizen. Ein ehrlicher Textblock ist besser als falsch einsortierte
    Felder.
    """
    teile = rohzeile.rstrip("\n").split("\t")
    if len(teile) < len(VORNE) + len(HINTEN):
        return None
    # CSV-Anfuehrungszeichen bereinigen: verdoppelte Zeichen und Umschliessung
    # sind Reste des kaputten Exports, keine echten Namensbestandteile.
    teile = [t.strip().strip('"').replace('""', '"') for t in teile]
    eintrag = dict(zip(VORNE, teile[:len(VORNE)]))
    eintrag.update(zip(HINTEN, teile[-len(HINTEN):]))
    mitte = [t.strip().strip('"') for t in teile[len(VORNE):-len(HINTEN)] if t.strip().strip('"')]
    eintrag["rest"] = ", ".join(mitte)
    return eintrag


def einlesen() -> list[dict]:
    firmen: dict[str, dict] = {}
    for name in TRACKER:
        pfad = QUELLE / name
        if not pfad.is_file():
            print(f"  übersprungen: {pfad} fehlt")
            continue
        with pfad.open(encoding="utf-8") as datei:
            datei.readline()                       # Kopfzeile
            for rohzeile in datei:
                eintrag = zeile_lesen(rohzeile)
                if not eintrag:
                    continue
                firma = (eintrag.get("name") or "").strip()
                if not firma:
                    continue
                # Der erste Tracker gewinnt: die 100-km-Fassung ist die neuere.
                firmen.setdefault(firma.casefold(), eintrag)
    return list(firmen.values())


def nach_notion(zeile: dict) -> dict:
    felder: dict = {
        "Name": (zeile.get("name") or "").strip(),
        "Unternehmen": (zeile.get("name") or "").strip(),
        "Status": "Neu",
        "Branche": branche(zeile.get("type", "")),
        "Lead-Quelle": "Google Maps" if "maps" in (zeile.get("source") or "").casefold() else "Recherche",
        # 'verified' liegt im beschaedigten Mittelteil und laesst sich nicht
        # verlaesslich herausloesen – bleibt bewusst leer statt falsch gesetzt.
    }
    zuordnung = {
        "Website": zeile.get("website"), "Ort": zeile.get("city"), "PLZ": zeile.get("plz"),
        "Groesse": zeile.get("size"), "Entscheider": zeile.get("decision_maker"),
        # Der beschädigte Mittelteil kommt ungeteilt in die Notizen.
        "Notizen": zeile.get("rest"),
    }
    for feld, wert in zuordnung.items():
        wert = (wert or "").strip()
        if wert:
            felder[feld] = wert
    mail = erste_mail(zeile.get("email", ""))
    if mail:
        felder["E-Mail"] = mail
    telefon = (zeile.get("phone") or "").strip()
    if telefon:
        felder["Telefon"] = telefon
    entfernung = zahl(zeile.get("distance_km"))
    if entfernung is not None:
        felder["Entfernung km"] = entfernung
    return felder


def main() -> int:
    parser = argparse.ArgumentParser(description="Akquise-Tracker nach Notion übernehmen.")
    parser.add_argument("--apply", action="store_true", help="wirklich schreiben")
    parser.add_argument("--limit", type=int, default=0, help="nur die ersten N übernehmen")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from services.notion_db import NotionDatabaseService

    dienst = NotionDatabaseService()
    zeilen = einlesen()
    print("Prospect-Übernahme  —  " + ("SCHREIBMODUS" if args.apply else "Trockenlauf"))
    print(f"\nAus den Trackern gelesen: {len(zeilen)} eindeutige Firmen")

    vorhanden = {str(eintrag.get("Name", "")).casefold()
                 for eintrag in dienst.query("kontakte", limit=100)["eintraege"]}
    print(f"In Notion bereits vorhanden: {len(vorhanden)}")

    neu = [z for z in zeilen if (z.get("name") or "").strip().casefold() not in vorhanden]
    if args.limit:
        neu = neu[:args.limit]
    print(f"Zu übernehmen: {len(neu)}\n")

    angelegt, fehler = 0, []
    for zeile in neu:
        felder = nach_notion(zeile)
        marke = f"{felder['Name'][:38]:40s} {felder['Branche']:12s} {felder.get('E-Mail', '—')}"
        if not args.apply:
            print(f"  + {marke}")
            continue
        try:
            dienst.create("kontakte", felder)
            angelegt += 1
            print(f"  + {marke}")
        except Exception as exc:
            fehler.append((felder["Name"], str(exc)[:120]))
            print(f"  ! {felder['Name'][:38]:40s} {str(exc)[:80]}")

    print()
    if not args.apply:
        print(f"Trockenlauf: {len(neu)} Firmen würden angelegt. Mit --apply schreiben.")
    else:
        print(f"Angelegt: {angelegt}" + (f", Fehler: {len(fehler)}" if fehler else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
