#!/usr/bin/env python3
"""Idempotente Schema-Migration für die Nurovelle-Notion-Datenbanken.

Hintergrund: Notion-Schemaänderungen wurden bisher als Einmal-Befehle gefahren.
Damit war nicht nachvollziehbar, was wann warum geändert wurde, und ein zweiter
Lauf hätte Felder doppelt angelegt. Dieses Skript ist die nachvollziehbare
Alternative — es liegt im Repository, protokolliert jeden Schritt und lässt sich
beliebig oft ausführen.

Arbeitsweise:

* **Trockenlauf ist Standard.** Ohne ``--apply`` wird nur angezeigt, was fehlt.
  Das entspricht der Projektregel „Notion-Schemata werden nicht ohne Freigabe
  geändert" (``Nurtoring-Email/current_state.md``).
* **Rein additiv.** Es werden ausschließlich fehlende Felder und fehlende
  Auswahloptionen ergänzt. Bestehende Felder, Optionen und Relationen bleiben
  unangetastet — sonst brechen die Verknüpfungen.
* **Umbenennen kann das Skript nicht.** Die Notion-API quittiert das Umbenennen
  bestehender Auswahloptionen mit HTTP 200, wendet es aber nicht an. Solche
  Fälle werden als Handarbeit ausgewiesen (siehe ``MANUELL``).

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/notion_migrate.py            # zeigt nur an
    ../.venv/bin/python scripts/notion_migrate.py --apply    # führt aus
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

#: Gewünschter Sollzustand je Datenbank (Kurzname -> Umgebungsvariable).
#: ``felder`` werden angelegt, wenn sie fehlen; ``optionen`` werden an
#: bestehende Auswahlfelder angehängt, wenn sie noch nicht vorhanden sind.
SOLLZUSTAND = {
    "kontakte": {
        "env": "NOTION_DB_CONTACTS",
        "zweck": "Prospects aus den Akquise-Trackern (company/outreach)",
        "felder": {
            # Die Tracker fuehren Angaben, fuer die es in Notion keine Felder gab.
            # Rein additiv - bestehende Eintraege und Relationen bleiben unberuehrt.
            "Website": {"url": {}},
            "Ort": {"rich_text": {}},
            "PLZ": {"rich_text": {}},
            "Groesse": {"rich_text": {}},
            "Entscheider": {"rich_text": {}},
            "Passende Module": {"rich_text": {}},
            "Entfernung km": {"number": {}},
            "Geprueft": {"checkbox": {}},
        },
        "optionen": {
            # Die Tracker recherchieren ueber Kartendienste und Impressen.
            "Lead-Quelle": ["Google Maps", "Impressum", "Recherche"],
            "Branche": ["Pflege", "Handel", "Gastronomie", "Bildung"],
        },
    },
    "social_posts": {
        "env": "NOTION_DB_SOCIAL",
        "zweck": "Kurze Plattform-Beiträge (bestehende DB '📱 Social Media Posting')",
        "felder": {},
        "optionen": {
            # Facebook und TikTok fehlten in der ursprünglichen Anlage von Mai.
            "Plattform": ["Facebook", "TikTok"],
        },
    },
}

#: Rückbau: Felder und Auswahloptionen, die versehentlich angelegt wurden.
#: Wird nur ausgeführt, wenn das jeweilige Feld in *allen* Einträgen leer ist –
#: das Skript prüft das selbst und bricht sonst ab. Notion kennt kein
#: Rückgängig für gelöschte Felder.
RUECKBAU = {
    "content_stuecke": {
        "env": "NOTION_DB_CONTENT_PIECES",
        "grund": ("Social-Posts gehören in die Datenbank '📱 Social Media Posting', "
                  "nicht in die Content-Stücke. Die Felder wurden irrtümlich hier angelegt."),
        "felder": ["Post-Text", "Hook", "Hashtags", "Motiv-Briefing", "Motiv-URL",
                   "Quelle", "Veroeffentlichungs-Zeit"],
        # Bewusst KEIN Rückbau von Auswahloptionen: das Entfernen von Optionen
        # war nie beauftragt und ist zu riskant – Notion ersetzt die Liste
        # vollständig, ein Fehler dabei trifft sofort bestehende Einträge.
    },
}

#: Was die API nicht kann und deshalb in Notion selbst erledigt werden muss.
MANUELL = [
    ("content_stuecke", "Kanal",
     "Option 'Autonova' in 'Nurovelle' umbenennen. Über die API nicht möglich "
     "(HTTP 200 ohne Wirkung). In Notion umbenennen statt neu anlegen – dabei "
     "behält Notion die interne ID, alle bestehenden Einträge bleiben verknüpft."),
]


def kopf(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


class Migration:
    def __init__(self, apply: bool):
        self.apply = apply
        self.key = os.getenv("NOTION_API_KEY", "").strip().strip('"')
        if not self.key:
            raise SystemExit("NOTION_API_KEY fehlt in AGENT/.env.")
        self.aenderungen = 0

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Notion-Version": VERSION,
                "Content-Type": "application/json"}

    def _laden(self, database_id: str) -> dict:
        response = requests.get(f"{API}/databases/{database_id}", headers=self._headers, timeout=25)
        response.raise_for_status()
        return response.json()

    def _schreiben(self, database_id: str, properties: dict) -> None:
        response = requests.patch(f"{API}/databases/{database_id}", headers=self._headers,
                                  json={"properties": properties}, timeout=25)
        response.raise_for_status()

    def _alle_seiten(self, database_id: str) -> list[dict]:
        seiten, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            response = requests.post(f"{API}/databases/{database_id}/query",
                                     headers=self._headers, json=body, timeout=30)
            response.raise_for_status()
            daten = response.json()
            seiten += daten.get("results", [])
            if not daten.get("has_more"):
                return seiten
            cursor = daten.get("next_cursor")

    @staticmethod
    def _belegt(prop: dict) -> bool:
        kind = prop.get("type")
        value = prop.get(kind)
        if kind in ("title", "rich_text"):
            return bool("".join(part.get("plain_text", "") for part in value or []).strip())
        if kind in ("select", "status"):
            return bool((value or {}).get("name"))
        if kind == "multi_select":
            return bool(value)
        return value not in (None, "", [], {})

    def rueckbau(self, name: str, spec: dict) -> None:
        database_id = os.getenv(spec["env"], "").strip().strip('"')
        kopf(f"Rückbau: {name}")
        print(f"  Grund: {spec['grund']}")
        if not database_id:
            print(f"  übersprungen: {spec['env']} ist nicht gesetzt")
            return
        vorhanden = self._laden(database_id).get("properties", {})
        zu_loeschen = [feld for feld in spec.get("felder", []) if feld in vorhanden]
        offene_optionen = {}
        for feld, werte in spec.get("optionen", {}).items():
            if feld not in vorhanden:
                continue
            typ = vorhanden[feld].get("type")
            bestehend = vorhanden[feld].get(typ, {}).get("options", [])
            treffer = [option for option in bestehend if option["name"] in werte]
            if treffer:
                offene_optionen[feld] = (typ, bestehend, treffer)
        if not zu_loeschen and not offene_optionen:
            print("  nichts zurückzubauen")
            return

        seiten = self._alle_seiten(database_id)
        print(f"  {len(seiten)} Einträge geprüft")
        aenderung: dict = {}

        for feld in zu_loeschen:
            belegt = sum(1 for seite in seiten if self._belegt(seite["properties"].get(feld, {})))
            if belegt:
                print(f"  ! {feld}: in {belegt} Einträgen belegt – wird NICHT gelöscht")
                continue
            print(f"  - {feld}  (leer)")
            aenderung[feld] = None          # None entfernt das Feld

        for feld, (typ, bestehend, treffer) in offene_optionen.items():
            benutzt = []
            for option in treffer:
                anzahl = sum(1 for seite in seiten
                             if (seite["properties"].get(feld, {}).get(typ) or {}).get("name") == option["name"])
                if anzahl:
                    benutzt.append(f"{option['name']} ({anzahl}x)")
            if benutzt:
                print(f"  ! {feld}: {benutzt} in Verwendung – Optionen bleiben")
                continue
            namen = [option["name"] for option in treffer]
            print(f"  - {feld}: {namen}  (unbenutzt)")
            behalten = [{"id": option["id"]} for option in bestehend if option not in treffer]
            aenderung[feld] = {typ: {"options": behalten}}

        if not aenderung:
            print("  nichts zu tun")
            return
        self.aenderungen += len(aenderung)
        if not self.apply:
            print(f"  → Trockenlauf: {len(aenderung)} Rückbau-Schritt(e)")
            return
        self._schreiben(database_id, aenderung)
        print(f"  → {len(aenderung)} Rückbau-Schritt(e) ausgeführt")

    def datenbank(self, name: str, spec: dict) -> None:
        database_id = os.getenv(spec["env"], "").strip().strip('"')
        kopf(f"{name}  ({spec['zweck']})")
        if not database_id:
            print(f"  übersprungen: {spec['env']} ist nicht gesetzt")
            return
        daten = self._laden(database_id)
        titel = "".join(part.get("plain_text", "") for part in daten.get("title", []))
        vorhanden = daten.get("properties", {})
        print(f"  {titel}  ({len(vorhanden)} Felder)")

        aenderung: dict = {}

        for feld, definition in spec.get("felder", {}).items():
            if feld in vorhanden:
                print(f"  = {feld}")
            else:
                print(f"  + {feld}  (fehlt)")
                aenderung[feld] = definition

        for feld, gewuenscht in spec.get("optionen", {}).items():
            if feld not in vorhanden:
                print(f"  ! {feld}: Feld existiert nicht – Optionen übersprungen")
                continue
            typ = vorhanden[feld].get("type")
            bestehend = vorhanden[feld].get(typ, {}).get("options", [])
            namen = [option["name"] for option in bestehend]
            fehlend = [wert for wert in gewuenscht if wert not in namen]
            if not fehlend:
                print(f"  = {feld}: alle Optionen vorhanden")
                continue
            print(f"  + {feld}: {fehlend}")
            # Notion ersetzt bei einem PATCH die gesamte Optionsliste. Bestehende
            # Optionen müssen deshalb VOLLSTÄNDIG zurückgeschickt werden – id,
            # name und color. Nur die id zu senden liess sie verschwinden
            # (YouTube, Twitter/X und Instagram gingen so einmal verloren).
            neu = [{"id": option["id"], "name": option["name"],
                    "color": option.get("color", "default")} for option in bestehend]
            neu += [{"name": wert} for wert in fehlend]
            aenderung[feld] = {typ: {"options": neu}}

        if not aenderung:
            print("  nichts zu tun")
            return
        self.aenderungen += len(aenderung)
        if not self.apply:
            print(f"  → Trockenlauf: {len(aenderung)} Änderung(en) würden geschrieben")
            return
        self._schreiben(database_id, aenderung)
        print(f"  → {len(aenderung)} Änderung(en) geschrieben")


def main() -> int:
    parser = argparse.ArgumentParser(description="Notion-Schema abgleichen (additiv, idempotent).")
    parser.add_argument("--apply", action="store_true",
                        help="Änderungen wirklich schreiben (ohne dieses Flag nur anzeigen)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    migration = Migration(args.apply)

    print("Notion-Schema-Abgleich  —  " + ("SCHREIBMODUS" if args.apply else "Trockenlauf"))
    for name, spec in RUECKBAU.items():
        migration.rueckbau(name, spec)
    for name, spec in SOLLZUSTAND.items():
        migration.datenbank(name, spec)

    if MANUELL:
        kopf("Von Hand in Notion zu erledigen")
        for datenbank, feld, hinweis in MANUELL:
            print(f"  {datenbank}.{feld}: {hinweis}")

    kopf("Ergebnis")
    if migration.aenderungen == 0:
        print("  Schema ist auf Stand.")
    elif args.apply:
        print(f"  {migration.aenderungen} Änderung(en) angewendet.")
    else:
        print(f"  {migration.aenderungen} Änderung(en) offen — mit --apply ausführen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
