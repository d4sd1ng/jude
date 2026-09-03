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
from datetime import datetime, timedelta, timezone

from core.paths import DATA_DIR
from services.notifications import NotificationService

#: Werkzeuge, die lange laufen und im Hintergrund starten sollen, um den
#: Scheduler-Tick nicht zu blockieren (und damit chat_lock in web/app.py zu halten).
HINTERGRUND_TOOLS = {"auftragswaechter", "delegate_to_agent", "auftrag_erteilen", "team_tagesrunde"}


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
        vorbelegt_last_run = None
        if at:
            datetime.strptime(at, "%H:%M")  # validiert HH:MM
            schedule = {"type": "daily", "at": at}
            # Ohne das feuert eine "taeglich um 07:00" Aufgabe SOFORT, wenn sie
            # nach 07:00 angelegt wird: _is_due() sieht last_run=None und die
            # Uhrzeit bereits ueberschritten und haelt das fuer faellig. Neun
            # Mitarbeiter-Aufgaben liefen so alle gleichzeitig um 21:43 statt
            # gestaffelt am naechsten Morgen (02.09.2026). Ist die Zielzeit
            # heute schon vorbei, gilt der heutige Tag als erledigt.
            jetzt = datetime.now(timezone.utc).astimezone()
            if jetzt.strftime("%H:%M") >= at:
                vorbelegt_last_run = jetzt.astimezone(timezone.utc).isoformat()
        elif every_minutes:
            schedule = {"type": "interval", "every_minutes": max(1, int(every_minutes))}
        else:
            raise ValueError("Bitte 'at' (HH:MM) oder 'every_minutes' angeben.")
        task = {"id": uuid.uuid4().hex[:12], "name": str(name).strip() or "Aufgabe",
                "action_type": action_type, "schedule": schedule,
                "prompt": prompt, "tool": tool, "tool_args": tool_args or {},
                "speak": bool(speak), "enabled": True, "last_run": vorbelegt_last_run,
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

    #: Ein gescheiterter Lauf wird einmal wiederholt – so lange danach.
    WIEDERHOLUNG_MIN = 30
    MAX_VERSUCHE = 2

    @staticmethod
    def _lokal(wert: str | None, now: datetime) -> datetime | None:
        """Zeitstempel in der Zeitzone von ``now`` lesen.

        ``last_run`` wird in UTC abgelegt, ``now`` ist Ortszeit. Ohne Umrechnung
        vergleicht der Tagesabgleich unten UTC-Datum mit Ortsdatum: eine Aufgabe
        um 00:30 Ortszeit (UTC+2) gilt danach den ganzen Vormittag als noch
        nicht gelaufen und feuert alle 30 Sekunden erneut.
        """
        if not wert:
            return None
        try:
            stamp = datetime.fromisoformat(wert)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(now.tzinfo)

    @classmethod
    def _is_due(cls, task: dict, now: datetime) -> bool:
        if not task.get("enabled", True):
            return False
        schedule = task.get("schedule", {})
        last_dt = cls._lokal(task.get("last_run"), now)
        # Nachholtermin nach einem Fehlschlag: ein Netzaussetzer am Vormittag
        # hat den Tageslauf bisher endgueltig gekostet – die Aufgabe galt als
        # erledigt und war bis zum naechsten Tag gesperrt.
        wieder = cls._lokal(task.get("retry_at"), now)
        if wieder is not None and now >= wieder:
            return True
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
            # Werkzeuge, die lange laufen (z. B. Auftragswächter), können im
            # Hintergrund starten – der Scheduler-Tick blockiert sonst Minuten lang.
            if task.get("tool") in HINTERGRUND_TOOLS:
                def _im_hintergrund():
                    try:
                        ergebnis = self.agent.tools.execute(task["tool"], dict(task.get("tool_args") or {}))
                        if isinstance(ergebnis, str) and "fehlgeschlagen:" in ergebnis[:160]:
                            raise RuntimeError(ergebnis[:300])
                    except Exception as exc:
                        try:
                            NotificationService.create("scheduled_error",
                                                       f"{task.get('name', task.get('tool'))} fehlgeschlagen",
                                                       str(exc)[:300])
                        except Exception:
                            pass
                threading.Thread(target=_im_hintergrund, daemon=True).start()
                return "im Hintergrund gestartet"
            # Normale Werkzeuge (schnell) laufen synchron, damit der Ergebnis sofort
            # zur Benachrichtigung wird.
            ergebnis = self.agent.tools.execute(task["tool"], dict(task.get("tool_args") or {}))
            ergebnis_str = str(ergebnis)
            # Werkzeugschicht wirft nicht, sondern liefert Fehler-Strings – diese
            # müssen wir explizit als Fehler wirken, sonst zählt der Fehlschlag als Erfolg.
            if isinstance(ergebnis, str) and "fehlgeschlagen:" in ergebnis[:160]:
                raise RuntimeError(ergebnis_str[:300])
            return ergebnis_str
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
                self._abschluss(task, now, fehler=None)
            except Exception as exc:
                NotificationService.create("scheduled_error", task["name"], str(exc)[:1000], {"task_id": task["id"]})
                fired.append({"id": task["id"], "name": task["name"], "ok": False, "error": str(exc)})
                self._abschluss(task, now, fehler=str(exc))
        return fired

    def _abschluss(self, task: dict, now: datetime, fehler: str | None) -> None:
        """Lauf festschreiben.

        ``last_run`` bekommt die **Endzeit**, nicht die Startzeit des Ticks:
        die Aufgaben laufen nacheinander, und ein Lauf kann Minuten dauern –
        bisher trugen alle Aufgaben eines Durchgangs denselben Zeitstempel.
        """
        ende = datetime.now(timezone.utc)
        with self._lock:
            data = self._load()
            eintrag = data.get(task["id"])
            if eintrag is None:
                return
            eintrag["last_run"] = ende.isoformat()
            versuche = int(eintrag.get("versuche") or 0)
            if fehler is None:
                eintrag.pop("retry_at", None)
                eintrag.pop("versuche", None)
                eintrag.pop("last_error", None)
            elif versuche + 1 < self.MAX_VERSUCHE:
                eintrag["versuche"] = versuche + 1
                eintrag["retry_at"] = (ende + timedelta(minutes=self.WIEDERHOLUNG_MIN)).isoformat()
                eintrag["last_error"] = fehler[:500]
            else:                       # ausgereizt: erst morgen wieder
                eintrag.pop("retry_at", None)
                eintrag["versuche"] = 0
                eintrag["last_error"] = fehler[:500]
            self._save(data)
