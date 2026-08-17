#!/usr/bin/env python3
"""Räumt doppelt angelegte Kontakte auf – archivieren, nicht löschen.

Am 13.08.2026 legte der Akquise-Agent zehn Pflegedienste an, die alle bereits
in der Datenbank standen; eine Firma kam sogar dreimal vor. Der Grund war
keine Böswilligkeit, sondern eine fehlende Vorgabe: seine Rolle verlangte
"recherchiere fuenf neue Firmen", ohne ihn zu einem Abgleich zu verpflichten.
Die Rolle ist inzwischen geändert – dieser Aufräumer beseitigt die Folgen.

Vorgehen:

* Firmen werden über eine normalisierte Namensform zusammengefasst
  (Kleinschreibung, ohne Satz- und Sonderzeichen, erste 26 Zeichen).
* Behalten wird der **inhaltsreichste** Eintrag: gezählt werden gefüllte
  Felder wie E-Mail, Telefon, Website, Entscheider, Entfernung. Bei Gleichstand
  gewinnt der ältere – er trägt meist die Verknüpfungen.
* Die übrigen werden **archiviert** (``archived: true``). In Notion landen sie
  im Papierkorb und lassen sich dort wiederherstellen; gelöscht wird nichts.
* Verknüpfungen werden nicht angefasst.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/notion_dubletten.py            # zeigt an
    ../.venv/bin/python scripts/notion_dubletten.py --apply    # archiviert
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

API = "https://api.notion.com/v1"

#: Woran sich Inhalt bemisst. Je mehr davon gefüllt ist, desto wertvoller.
GEWICHTET = ("E-Mail", "Telefon", "Website", "Entscheider", "Entfernung km",
             "Groesse", "PLZ", "Ort", "Notizen", "Passende Module")


def kopf() -> dict:
    key = os.getenv("NOTION_API_KEY", "").strip().strip('"')
    if not key:
        raise SystemExit("NOTION_API_KEY fehlt in AGENT/.env.")
    return {"Authorization": f"Bearer {key}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def klartext(eigenschaft: dict) -> str:
    art = eigenschaft.get("type")
    wert = eigenschaft.get(art)
    if art in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in wert or []).strip()
    if art in ("select", "status"):
        return (wert or {}).get("name", "")
    if art in ("email", "phone_number", "url"):
        return str(wert or "")
    if art == "number":
        return "" if wert is None else str(wert)
    if art == "relation":
        return ",".join(x.get("id", "") for x in wert or [])
    return ""


def namensform(name: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]+", "", (name or "").casefold())[:26]


def seiten(headers: dict, datenbank: str) -> list[dict]:
    ergebnis, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        antwort = requests.post(f"{API}/databases/{datenbank}/query",
                                headers=headers, json=payload, timeout=25)
        antwort.raise_for_status()
        daten = antwort.json()
        ergebnis.extend(daten["results"])
        if not daten.get("has_more"):
            return ergebnis
        cursor = daten["next_cursor"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Doppelte Kontakte archivieren.")
    parser.add_argument("--apply", action="store_true", help="wirklich archivieren")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    headers = kopf()
    datenbank = os.getenv("NOTION_DB_CONTACTS", "").strip().strip('"')

    alle = seiten(headers, datenbank)
    gruppen: dict[str, list] = defaultdict(list)
    for seite in alle:
        eigenschaften = seite.get("properties", {})
        name = next((klartext(v) for v in eigenschaften.values() if v.get("type") == "title"), "")
        if not namensform(name):
            continue
        inhalt = sum(1 for feld in GEWICHTET if klartext(eigenschaften.get(feld, {})).strip())
        gruppen[namensform(name)].append(
            {"id": seite["id"], "name": name, "erstellt": seite["created_time"],
             "inhalt": inhalt,
             "mail": klartext(eigenschaften.get("E-Mail", {})),
             "status": klartext(eigenschaften.get("Status", {}))})

    doppelt = {k: v for k, v in gruppen.items() if len(v) > 1}
    print("Doppelte Kontakte  —  " + ("ARCHIVIEREN" if args.apply else "Trockenlauf"))
    print(f"\nEinträge gesamt: {len(alle)}  |  doppelt geführte Firmen: {len(doppelt)}\n")
    if not doppelt:
        print("Nichts zu tun.")
        return 0

    weg: list[dict] = []
    for eintraege in doppelt.values():
        # Inhaltsreichster gewinnt, bei Gleichstand der aeltere.
        eintraege.sort(key=lambda e: (-e["inhalt"], e["erstellt"]))
        behalten, rest = eintraege[0], eintraege[1:]
        print(f"  {behalten['name'][:46]}")
        print(f"     BEHALTEN   {behalten['erstellt'][:16].replace('T',' ')}  "
              f"{behalten['inhalt']} Felder gefüllt  {behalten['mail'] or ''}")
        for e in rest:
            print(f"     archivieren {e['erstellt'][:16].replace('T',' ')}  "
                  f"{e['inhalt']} Felder gefüllt  {e['mail'] or ''}")
            weg.append(e)
        print()

    print(f"Zu archivieren: {len(weg)}  |  danach verbleiben: {len(alle) - len(weg)}")
    if not args.apply:
        print("\nTrockenlauf. Mit --apply archivieren (Notion-Papierkorb, wiederherstellbar).")
        return 0

    erledigt, fehler = 0, []
    for eintrag in weg:
        try:
            antwort = requests.patch(f"{API}/pages/{eintrag['id']}", headers=headers,
                                     json={"archived": True}, timeout=25)
            antwort.raise_for_status()
            erledigt += 1
        except Exception as exc:
            fehler.append((eintrag["name"], str(exc)[:90]))
    print(f"\nArchiviert: {erledigt}" + (f", Fehler: {len(fehler)}" if fehler else ""))
    for name, grund in fehler:
        print(f"  ! {name[:40]}: {grund}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
