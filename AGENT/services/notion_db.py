"""Generischer Zugriff auf die in ``AGENT/.env`` hinterlegten Notion-Datenbanken.

Die Schlüssel lagen längst vollständig vor (Kontakte, Sequenzen, E-Mail-Inhalte,
Content-Stücke, Scheduling, Rezepte) – es fehlten schlicht die Werkzeuge, um sie
zu benutzen. Statt für jede Datenbank eigenen Code zu schreiben, arbeitet dieser
Dienst schemagetrieben: er liest die Feldliste live aus Notion und wandelt
Werte anhand des dort hinterlegten Typs um. Neue Datenbanken brauchen damit nur
einen Eintrag in ``DATABASES``.
"""

from __future__ import annotations

import os
from typing import Any

import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

#: Kurzname -> Umgebungsvariable. Der Kurzname ist das, was die Agenten angeben.
DATABASES = {
    "kontakte": "NOTION_DB_CONTACTS",
    "sequenzen": "NOTION_DB_SEQUENCES",
    "mail_inhalte": "NOTION_DB_CONTENT",
    "content_stuecke": "NOTION_DB_CONTENT_PIECES",
    "scheduling": "NOTION_DB_SCHEDULING",
    "social_posts": "NOTION_DB_SOCIAL",
    "abonnenten": "NOTION_DB_SUBSCRIBERS",
    "rezepte": "NOTION_DB_RECIPES",
    "essensplan": "NOTION_DB_MEALPLAN",
}


