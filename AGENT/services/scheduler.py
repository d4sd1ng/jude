"""Zeitgesteuerte, proaktive Aufgaben.

Jude kann Aufgaben zu festen Zeiten oder in Intervallen ausführen: eine Anfrage
an den Agenten stellen, das Briefing sprechen oder ein Werkzeug aufrufen. Das
Ergebnis landet als Benachrichtigung (und wird bei aktiver Sprachsteuerung
vorgelesen). Aufgaben werden als JSON unter ``Jude/data`` gespeichert.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from core.paths import DATA_DIR
from services.notifications import NotificationService


class SchedulerService:
    def __init__(self, agent=None, briefing=None, voice=None):
        self.agent = agent
        self.briefing = briefing
        self.voice = voice
        self.path = DATA_DIR / "scheduled_tasks.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------ Speicher

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- API

    def list(self) -> list[dict]:
        return sorted(self._load().values(), key=lambda t: t.get("created_at", ""))

    def create(self, name: str, action_type: str, *, at: str | None = None,
               every_minutes: int | None = None, prompt: str | None = None,
               tool: str | None = None, tool_args: dict | None = None,
               speak: bool = True) -> dict:
        if action_type not in {"prompt", "briefing", "tool"}:
            raise ValueError("action_type muss prompt, briefing oder tool sein.")
        if action_type == "prompt" and not (prompt or "").strip():
            raise ValueError("Für 'prompt' wird ein Text benötigt.")
        if action_type == "tool" and not tool:
            raise ValueError("Für 'tool' wird ein Werkzeugname benötigt.")
        if at:
            datetime.strptime(at, "%H:%M")  # validiert HH:MM
            schedule = {"type": "daily", "at": at}
        elif every_minutes:
            schedule = {"type": "interval", "every_minutes": max(1, int(every_minutes))}
        else:
            raise ValueError("Bitte 'at' (HH:MM) oder 'every_minutes' angeben.")
        task = {"id": uuid.uuid4().hex[:12], "name": str(name).strip() or "Aufgabe",
                "action_type": action_type, "schedule": schedule,
                "prompt": prompt, "tool": tool, "tool_args": tool_args or {},
                "speak": bool(speak), "enabled": True, "last_run": None,
                "created_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            data = self._load()
            data[task["id"]] = task
            self._save(data)
        return task

    def delete(self, task_id: str) -> dict:
        with self._lock:
            data = self._load()
            if task_id not in data:
                raise KeyError("Aufgabe unbekannt.")
            removed = data.pop(task_id)
            self._save(data)
        return {"id": task_id, "name": removed["name"], "status": "entfernt"}

    def set_enabled(self, task_id: str, enabled: bool) -> dict:
        with self._lock:
            data = self._load()
            if task_id not in data:
                raise KeyError("Aufgabe unbekannt.")
            data[task_id]["enabled"] = bool(enabled)
            self._save(data)
            return data[task_id]

    # --------------------------------------------------------------- Lauf

    @staticmethod
    def _is_due(task: dict, now: datetime) -> bool:
        if not task.get("enabled", True):
            return False
        schedule = task.get("schedule", {})
        last = task.get("last_run")
        last_dt = datetime.fromisoformat(last) if last else None
        if schedule.get("type") == "daily":
            if now.strftime("%H:%M") < schedule.get("at", "99:99"):
                return False
            return last_dt is None or last_dt.date() < now.date()
        if schedule.get("type") == "interval":
            if last_dt is None:
                return True
            return (now - last_dt).total_seconds() >= schedule["every_minutes"] * 60
        return False

    def _run_task(self, task: dict) -> str:
        action = task["action_type"]
        if action == "briefing" and self.briefing is not None:
            return self.briefing.spoken_brief()
        if action == "prompt" and self.agent is not None:
            return str(self.agent.process_input(task["prompt"]))
        if action == "tool" and self.agent is not None:
            return str(self.agent.tools.execute(task["tool"], dict(task.get("tool_args") or {})))
        raise RuntimeError("Aufgabe kann nicht ausgeführt werden (fehlende Komponente).")

    def tick(self, now: datetime | None = None) -> list[dict]:
        """Führt fällige Aufgaben aus. Liefert die ausgelösten Ausführungen."""
        now = now or datetime.now(timezone.utc).astimezone()
        fired = []
        for task in self.list():
            if not self._is_due(task, now):
                continue
            try:
                result = self._run_task(task)
                NotificationService.create("scheduled", task["name"], result[:2000], {"task_id": task["id"]})
                if task.get("speak") and self.voice is not None and self.voice.status().get("running"):
                    try:
                        self.voice._speak(f"{task['name']}: {result[:600]}")
                    except Exception:
                        pass
                fired.append({"id": task["id"], "name": task["name"], "ok": True})
            except Exception as exc:
                NotificationService.create("scheduled_error", task["name"], str(exc)[:1000], {"task_id": task["id"]})
                fired.append({"id": task["id"], "name": task["name"], "ok": False, "error": str(exc)})
            finally:
                with self._lock:
                    data = self._load()
                    if task["id"] in data:
                        data[task["id"]]["last_run"] = now.astimezone(timezone.utc).isoformat()
                        self._save(data)
        return fired
