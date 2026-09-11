"""Zeitgesteuerte, proaktive Aufgaben.

Jude kann Aufgaben zu festen Zeiten oder in Intervallen ausführen: eine Anfrage
an den Agenten stellen, das Briefing sprechen oder ein Werkzeug aufrufen. Das
Ergebnis landet als Benachrichtigung (und wird bei aktiver Sprachsteuerung
vorgelesen). Aufgaben werden als JSON unter ``Jude/data`` gespeichert.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from datetime import datetime, timedelta, timezone

from core.paths import DATA_DIR
from services.notifications import NotificationService

logger = logging.getLogger(__name__)

#: Werkzeuge, die lange laufen und im Hintergrund starten sollen, um den
#: Scheduler-Tick nicht zu blockieren (und damit chat_lock in web/app.py zu halten).
HINTERGRUND_TOOLS = {"delegate_to_agent", "auftrag_erteilen", "revisionen_nacharbeiten",
                     "auftraege_nacharbeiten"}


class SchedulerService:
    #: Harte Obergrenze für gleichzeitig laufende Hintergrund-Werkzeuge
    #: (delegate_to_agent etc.). Ohne sie feuerten nach einem Neustart alle
    #: fälligen Mitarbeiter-Läufe im selben Tick gleichzeitig los (13 auf
    #: einmal, gemessen 03.09.2026) – das darf nie wieder passieren, auch
    #: nicht in diesem Kanten-fall.
    MAX_GLEICHZEITIG = 3

    #: Die eine laufende Instanz - services/review.py und services/
    #: confirmations.py liegen unterhalb von services/team.py in der
    #: Importreihenfolge und bekommen den Scheduler nie durchgereicht; ohne
    #: diese Referenz gäbe es keinen Weg, aus einer Ablehnung heraus sofort
    #: nachzufassen, nur ueber einen Poll-Tick (siehe dispatch_now unten -
    #: 11.09.2026, Tino: "remove that timer... they need to immediatly work
    #: on it").
    #:
    #: WIRD NICHT automatisch in __init__ gesetzt - bewusst. main.py setzt sie
    #: explizit auf die EINE echte Instanz nach der Konstruktion. Automatisch
    #: bei jeder Konstruktion zu setzen sah zunaechst einfacher aus, brach aber
    #: die Testisolation: tests/test_new_features.py baut zwei eigene
    #: SchedulerService()-Instanzen mit eigenen (teils fehlenden) 'agent'-
    #: Referenzen fuer isolierte Scheduler-Tests. Jede Konstruktion startet
    #: MAX_GLEICHZEITIG echte Daemon-Worker-Threads, die den Prozess ueberleben
    #: (Daemon-Threads enden nie von selbst) - mit Auto-Registrierung wurde
    #: JEDE dieser Test-Instanzen zur globalen Singleton, und ein spaeterer,
    #: voellig unabhaengiger Test, der revision() aufrief, weckte den
    #: liegengebliebenen Worker-Thread der ALTEN Test-Instanz auf, der dann
    #: mit dem 'agent' (oder None) dieses alten Tests parallel auf dieselbe
    #: Test-Datenbank zugriff wie der gerade laufende Test - Ursache eines
    #: "duplicate column name"-Races beim Schema-Migrationscheck in
    #: services/database.py, gemessen 11.09.2026 direkt nach Einfuehrung.
    _aktiv: "SchedulerService | None" = None

    @classmethod
    def aktuelle(cls) -> "SchedulerService | None":
        return cls._aktiv

    @classmethod
    def registrieren(cls, instanz: "SchedulerService") -> None:
        """Von main.py nach der echten Konstruktion aufzurufen - siehe
        Kommentar bei ``_aktiv`` oben, warum das nicht automatisch passiert."""
        cls._aktiv = instanz

    def __init__(self, agent=None, briefing=None, voice=None):
        self.agent = agent
        self.briefing = briefing
        self.voice = voice
        self.path = DATA_DIR / "scheduled_tasks.json"
        self._lock = threading.Lock()
        # last_run wird erst bei echtem Abschluss geschrieben (siehe _verarbeite) –
        # ohne dieses Set haette jeder 30-Sekunden-Tick eine noch laufende,
        # aber weiterhin "faellige" Aufgabe erneut eingereiht, bis ein Slot frei
        # wird. Ergebnis waeren mehrere gleichzeitige Laeufe derselben Aufgabe.
        self._laufend_ids: set[str] = set()
        # Ein threading.Semaphore mit einem Thread pro Aufgabe war NICHT fair:
        # nach einem Neustart mit vielen gleichzeitig fälligen Aufgaben blieben
        # einzelne Mitarbeiter (Coder, designer, engineer) 16+ Minuten lang
        # unbearbeitet liegen, waehrend andere mehrfach drankamen – Python
        # garantiert bei vielen wartenden Threads keine strikte Reihenfolge.
        # Eine echte FIFO-Warteschlange mit festen Worker-Threads garantiert
        # Reihenfolge: wer zuerst faellig war, kommt zuerst dran.
        self._warteschlange: "queue.Queue[dict]" = queue.Queue()
        for _ in range(self.MAX_GLEICHZEITIG):
            threading.Thread(target=self._worker, daemon=True).start()

    #: Mindestabstand zwischen zwei dispatch_now()-Ausloesungen fuer DENSELBEN
    #: Mitarbeiter (siehe Kommentar in dispatch_now unten - Endlosschleifen-
    #: Schutz, gemessen 11.09.2026).
    DISPATCH_NOW_COOLDOWN_SEC = 120

    def dispatch_now(self, agent_name: str, task_text: str) -> bool:
        """Eine Revision SOFORT einreihen statt auf den naechsten Poll-Tick zu
        warten (11.09.2026 - Tino: eine Ablehnung soll nicht bis zu 5/30
        Minuten liegen bleiben, bevor jemand drangeht).

        Landet trotzdem im selben begrenzten Worker-Pool wie geplante
        Aufgaben (MAX_GLEICHZEITIG Worker-Threads) - ein Schwall gleichzeitiger
        Ablehnungen reiht sich FIFO ein statt unbegrenzt parallel zu feuern,
        derselbe Schutz, der nach dem Budget-Blowout vom 03.09.2026 eingefuehrt
        wurde. Kein Eintrag in scheduled_tasks.json noetig - _abschluss()
        ignoriert eine Aufgaben-ID, die dort nicht vorkommt, klaglos.

        Gibt False zurueck (und tut nichts), wenn der Mitarbeiter gerade
        bereits laeuft - sein laufender Durchgang bekommt die Revision beim
        naechsten eigenen Abschluss ohnehin zu sehen (offene_revisionen()/
        agenten_mit_offenen_revisionen() sind Teil seines Systemprompts).

        COOLDOWN (neu, 11.09.2026 - echte Endlosschleife beobachtet): der
        deterministische Gate-Check in team_gate.py laeuft SYNCHRON am Ende
        jedes eigenen Laufs eines Mitarbeiters und ruft bei ungueltigem JSON
        selbst queue.revision() auf - das loeste dispatch_now() aus, WAEHREND
        der Mitarbeiter laut agent_runs schon wieder auf 'nicht laufend'
        stand (sein Lauf war ja gerade fertig). Der obige "laeuft bereits"-
        Schutz griff darum nicht: scraper wurde binnen 20 Minuten 14 Runden
        lang sofort erneut gestartet, produzierte praktisch dieselbe defekte
        Ausgabe erneut und loeste damit die naechste sofortige Ablehnung aus -
        eine echte Endlosschleife, viel schneller als jeder vorherige Poll-
        Takt sie je erzeugen konnte. Ein Mitarbeiter, der innerhalb der
        Cooldown-Zeit schon einen eigenen Lauf beendet hat, wartet stattdessen
        bis zur naechsten regulaeren Gelegenheit (naechster eigener Trigger,
        z.B. sein eigener Tagesablauf oder eine neue, spaetere Ablehnung nach
        Ablauf der Cooldown-Zeit) - kein blindes Sofort-Wiederholen derselben
        vermutlich unveraenderten Ausgabe.
        """
        from services.database import connection
        with connection() as db:
            laufend = db.execute(
                "SELECT 1 FROM agent_runs WHERE agent=? AND status='laufend' LIMIT 1",
                (agent_name,)).fetchone()
            letzter = db.execute(
                "SELECT created_at FROM agent_runs WHERE agent=? ORDER BY created_at DESC LIMIT 1",
                (agent_name,)).fetchone()
        if laufend:
            return False
        if letzter:
            # agent_runs.created_at steht als Python-isoformat() ('...T...+00:00'),
            # SQLite's eigenes datetime('now', '-N seconds') liefert ein anderes
            # Format ('YYYY-MM-DD HH:MM:SS') - ein roher String-Vergleich zwischen
            # beiden waere IMMER wahr (Groszbuchstabe 'T' sortiert lexikalisch nach
            # dem Leerzeichen), haette dispatch_now() also fuer immer lahmgelegt,
            # nicht nur fuer die Cooldown-Zeit. Deshalb hier in Python parsen und
            # vergleichen statt der SQL-Funktion zu vertrauen.
            zuletzt = datetime.fromisoformat(letzter["created_at"])
            if (datetime.now(timezone.utc) - zuletzt).total_seconds() < self.DISPATCH_NOW_COOLDOWN_SEC:
                return False
        self._warteschlange.put({
            "id": uuid.uuid4().hex[:12],
            "name": f"Revision sofort ({agent_name})",
            "action_type": "tool",
            "tool": "delegate_to_agent",
            "tool_args": {"name": agent_name, "task": task_text},
        })
        return True

    def _worker(self) -> None:
        while True:
            task = self._warteschlange.get()
            try:
                self._verarbeite(task)
            finally:
                with self._lock:
                    self._laufend_ids.discard(task["id"])
                self._warteschlange.task_done()

    @staticmethod
    def _zusammenfassen(ergebnis) -> str:
        """Kompakte, lesbare Notification statt der rohen Werkzeug-Antwort.

        ``delegate_to_agent`` (AgentRunner.run()) liefert u.a. den kompletten
        Systemprompt unter 'role' - bei einem str(dict)[:2000] fraß allein das
        den ganzen Platz auf, noch bevor 'status'/'answer' ueberhaupt anfingen
        (gemessen 11.09.2026: jede 'Trendrecherche (Rasmus)'-Notification
        brach mitten in scrapers Rollentext ab, kein einziges Mal war zu
        sehen, ob und was er tatsaechlich gefunden hatte).
        """
        if isinstance(ergebnis, dict) and "agent" in ergebnis and "status" in ergebnis:
            wer = ergebnis.get("person") or ergebnis.get("agent")
            antwort = str(ergebnis.get("answer") or "").strip()
            kern = f"{wer} ({ergebnis['agent']}): {ergebnis['status']}"
            return f"{kern} - {antwort[:1800]}" if antwort else kern
        return str(ergebnis)[:2000]

    def _verarbeite(self, task: dict) -> None:
        fehler = None
        begonnen = datetime.now(timezone.utc)
        logger.info("Scheduler: '%s' (%s) startet.", task.get("name", task["id"]), task.get("tool"))
        try:
            ergebnis = self.agent.tools.execute(task["tool"], dict(task.get("tool_args") or {}))
            if isinstance(ergebnis, str) and "fehlgeschlagen:" in ergebnis[:160]:
                raise RuntimeError(ergebnis[:300])
            NotificationService.create("scheduled", task["name"], self._zusammenfassen(ergebnis), {"task_id": task["id"]})
        except Exception as exc:
            fehler = str(exc)
            try:
                NotificationService.create("scheduled_error",
                                           f"{task.get('name', task.get('tool'))} fehlgeschlagen",
                                           str(exc)[:300])
            except Exception:
                pass
        dauer = (datetime.now(timezone.utc) - begonnen).total_seconds()
        if fehler:
            logger.warning("Scheduler: '%s' fehlgeschlagen nach %.1fs: %s",
                           task.get("name", task["id"]), dauer, fehler[:300])
        else:
            logger.info("Scheduler: '%s' fertig nach %.1fs.", task.get("name", task["id"]), dauer)
        self._abschluss(task, datetime.now(timezone.utc).astimezone(), fehler=fehler)

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
                # In die FIFO-Warteschlange einreihen statt einen eigenen
                # Thread zu starten – MAX_GLEICHZEITIG feste Worker holen sich
                # Aufgaben in der Reihenfolge, in der sie faellig wurden. Ohne
                # dieses Sperr-Set haette jeder 30-Sekunden-Tick dieselbe, noch
                # wartende Aufgabe erneut eingereiht, solange last_run noch
                # nicht aktualisiert ist (last_run wird erst bei echtem
                # Abschluss in _verarbeite gesetzt, nicht beim Einreihen –
                # sonst gilt ein durch einen Neustart abgewuergter Lauf als
                # "gerade erledigt" und wird erst wieder in vollen 5 Minuten
                # faellig statt sofort erneut anzulaufen).
                with self._lock:
                    if task["id"] in self._laufend_ids:
                        return None
                    self._laufend_ids.add(task["id"])
                self._warteschlange.put(task)
                return None
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
                if result is None:
                    # Im Hintergrund gestartet: der Thread selbst meldet und
                    # schreibt last_run bei echtem Abschluss, nicht hier.
                    fired.append({"id": task["id"], "name": task["name"], "ok": True, "hintergrund": True})
                    continue
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