class NotionDatabaseService:
    def __init__(self):
        self.key = os.getenv("NOTION_API_KEY", "").strip().strip('"')
        self._schema_cache: dict[str, dict] = {}

    # ------------------------------------------------------------- Grundlagen

    def available(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        if not self.key:
            raise RuntimeError("NOTION_API_KEY fehlt in AGENT/.env.")
        return {"Authorization": f"Bearer {self.key}", "Notion-Version": VERSION,
                "Content-Type": "application/json"}

    @staticmethod
    def _database_id(name: str) -> str:
        variable = DATABASES.get(name.strip().lower())
        if variable is None:
            raise ValueError(f"Unbekannte Datenbank {name!r}. Bekannt: {', '.join(sorted(DATABASES))}.")
        value = os.getenv(variable, "").strip().strip('"')
        if not value:
            raise RuntimeError(f"{variable} ist in AGENT/.env nicht gesetzt.")
        return value

    def databases(self) -> list[dict]:
        """Welche Datenbanken eingerichtet sind – ohne Netzzugriff."""
        return [{"name": name, "konfiguriert": bool(os.getenv(variable, "").strip().strip('"'))}
                for name, variable in sorted(DATABASES.items())]

    def schema(self, name: str, refresh: bool = False) -> dict:
        """Feldnamen und -typen. Ohne die schreibt ein Agent ins Leere."""
        if name in self._schema_cache and not refresh:
            return self._schema_cache[name]
        response = requests.get(f"{API}/databases/{self._database_id(name)}",
                                headers=self._headers(), timeout=20)
        response.raise_for_status()
        data = response.json()
        fields, options = {}, {}
        for key, value in data.get("properties", {}).items():
            kind = value.get("type")
            fields[key] = kind
            # Auswahlfelder mit ihren gueltigen Werten ausliefern: schreibt ein
            # Agent einen unbekannten Wert, legt Notion ihn stillschweigend als
            # neue Option an und die Listen verwildern.
            if kind in ("select", "status", "multi_select"):
                spec = value.get(kind) or {}
                choices = [item.get("name") for item in spec.get("options", [])]
                if choices:
                    options[key] = choices
        title = "".join(part.get("plain_text", "") for part in data.get("title", []))
        self._schema_cache[name] = {"name": name, "titel": title, "felder": fields,
                                    "erlaubte_werte": options}
        return self._schema_cache[name]

    # ------------------------------------------------------------ Umwandlung

    @staticmethod
    def _plain(prop: dict) -> Any:
        """Notion-Eigenschaft -> einfacher Python-Wert."""
        kind = prop.get("type")
        value = prop.get(kind)
        if kind in ("title", "rich_text"):
            return "".join(part.get("plain_text", "") for part in value or []).strip()
        if kind == "select":
            return (value or {}).get("name")
        if kind == "multi_select":
            return [item.get("name") for item in value or []]
        if kind == "status":
            return (value or {}).get("name")
        if kind == "date":
            return (value or {}).get("start")
        if kind in ("people", "relation"):
            return [item.get("id") for item in value or []]
        if kind == "formula":
            inner = value or {}
            return inner.get(inner.get("type"))
        return value

    @staticmethod
    def _to_property(kind: str, value: Any) -> dict:
        """Einfacher Wert -> Notion-Eigenschaft, passend zum Feldtyp."""
        if kind == "title":
            return {"title": [{"text": {"content": str(value)[:2000]}}]}
        if kind == "rich_text":
            return {"rich_text": [{"text": {"content": str(value)[:2000]}}]}
        if kind == "number":
            return {"number": float(value)}
        if kind == "checkbox":
            return {"checkbox": bool(value)}
        if kind in ("select", "status"):
            return {kind: {"name": str(value)}}
        if kind == "multi_select":
            items = value if isinstance(value, list) else [value]
            return {"multi_select": [{"name": str(item)} for item in items]}
        if kind == "date":
            return {"date": {"start": str(value)}}
        if kind == "url":
            return {"url": str(value)}
        if kind == "email":
            return {"email": str(value)}
        if kind == "phone_number":
            return {"phone_number": str(value)}
        if kind == "relation":
            items = value if isinstance(value, list) else [value]
            return {"relation": [{"id": str(item)} for item in items]}
        raise ValueError(f"Feldtyp {kind!r} kann nicht geschrieben werden.")

    # ------------------------------------------------------------------ API

    def query(self, name: str, limit: int = 25, search: str | None = None) -> dict:
        """Einträge lesen. ``search`` filtert nachträglich über alle Textfelder,
        weil Notion-Filter je Feldtyp unterschiedlich aufgebaut sein müssten."""
        payload: dict = {"page_size": max(1, min(int(limit), 100))}
        response = requests.post(f"{API}/databases/{self._database_id(name)}/query",
                                 headers=self._headers(), json=payload, timeout=25)
        response.raise_for_status()
        rows = []
        for page in response.json().get("results", []):
            entry = {"id": page.get("id"), "url": page.get("url")}
            for field, prop in page.get("properties", {}).items():
                entry[field] = self._plain(prop)
            rows.append(entry)
        if search:
            needle = search.casefold()
            rows = [row for row in rows
                    if any(needle in str(value).casefold() for value in row.values())]
        return {"datenbank": name, "anzahl": len(rows), "eintraege": rows}

    def _validate(self, name: str, fields: dict) -> dict:
        """Feldnamen und Auswahlwerte prüfen, bevor etwas geschrieben wird.

        Notion legt unbekannte Auswahlwerte stillschweigend als neue Option an –
        nach ein paar Agentenläufen wären die Listen unbrauchbar. Deshalb wird
        hier hart abgewiesen statt stillschweigend erweitert.
        """
        schema = self.schema(name)
        felder, erlaubt = schema["felder"], schema["erlaubte_werte"]
        unknown = [key for key in fields if key not in felder]
        if unknown:
            raise ValueError(f"Unbekannte Felder {unknown}. Vorhanden: {sorted(felder)}.")
        for key, value in fields.items():
            if key not in erlaubt or value in (None, ""):
                continue
            werte = value if isinstance(value, list) else [value]
            falsch = [str(item) for item in werte if str(item) not in erlaubt[key]]
            if falsch:
                raise ValueError(f"Feld {key!r}: {falsch} ist keine vorhandene Option. "
                                 f"Erlaubt: {erlaubt[key]}.")
        return felder

    def create(self, name: str, fields: dict) -> dict:
        schema = self._validate(name, fields)
        properties = {key: self._to_property(schema[key], value) for key, value in fields.items()}
        response = requests.post(f"{API}/pages", headers=self._headers(),
                                 json={"parent": {"database_id": self._database_id(name)},
                                       "properties": properties}, timeout=25)
        response.raise_for_status()
        page = response.json()
        return {"angelegt": True, "id": page.get("id"), "url": page.get("url"), "datenbank": name}

    def update(self, name: str, page_id: str, fields: dict) -> dict:
        schema = self._validate(name, fields)
        properties = {key: self._to_property(schema[key], value) for key, value in fields.items()}
        response = requests.patch(f"{API}/pages/{page_id}", headers=self._headers(),
                                  json={"properties": properties}, timeout=25)
        response.raise_for_status()
        return {"aktualisiert": True, "id": page_id, "datenbank": name}
