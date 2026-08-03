"""Benannte Sub-Agenten ("Mitarbeiter") mit eigener Rolle und Werkzeug-Auswahl.

Jude kann spezialisierte Sub-Agenten anlegen (Name, Rolle, erlaubte Skills),
sie wie Mitarbeiter mit Aufgaben betrauen und wieder entfernen. Jeder Sub-Agent
bekommt eine eigene, eingeschränkte Werkzeugliste; sicherheitsrelevante Aktionen
laufen weiterhin über dieselbe Bestätigungs-Warteschlange.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from core.paths import DATA_DIR

_NAME_RE = re.compile(r"^[A-Za-zÄÖÜäöüß0-9 _-]{2,40}$")


class SubAgentService:
    def __init__(self, registry, router):
        self.registry = registry
        self.router = router
        self.path = DATA_DIR / "sub_agents.json"

    # ------------------------------------------------------------ Speicher

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

    # ---------------------------------------------------------------- API

    def available_skills(self) -> list[str]:
        return sorted(self.registry.tools)

    def list(self) -> list[dict]:
        return sorted(self._load().values(), key=lambda a: a["name"].casefold())

    def get(self, name: str) -> dict | None:
        return self._load().get(self._key(name))

    def create(self, name: str, role: str, skills: list[str], model: str | None = None) -> dict:
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
        spec = {"name": name, "role": role, "skills": skills, "model": model,
                "created_at": datetime.now(timezone.utc).isoformat()}
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

    def _build_agent(self, spec: dict):
        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        sub = ToolRegistry()
        sub.set_confirmations(self.registry.confirmations)
        for skill in spec["skills"]:
            tool = self.registry.tools.get(skill)
            if tool is not None:
                sub.register(tool)
        prompt = (f"Du bist {spec['name']}, ein spezialisierter Sub-Agent von Jude. "
                  f"Deine Rolle: {spec['role']}. Nutze ausschließlich deine zugewiesenen Werkzeuge, "
                  f"bleibe bei deiner Aufgabe und antworte knapp und umsetzbar.")
        return Agent(self.router, sub, system_prompt=prompt)

    def run(self, name: str, task: str) -> dict:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        task = str(task).strip()
        if not task:
            raise ValueError("Es wurde keine Aufgabe angegeben.")
        agent = self._build_agent(spec)
        answer = agent.process_input(task)
        return {"agent": spec["name"], "role": spec["role"], "skills": spec["skills"],
                "answer": answer, "model": agent.last_model}
