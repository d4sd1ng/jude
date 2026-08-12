#!/usr/bin/env python3
"""Legt den Dienstplan des Agenten-Teams an — wer wann was tut.

Die Agenten konnten bisher nur auf Zuruf arbeiten: es gab niemanden, der sie
startet. Dieses Skript trägt sie in Judes Scheduler ein, der die Aufgaben
zeitgesteuert ausführt und dabei ``delegate_to_agent`` aufruft.

Wie bei ``notion_migrate.py`` gilt: **Trockenlauf ist Standard**, ausgeführt
wird erst mit ``--apply``. Bestehende Einträge werden am Namen erkannt und
nicht doppelt angelegt; ``--force`` ersetzt sie.

Aufruf aus ``AGENT/``::

    ../.venv/bin/python scripts/team_dispatch.py            # zeigt den Plan
    ../.venv/bin/python scripts/team_dispatch.py --apply    # trägt ihn ein
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

#: Der Dienstplan. ``at`` ist die tägliche Uhrzeit (Ortszeit des Rechners).
#: Die Reihenfolge über den Tag ist bewusst: erst sammeln, dann verwerten.
DIENSTPLAN = [
    {
        "name": "Tech-Themen sammeln",
        "at": "07:00",
        "agent": "scraper",
        "auftrag": (
            "Suche die wichtigsten Tech-Meldungen der letzten 24 Stunden zu Tesla, Nvidia, AMD, "
            "Google, Microsoft und OpenAI. Pruefe jede auf Substanz und lege die drei bis fuenf "
            "verwertbarsten als Dokument ab: Quelle, Datum, Kernaussage. Halte in einer Notiz fest, "
            "welche Meldungen du erfasst hast, damit du sie morgen nicht doppelt bearbeitest."
        ),
    },
    {
        "name": "Leads pruefen",
        "at": "08:00",
        "agent": "leadmanager",
        "auftrag": (
            "Pruefe in Notion, welche Versandpunkte heute faellig sind und welche Leads seit ueber "
            "sieben Tagen keinen Fortschritt hatten. Sieh im Postfach nach Antworten; bei Antwort "
            "oder Absage die laufende Sequenz stoppen und den Lead-Status nachziehen. Fasse zusammen, "
            "wer heute welche Mail bekommt."
        ),
    },
    {
        "name": "Social-Beitraege entwerfen",
        "at": "09:30",
        "agent": "social",
        "auftrag": (
            "Nimm die Themen, die der scraper heute abgelegt hat, und entwirf daraus je einen "
            "Beitrag fuer LinkedIn und Instagram in der Notion-Datenbank 'social_posts'. Status "
            "'Idee'. Beschreibe im Motiv-Briefing, was das Bild zeigen soll, und beauftrage den "
            "designer damit. Veroeffentliche nichts."
        ),
    },
    {
        "name": "Langformat planen",
        "at": "10:30",
        "agent": "content",
        "auftrag": (
            "Sieh dir die gesammelten Tech-Themen der Woche an und schlage ein langes Format vor "
            "(Blog-Artikel oder Whitepaper) fuer Nurovelle. Lege es in 'content_stuecke' mit Status "
            "'Idee' an: Titel, Typ, Kanal, Themen, Beschreibung. Nur eines pro Lauf."
        ),
    },
    {
        "name": "Akquise-Recherche",
        "at": "11:00",
        "agent": "outreach",
        "auftrag": (
            "Recherchiere fuenf neue Einzelunternehmer oder kleine Firmen als Prospects. Pruefe "
            "gegen deine Notizen, ob du sie schon kennst. Lege neue Treffer in der Notion-Datenbank "
            "'kontakte' an (Name, Unternehmen, Branche, Lead-Quelle, Status 'Neu') und halte in "
            "einer Notiz fest, was du erfasst hast."
        ),
    },
]


def kopf(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Dienstplan des Agenten-Teams einrichten.")
    parser.add_argument("--apply", action="store_true", help="Aufgaben wirklich anlegen")
    parser.add_argument("--force", action="store_true", help="bestehende gleichnamige Aufgaben ersetzen")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from main import build_application

    agent, _ = build_application()
    scheduler = agent.scheduler
    vorhanden = {task["name"]: task for task in scheduler.list()}

    print("Dienstplan  —  " + ("SCHREIBMODUS" if args.apply else "Trockenlauf"))
    kopf("Geplant")
    angelegt = ersetzt = uebersprungen = 0

    for eintrag in DIENSTPLAN:
        marke = f"{eintrag['at']}  {eintrag['name']:26s} -> {eintrag['agent']}"
        alt = vorhanden.get(eintrag["name"])
        if alt and not args.force:
            print(f"  = {marke}   (existiert bereits)")
            uebersprungen += 1
            continue
        print(f"  {'~' if alt else '+'} {marke}")
        if not args.apply:
            continue
        if alt:
            scheduler.delete(alt["id"])
            ersetzt += 1
        else:
            angelegt += 1
        scheduler.create(
            eintrag["name"], "tool", at=eintrag["at"],
            tool="delegate_to_agent",
            tool_args={"name": eintrag["agent"], "task": eintrag["auftrag"]},
            speak=False,   # laeuft im Hintergrund, soll nicht dazwischenreden
        )

    kopf("Ergebnis")
    if not args.apply:
        print(f"  Trockenlauf: {len(DIENSTPLAN) - uebersprungen} Aufgabe(n) offen, "
              f"{uebersprungen} bereits vorhanden.")
        print("  Mit --apply eintragen (--force ersetzt bestehende).")
    else:
        print(f"  {angelegt} angelegt, {ersetzt} ersetzt, {uebersprungen} unveraendert.")
        print(f"  Aktive Aufgaben insgesamt: {len(scheduler.list())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
