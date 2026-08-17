"""Rezepte aus der Notion-Datenbank „Küchenmanagement".

Die eigentliche Rezeptsammlung liegt bei Tino in Notion, nicht im Code. Der
kuratierte Fallback in :mod:`services.meals` kennt nur Namen und Zutaten – die
Zubereitung, die Airfryer-Variante und die Rotationssperre stehen ausschließlich
hier.

Benötigt ``NOTION_API_KEY`` und ``NOTION_DB_RECIPES`` in ``AGENT/.env``. Fehlt
eines davon, meldet der Service sich als nicht eingerichtet, statt zu werfen –
das Cockpit zeigt dann einen Hinweis statt einer Fehlermeldung.
"""

from __future__ import annotations

import os
from datetime import date

import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _plain(prop: dict) -> str:
    """Notion liefert Text als Liste von Rich-Text-Fragmenten."""
    parts = prop.get("rich_text") or prop.get("title") or []
    return "".join(part.get("plain_text", "") for part in parts).strip()


class NotionRecipeService:
    def __init__(self):
        self.key = os.getenv("NOTION_API_KEY", "").strip().strip('"')
        self.database = os.getenv("NOTION_DB_RECIPES", "").strip().strip('"')
        self._cache: list[dict] | None = None

    def available(self) -> bool:
        return bool(self.key and self.database)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Notion-Version": VERSION,
                "Content-Type": "application/json"}

    @staticmethod
    def _recipe(page: dict) -> dict:
        props = page.get("properties", {})

        def number(name):
            return (props.get(name) or {}).get("number")

        def select(name):
            option = (props.get(name) or {}).get("select")
            return option.get("name") if option else None

        def checkbox(name):
            return bool((props.get(name) or {}).get("checkbox"))

        devices = [item.get("name") for item in (props.get("Geraete") or {}).get("multi_select", [])]
        return {
            "id": page.get("id"),
            "name": _plain(props.get("Rezept", {})),
            "ingredients": _plain(props.get("Zutaten Text", {})),
            "preparation": _plain(props.get("Zubereitung Text", {})),
            "airfryer": _plain(props.get("Airfryer Text", {})),
            "note": _plain(props.get("Einkaufshinweis", {})),
            "portions": number("Portionen"),
            "minutes": number("Zeit Minuten"),
            "lock_days": number("Rotationssperre Tage"),
            "category": select("Kategorie"),
            "protein": select("Hauptprotein"),
            "devices": devices,
            "freezable": checkbox("Einfrierbar"),
            "butcher": checkbox("Metzger relevant"),
        }

    def recipes(self, refresh: bool = False) -> list[dict]:
        """Alle Rezepte. Notion deckelt bei 100 Einträgen je Seite, deshalb wird
        über ``next_cursor`` durchgeblättert."""
        if self._cache is not None and not refresh:
            return self._cache
        if not self.available():
            return []
        collected, cursor = [], None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = requests.post(f"{API}/databases/{self.database}/query",
                                     headers=self._headers(), json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            collected += [self._recipe(page) for page in data.get("results", [])]
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        self._cache = [item for item in collected if item["name"]]
        return self._cache

    def today(self, day: date | None = None) -> dict | None:
        """Rezept des Tages. Die Auswahl ist über den Tag deterministisch, damit
        das Cockpit bei jedem Poll dasselbe Gericht zeigt und nicht springt."""
        items = self.recipes()
        if not items:
            return None
        current = day or date.today()
        return items[current.toordinal() % len(items)]
