"""Benannte Sub-Agenten ("Mitarbeiter") mit eigener Rolle und Werkzeug-Auswahl.

Jude kann spezialisierte Sub-Agenten anlegen (Name, Rolle, erlaubte Skills),
sie wie Mitarbeiter mit Aufgaben betrauen und wieder entfernen. Jeder Sub-Agent
bekommt eine eigene, eingeschränkte Werkzeugliste; sicherheitsrelevante Aktionen
laufen weiterhin über dieselbe Bestätigungs-Warteschlange.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.paths import DATA_DIR

_NAME_RE = re.compile(r"^[A-Za-zÄÖÜäöüß0-9 _-]{2,40}$")


class SubAgentService:
    #: Mitarbeiter laufen ueber Groq (llama-3.3-70b, kostenfreie Stufe): 70B statt
    #: lokal 8B, 128k statt 16k Kontext, mit Werkzeug-Unterstuetzung – und
    #: gemessen 0,3 s Antwortzeit gegen 138 s lokal. Judes eigenes Chat-Modell
    #: bleibt davon unberuehrt (dolphin3).
    #: Faellt Groq aus, greift die normale Fallback-Kette.
    STANDARD_MODELL = "cloud_groq_llama"
    TOOL_SCHRITTE = 16       # suchen, lesen, pruefen, ablegen, notieren
    MAX_NOTES = 500          # Obergrenze je Agent
    PROMPT_NOTES = 40        # wie viele davon in den Systemprompt wandern

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

    # ------------------------------------------------------- Eigenes Gedächtnis

    def _memory_path(self, name: str) -> Path:
        return DATA_DIR / "sub_agent_memory" / f"{self._key(name)}.json"

    def notes(self, name: str) -> list[dict]:
        try:
            return json.loads(self._memory_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def remember(self, name: str, note: str) -> dict:
        """Hält eine Erkenntnis dauerhaft fest.

        Sub-Agenten wurden bisher bei jedem Aufruf neu gebaut und danach
        verworfen – ein Akquise-Agent fing damit jedes Mal bei null an. Die
        Notizen überdauern den Lauf und werden beim nächsten Aufruf wieder in
        den Systemprompt gelegt.
        """
        note = str(note).strip()
        if not note:
            raise ValueError("Die Notiz ist leer.")
        items = self.notes(name)
        items.append({"note": note[:800], "created_at": datetime.now(timezone.utc).isoformat()})
        items = items[-self.MAX_NOTES:]
        path = self._memory_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"agent": name, "gespeichert": note[:120], "notizen_gesamt": len(items)}

    def forget_notes(self, name: str) -> dict:
        path = self._memory_path(name)
        removed = len(self.notes(name))
        path.unlink(missing_ok=True)
        return {"agent": name, "geloescht": removed}

    def _memory_tool(self, name: str):
        from core.tool_registry import Tool
        return Tool(
            name="remember_finding",
            description=("Hält eine dauerhafte Notiz fest (z. B. kontaktierte Firma, Absage, "
                         "erfolgreiche Ansprache). Nutze das nach jedem verwertbaren Ergebnis."),
            func=lambda note: self.remember(name, note),
            param_schema={"type": "object", "properties": {
                "note": {"type": "string", "description": "Was dauerhaft erinnert werden soll."}},
                "required": ["note"]},
        )

    def _build_agent(self, spec: dict):
        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        sub = ToolRegistry()
        sub.set_confirmations(self.registry.confirmations)
        for skill in spec["skills"]:
            tool = self.registry.tools.get(skill)
            if tool is not None:
                sub.register(tool)
        sub.register(self._memory_tool(spec["name"]))
        person, alter = spec.get("person"), spec.get("alter")
        wer = f"{person} ({alter})" if person and alter else (person or spec["name"])
        vorstellung = (f"Du heißt {person} und bist {alter} Jahre alt. " if person and alter
                       else f"Du heißt {person}. " if person else "")
        prompt = (f"Du bist {wer}, Mitarbeiter im Team von Jude, zuständig als '{spec['name']}'. "
                  f"{vorstellung}"
                  f"Deine Rolle: {spec['role']}. Nutze ausschließlich deine zugewiesenen Werkzeuge, "
                  f"bleibe bei deiner Aufgabe und antworte knapp und umsetzbar. "
                  f"Wenn du dich meldest, nenne deinen Namen.")
        notes = self.notes(spec["name"])
        if notes:
            recent = "\n".join(f"- {item['note']}" for item in notes[-self.PROMPT_NOTES:])
            prompt += ("\n\nWas du bisher festgehalten hast (nicht doppelt bearbeiten):\n" + recent)
        return Agent(self.router, sub, system_prompt=prompt,
                     max_tool_steps=self.TOOL_SCHRITTE,
                     force_model=spec.get("model") or self.STANDARD_MODELL)

    def run(self, name: str, task: str) -> dict:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        task = str(task).strip()
        if not task:
            raise ValueError("Es wurde keine Aufgabe angegeben.")
        agent = self._build_agent(spec)
        vorher = self.router_verbrauch()
        try:
            answer, status, blockers = agent.process_input(task), "abgeschlossen", []
        except Exception as exc:
            answer, status = "", "fehlgeschlagen"
            blockers = [f"{type(exc).__name__}: {exc}"]
        # Ergebnisformat nach dem Adapter-Vertrag der Agenten-Standards:
        # agent_id, task_id, status, output, blockers und token_usage sind Pflicht.
        nachher = self.router_verbrauch()
        return {
            "agent_id": self._key(spec["name"]),
            "task_id": uuid.uuid4().hex[:12],
            "agent": spec["name"],
            "person": spec.get("person"),
            "alter": spec.get("alter"),
            "role": spec["role"],
            "skills": spec["skills"],
            "status": status,
            "output": {"answer": answer},
            "answer": answer,          # Rückwärtskompatibel für GUI und Werkzeuge
            "blockers": blockers,
            "model": agent.last_model,
            "token_usage": {
                "model": agent.last_model,
                "input_tokens": nachher["input"] - vorher["input"],
                "output_tokens": nachher["output"] - vorher["output"],
                "estimated_cost": round(nachher["cost"] - vorher["cost"], 6),
                "currency": "USD",
            },
        }

    def router_verbrauch(self) -> dict:
        """Zwischenstand des Monatsverbrauchs – Differenz vor/nach einem Lauf
        ergibt den Verbrauch dieses Laufs."""
        try:
            usage = self.router.status()["usage"]
            return {"input": int(usage["input_tokens"]), "output": int(usage["output_tokens"]),
                    "cost": float(usage["cost_usd"])}
        except Exception:
            return {"input": 0, "output": 0, "cost": 0.0}
