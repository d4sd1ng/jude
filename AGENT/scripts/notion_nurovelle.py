#!/usr/bin/env python3
"""Stellt Notion auf Nurovelle um und macht den Lead-Bestand ehrlich.

Fuenf Schritte, jeder einzeln aufrufbar:

1. ``kanal``     Auswahloption "Nurovelle" in ``content_stuecke`` anlegen
2. ``umstellen`` alle Stuecke mit Kanal "Autonova" auf "Nurovelle" setzen
3. ``termine``   Social-Posts mit Termin in der Vergangenheit auf "Idee" zurueck
4. ``leads``     Kontakte ohne E-Mail auf Status "Inaktiv" – sie sind keine Leads
5. ``adresstyp`` Feld ``Adresstyp`` (Entscheider/Sammelpostfach) in ``kontakte``

**Gefahrenstelle Auswahloptionen.** Notion ersetzt beim Schreiben einer
Eigenschaft die komplette Optionsliste. Wer nur die neue Option schickt, loescht
alle anderen – genau so sind hier schon einmal die Plattform-Optionen
verschwunden. Deshalb liest ``option_ergaenzen`` die vorhandenen Optionen und
schickt sie unveraendert mit ``id``, ``name`` und ``color`` wieder mit; erst
dahinter steht die neue. Geloescht wird nirgends, und keine Verknuepfung wird
angefasst.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/notion_nurovelle.py                    # zeigt alles
    ../.venv/bin/python scripts/notion_nurovelle.py --nur leads        # nur ein Schritt
    ../.venv/bin/python scripts/notion_nurovelle.py --apply            # schreibt
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

SCHRITTE = ("kanal", "umstellen", "termine", "leads", "adresstyp")

ALT, NEU = "Autonova", "Nurovelle"


# ------------------------------------------------------------------ Schema

def option_ergaenzen(dienst, datenbank: str, feld: str, neue: str,
                     farbe: str = "green", apply: bool = False) -> bool:
    """Eine Auswahloption anlegen, ohne die vorhandenen zu verlieren."""
    from services.notion_db import API

    antwort = requests.get(f"{API}/databases/{dienst._database_id(datenbank)}",
                           headers=dienst._headers(), timeout=20)
    antwort.raise_for_status()
    eigenschaft = antwort.json()["properties"][feld]
    art = eigenschaft["type"]
    vorhanden = eigenschaft[art]["options"]
    namen = [o["name"] for o in vorhanden]
    print(f"    vorhandene Optionen ({len(vorhanden)}): {namen}")
    if neue in namen:
        print(f"    '{neue}' existiert bereits – nichts zu tun")
        return False
    # Die vorhandenen unveraendert mitschicken, sonst loescht Notion sie.
    optionen = [{"id": o["id"], "name": o["name"], "color": o["color"]} for o in vorhanden]
    optionen.append({"name": neue, "color": farbe})
    print(f"    -> {[o['name'] for o in optionen]}")
    if apply:
        patch = requests.patch(f"{API}/databases/{dienst._database_id(datenbank)}",
                               headers=dienst._headers(),
                               json={"properties": {feld: {art: {"options": optionen}}}},
                               timeout=25)
        patch.raise_for_status()
        danach = [o["name"] for o in patch.json()["properties"][feld][art]["options"]]
        fehlt = [n for n in namen if n not in danach]
        if fehlt:
            raise RuntimeError(f"Optionen verloren gegangen: {fehlt}")
        print(f"    geschrieben, nachher: {danach}")
    return True


def feld_anlegen(dienst, datenbank: str, feld: str, spezifikation: dict,
                 apply: bool = False) -> bool:
    """Eine neue Eigenschaft anlegen. Beruehrt keine bestehende."""
    from services.notion_db import API

    schema = dienst.schema(datenbank, refresh=True)
    if feld in schema["felder"]:
        print(f"    Feld '{feld}' existiert bereits ({schema['felder'][feld]})")
        return False
    print(f"    neues Feld '{feld}': {spezifikation}")
    if apply:
        antwort = requests.patch(f"{API}/databases/{dienst._database_id(datenbank)}",
                                 headers=dienst._headers(),
                                 json={"properties": {feld: spezifikation}}, timeout=25)
        antwort.raise_for_status()
        # Ohne das liest die anschliessende Pruefung in ``update`` noch das alte
        # Schema und weist das gerade angelegte Feld als unbekannt zurueck.
        dienst.schema(datenbank, refresh=True)
        print(f"    angelegt. Felder jetzt: {len(antwort.json()['properties'])}")
    return True


# ----------------------------------------------------------------- Schritte

def schritt_kanal(dienst, apply: bool) -> None:
    print("\n1. Auswahloption 'Nurovelle' in content_stuecke/Kanal")
    option_ergaenzen(dienst, "content_stuecke", "Kanal", NEU, apply=apply)


def schritt_umstellen(dienst, apply: bool) -> None:
    print(f"\n2. Content-Stuecke von '{ALT}' auf '{NEU}'")
    eintraege = dienst.query("content_stuecke", limit=100)["eintraege"]
    betroffen = [e for e in eintraege if e.get("Kanal") == ALT]
    verteilung: dict = {}
    for e in eintraege:
        verteilung[e.get("Kanal")] = verteilung.get(e.get("Kanal"), 0) + 1
    print(f"    Kanal-Verteilung vorher: {verteilung}")
    print(f"    umzustellen: {len(betroffen)}")
    if not apply:
        return
    erledigt, fehler = 0, []
    for eintrag in betroffen:
        try:
            dienst.update("content_stuecke", eintrag["id"], {"Kanal": NEU})
            erledigt += 1
        except Exception as exc:
            fehler.append((eintrag.get("Titel", "?")[:40], str(exc)[:90]))
    print(f"    umgestellt: {erledigt}" + (f", Fehler: {len(fehler)}" if fehler else ""))
    for titel, grund in fehler[:5]:
        print(f"      ! {titel}: {grund}")


def schritt_termine(dienst, apply: bool) -> None:
    print("\n3. Social-Posts mit Termin in der Vergangenheit")
    heute = date.today().isoformat()
    eintraege = dienst.query("social_posts", limit=100)["eintraege"]
    betroffen = [e for e in eintraege
                 if e.get("Status") == "Geplant"
                 and (e.get("Geplantes Datum") or "9999") < heute]
    print(f"    Posts gesamt: {len(eintraege)}, verfallen geplant: {len(betroffen)}")
    for e in betroffen[:5]:
        print(f"      {e.get('Geplantes Datum')}  {str(e.get('Titel'))[:55]}")
    if not apply:
        return
    erledigt = 0
    for eintrag in betroffen:
        try:
            dienst.update("social_posts", eintrag["id"], {"Status": "Idee"})
            erledigt += 1
        except Exception as exc:
            print(f"      ! {str(eintrag.get('Titel'))[:40]}: {str(exc)[:90]}")
    print(f"    zurueckgesetzt: {erledigt}")


def schritt_leads(dienst, apply: bool) -> None:
    print("\n4. Kontakte ohne E-Mail sind keine Leads -> Status 'Inaktiv'")
    eintraege = dienst.query("kontakte", limit=100)["eintraege"]
    mit = [e for e in eintraege if (e.get("E-Mail") or "").strip()]
    ohne = [e for e in eintraege if not (e.get("E-Mail") or "").strip()
            and e.get("Status") != "Inaktiv"]
    print(f"    Eintraege: {len(eintraege)}  |  echte Leads (mit E-Mail): {len(mit)}")
    print(f"    auf 'Inaktiv' zu setzen: {len(ohne)}")
    if not apply:
        return
    erledigt = 0
    for eintrag in ohne:
        try:
            dienst.update("kontakte", eintrag["id"], {"Status": "Inaktiv"})
            erledigt += 1
        except Exception as exc:
            print(f"      ! {str(eintrag.get('Name'))[:40]}: {str(exc)[:90]}")
    print(f"    gesetzt: {erledigt}")


def schritt_adresstyp(dienst, apply: bool) -> None:
    print("\n5. Feld 'Adresstyp' in kontakte")
    neu = feld_anlegen(dienst, "kontakte", "Adresstyp", {"select": {"options": [
        {"name": "Entscheider", "color": "green"},
        {"name": "Sammelpostfach", "color": "orange"}]}}, apply=apply)
    if not apply and neu:
        print("    (Setzen der vorhandenen Adressen erst nach dem Anlegen)")
        return
    eintraege = dienst.query("kontakte", limit=100)["eintraege"]
    mit = [e for e in eintraege if (e.get("E-Mail") or "").strip()]
    sammel = ("info@", "kontakt@", "praxis@", "office@", "mail@", "buero@", "post@")
    print(f"    Adressen einzuordnen: {len(mit)}")
    for eintrag in mit:
        adresse = (eintrag.get("E-Mail") or "").strip().lower()
        typ = "Sammelpostfach" if adresse.startswith(sammel) else "Entscheider"
        print(f"      {adresse:38s} -> {typ}")
        if apply and eintrag.get("Adresstyp") != typ:
            try:
                dienst.update("kontakte", eintrag["id"], {"Adresstyp": typ})
            except Exception as exc:
                print(f"      ! {str(exc)[:90]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Notion auf Nurovelle umstellen.")
    parser.add_argument("--apply", action="store_true", help="wirklich schreiben")
    parser.add_argument("--nur", choices=SCHRITTE, action="append",
                        help="nur diese Schritte (mehrfach moeglich)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from services.notion_db import NotionDatabaseService

    dienst = NotionDatabaseService()
    if not dienst.available():
        print("NOTION_API_KEY fehlt.")
        return 1

    gewaehlt = args.nur or list(SCHRITTE)
    print("Notion-Umstellung  —  " + ("SCHREIBMODUS" if args.apply else "Trockenlauf"))
    print(f"Schritte: {', '.join(gewaehlt)}")

    funktionen = {"kanal": schritt_kanal, "umstellen": schritt_umstellen,
                  "termine": schritt_termine, "leads": schritt_leads,
                  "adresstyp": schritt_adresstyp}
    for name in SCHRITTE:
        if name in gewaehlt:
            funktionen[name](dienst, args.apply)
            dienst._schema_cache.pop("content_stuecke", None)
            dienst._schema_cache.pop("kontakte", None)

    if not args.apply:
        print("\nTrockenlauf. Mit --apply schreiben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
