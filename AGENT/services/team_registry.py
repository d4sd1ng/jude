"""Stammdaten der Sub-Agenten (``data/sub_agents.json``): anlegen, lesen,
loeschen. Reiner CRUD-Ausschnitt aus ``services.team.SubAgentService`` -
Teil 3, Stage C des Refactorings (siehe jolly-napping-tulip.md).

``_load``/``_save``/``_key`` behalten ihre Namen mit fuehrendem Unterstrich
absichtlich: ``tools/chef.py`` und die Testsuite rufen sie trotzdem direkt
auf (siehe Explore-Bericht im Plan) - eine Umbenennung waere hier keine
reine Verschiebung mehr, sondern eine Verhaltensaenderung fuer Aufrufer
ausserhalb dieser Datei.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.paths import DATA_DIR

_NAME_RE = re.compile(r"^[A-Za-zÄÖÜäöüß0-9 _-]{2,40}$")


class AgentRegistry:
    def __init__(self, tool_registry):
        #: Das ToolRegistry-Objekt, gegen das ``create``/``available_skills``
        #: pruefen, welche Skills es tatsaechlich gibt - nicht zu verwechseln
        #: mit dieser Klasse selbst (Agenten-Stammdaten statt Werkzeuge).
        self.registry = tool_registry
        self.path = DATA_DIR / "sub_agents.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    def available_skills(self) -> list[str]:
        return sorted(self.registry.tools)

    def list(self) -> list[dict]:
        return sorted(self._load().values(), key=lambda a: a["name"].casefold())

    def get(self, name: str) -> dict | None:
        return self._load().get(self._key(name))

    def create(self, name: str, role: str, skills: list[str], model: str | None = None,
              person: str | None = None, alter: int | None = None) -> dict:
        name = str(name).strip()
        role = str(role).strip()
        if not _NAME_RE.match(name):
            raise ValueError("Name: 2–40 Zeichen, Buchstaben/Zahlen/Leerzeichen/-/_")
        if len(role) < 5:
            raise ValueError("Bitte eine Rollenbeschreibung angeben.")
        skills = list(dict.fromkeys(skills or []))
        unknown = [s for s in skills if s not in self.registry.tools]
        if unknown:
            raise ValueError("Unbekannte Skills: " + ", ".join(unknown))
        data = self._load()
        # Name und Alter geben dem Mitarbeiter eine Identitaet: der Agent stellt
        # sich damit vor und Tino kann ihn ansprechen wie einen Kollegen.
        vorhanden = data.get(self._key(name), {})
        spec = {"name": name, "role": role, "skills": skills, "model": model,
                "person": (person or vorhanden.get("person") or "").strip() or None,
                "alter": int(alter) if alter is not None else vorhanden.get("alter"),
                "created_at": vorhanden.get("created_at") or datetime.now(timezone.utc).isoformat()}
        data[self._key(name)] = spec
        self._save(data)
        return spec

    def delete(self, name: str) -> dict:
        data = self._load()
        if self._key(name) not in data:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        removed = data.pop(self._key(name))
        self._save(data)
        return {"name": removed["name"], "status": "entfernt"}

    def set_pausiert(self, name: str, pausiert: bool) -> dict:
        """Pausiert/entpausiert einen Mitarbeiter - kein Abbruch eines laufenden
        Laufs (Threads lassen sich nicht sauber toeten), nur eine Sperre gegen
        JEDEN neuen Lauf, egal ob manuell, per auftrag_erteilen oder ueber
        team_tagesrunde/revisionen_nacharbeiten. Eigene Methode statt ueber
        ``create()``: die validiert Pflichtfelder und wuerde bei jedem Aufruf
        z.B. das Modell zuruecksetzen, wenn es nicht mitgegeben wird.
        """
        data = self._load()
        key = self._key(name)
        if key not in data:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        data[key]["pausiert"] = bool(pausiert)
        self._save(data)
        return {"name": data[key]["name"], "pausiert": data[key]["pausiert"]}
