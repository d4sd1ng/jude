"""Fuehrt einen Sub-Agenten-Lauf aus und haelt seinen Lebenszyklus fest:
Start-/End-Protokoll in ``agent_runs``, Wiederholungs-Schutz gegen Leerlauf-
Werkzeugaufrufe, ehrlicher Status (abgeschlossen/teilweise/fehlgeschlagen)
statt eines pauschalen 'erledigt'. Aus ``services.team.SubAgentService``
ausgelagert - Teil 3, Stage C, letzter Schritt des Refactorings
(jolly-napping-tulip.md).

Braucht den ``AgentBuilder`` (fuer ``build_agent``) und die Fassade selbst
fuer die restlichen Querbezuege (``get``, ``_key``, ``_chefpruefung``,
``QUELLEN_PFLICHT``/``SCHREIB_PFLICHT``, ``REDAKTEUR``, ``router``).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AgentRunner:
    #: Rueckgabetexte, mit denen ``ToolRegistry.execute`` einen Fehlschlag meldet.
    FEHLERMUSTER = ("' fehlgeschlagen:", "' nicht gefunden.",
                    "Tool-Argumente müssen ein Objekt sein.",
                    "Aktion konnte nicht vorgemerkt werden:")

    def __init__(self, svc, builder):
        self.svc = svc
        self.builder = builder

    def run(self, name: str, task: str) -> dict:
        svc = self.svc
        spec = svc.get(name)
        if spec is None:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        task = str(task).strip()
        if not task:
            raise ValueError("Es wurde keine Aufgabe angegeben.")
        agent = self.builder.build_agent(spec)
        # Werkzeugaufrufe mitschreiben: ohne sie ist hinterher nicht
        # nachvollziehbar, woran ein Lauf haengengeblieben ist.
        #
        # Die Werkzeugschicht wirft bei einem Fehlschlag keine Ausnahme, sondern
        # gibt ihn als Text zurueck (tool_registry.execute). Der Agent formuliert
        # daraus eine hoefliche Antwort, und der Lauf sah bisher gelungen aus:
        # 11 von 11 Laeufen standen auf 'abgeschlossen', obwohl 6 nichts
        # zustande gebracht hatten. Deshalb wird hier jeder Rueckgabewert geprueft.
        werkzeuge: list[str] = []
        fehlschlaege: list[str] = []
        # IDs, die submit_for_review in DIESEM Lauf tatsaechlich anlegte oder
        # weiterreichte – ohne die laesst sich eine Vorlage, die ohne die
        # Pflicht-Schreibfunktion vorgelegt wurde (SCHREIB_PFLICHT), nicht
        # gezielt zurueckweisen, nur der ganze Lauf als 'teilweise' markieren.
        eingereichte_ids: list[str] = []
        original = agent.tools.execute
        # Schutz gegen den Leerlauf, der die Werkzeugketten auffrisst: derselbe
        # Aufruf mit denselben Argumenten, unmittelbar hintereinander. Gemessen
        # 05.09.2026: von 769 Aufrufen an einem Tag waren 6 % erzeugend und
        # 67 % melden/listen – projektleitung holte 164x 'pruefungsliste' und
        # traf dabei EINE Entscheidung. Das Werkzeug liefert etwas, das wie neue
        # Arbeit aussieht, das Modell verarbeitet es und ruft erneut auf.
        #
        # Bewusst nur bei UNMITTELBARER Wiederholung: hat der Mitarbeiter
        # zwischendurch etwas anderes getan (z. B. entschieden), hat sich die
        # Lage geaendert und dieselbe Abfrage ist berechtigt. Das ist kein
        # Deckel auf die Zahl der Schritte, sondern macht Leerlauf unmoeglich.
        letzter: dict = {"schluessel": None, "ergebnis": None, "zaehler": 0}

        def _schluessel(werkzeug, argumente):
            try:
                return werkzeug + "|" + json.dumps(argumente, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                return werkzeug + "|" + repr(argumente)

        def mitschreiben(werkzeug, argumente):
            werkzeuge.append(werkzeug)
            schluessel = _schluessel(werkzeug, argumente)
            if schluessel == letzter["schluessel"]:
                letzter["zaehler"] += 1
                if letzter["zaehler"] >= 2:
                    return (f"'{werkzeug}' wurde gerade eben mit genau denselben Argumenten "
                            f"aufgerufen und liefert unveraendert dasselbe. Der Aufruf wurde "
                            f"nicht erneut ausgefuehrt. Es hat sich seitdem nichts geaendert – "
                            f"nimm das vorherige Ergebnis und mach den naechsten Schritt deiner "
                            f"Aufgabe, oder beende den Lauf, wenn nichts mehr zu tun ist.")
            else:
                letzter["schluessel"], letzter["zaehler"] = schluessel, 1
            ergebnis = original(werkzeug, argumente)
            letzter["ergebnis"] = ergebnis
            if self._ist_fehlschlag(ergebnis):
                fehlschlaege.append(str(ergebnis).strip()[:300])
            if werkzeug == "submit_for_review" and isinstance(ergebnis, dict) and ergebnis.get("id"):
                eingereichte_ids.append(ergebnis["id"])
            return ergebnis
        agent.tools.execute = mitschreiben
        ergebnis_id = uuid.uuid4().hex[:12]
        self._lauf_beginnen(ergebnis_id, spec, task)
        begonnen = time.monotonic()
        vorher = self.router_verbrauch()
        try:
            answer, status, blockers = agent.process_input(task), "abgeschlossen", []
        except Exception as exc:
            answer, status = "", "fehlgeschlagen"
            blockers = [f"{type(exc).__name__}: {exc}"]
        if status != "fehlgeschlagen":
            status, blockers = self._bewerten(werkzeuge, fehlschlaege,
                                              hat_werkzeuge=bool(agent.tools.tools))
        pflicht = svc.QUELLEN_PFLICHT.get(spec["name"])
        if status == "abgeschlossen" and pflicht and pflicht not in werkzeuge:
            status = "teilweise"
            blockers = blockers + [f"Pflicht-Quelle nicht abgefragt: '{pflicht}' fehlt in den "
                                   f"Werkzeugaufrufen – 'nichts zu tun' zaehlt nur nach echter Pruefung."]
        schreib_pflicht = svc.SCHREIB_PFLICHT.get(spec["name"], ())
        fehlende_pflicht = [w for w in schreib_pflicht if w not in werkzeuge]
        if fehlende_pflicht and eingereichte_ids:
            from services.review import ReviewQueue
            queue = ReviewQueue()
            fehlend_text = " und ".join(f"'{w}'" for w in fehlende_pflicht)
            for rid in eingereichte_ids:
                try:
                    voll = queue.zeigen(rid)
                    if voll and voll.get("status") == "pruefung":
                        queue.revision(rid, f"Jude: Vorgelegt, ohne {fehlend_text} in diesem Lauf "
                                        f"aufzurufen – das ist dieselbe, unveraenderte Vorlage wie zuvor. "
                                        f"Erst wirklich schreiben/erzeugen, dann vorlegen.")
                except Exception:
                    pass
            status = "teilweise"
            blockers = blockers + [f"Pflicht-Werkzeug(e) nicht aufgerufen: {fehlend_text} fehlt/fehlen – "
                                   f"vorgelegte Datei(en) automatisch zurueckgewiesen."]
        # Ergebnisformat nach dem Adapter-Vertrag der Agenten-Standards:
        # agent_id, task_id, status, output, blockers und token_usage sind Pflicht.
        nachher = self.router_verbrauch()
        dauer = int((time.monotonic() - begonnen) * 1000)
        self._protokollieren(ergebnis_id, spec, task, status, answer, blockers,
                             agent.last_model, nachher, vorher, dauer, werkzeuge)
        # Der Mitarbeiter ist hier fertig – die Chefpruefung haelt ihn nicht auf.
        # Der Redakteur legt nichts vor, bei ihm gaebe es nichts zu pruefen.
        geprueft = ([] if spec["name"] == svc.REDAKTEUR
                    else svc._chefpruefung(spec["name"]))
        return {
            "agent_id": svc._key(spec["name"]),
            "task_id": ergebnis_id,
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
            "duration_ms": dauer,
            "tool_calls": werkzeuge,
            "chefpruefung": geprueft,
        }

    @classmethod
    def _ist_fehlschlag(cls, ergebnis) -> bool:
        text = str(ergebnis)
        return any(muster in text for muster in cls.FEHLERMUSTER)

    @staticmethod
    def _bewerten(werkzeuge: list[str], fehlschlaege: list[str],
                  hat_werkzeuge: bool = True) -> tuple[str, list[str]]:
        """Ehrlicher Status statt pauschalem 'abgeschlossen'.

        Bewertet wird ausschliesslich Gemessenes – welche Werkzeuge liefen und
        welche meldeten einen Fehler. Der Antworttext wird bewusst nicht
        ausgewertet: ein Modell, das seinen Misserfolg schoenschreibt, wuerde
        sonst genau die Luecke wieder aufreissen.

        ``fehlgeschlagen``  kein Werkzeug hat funktioniert
        ``teilweise``       einzelne Werkzeuge scheiterten, oder es wurde gar
                            keines benutzt (das Modell hat den Aufruf nur
                            hingeschrieben statt ihn auszufuehren)
        ``abgeschlossen``   alle Werkzeugaufrufe gingen durch
        """
        if not hat_werkzeuge:
            # Der Redakteur hat keine Werkzeuge; sein Ergebnis IST der Text.
            return "abgeschlossen", []
        if not werkzeuge:
            return "teilweise", ["Kein Werkzeug benutzt – das Ergebnis wurde nirgends abgelegt."]
        if not fehlschlaege:
            return "abgeschlossen", []
        if len(fehlschlaege) >= len(werkzeuge):
            return "fehlgeschlagen", fehlschlaege
        return "teilweise", fehlschlaege

    def _lauf_beginnen(self, lauf_id, spec, task) -> None:
        """Sofort beim Start eine Zeile anlegen (status='laufend') – sonst
        taucht ein laufender Auftrag nirgends auf, bevor er fertig ist, und
        es ist von aussen nicht zu sehen, ob gerade jemand arbeitet."""
        try:
            from services.database import connection
            with connection() as db:
                db.execute(
                    "INSERT INTO agent_runs(id,created_at,agent,person,task,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (lauf_id, datetime.now(timezone.utc).isoformat(), spec["name"],
                     spec.get("person"), task[:4000], "laufend"))
        except Exception as exc:
            logger.warning("Laufstart nicht protokolliert: %s", exc)

    def laufende_bereinigen(self) -> int:
        """Beim Start aufräumen: ein 'laufend' aus einem früheren Prozess kann
        nur ein Geist sein – der Prozess, der ihn hätte fertigstellen können,
        ist weg. Ohne das zeigt die Live-Anzeige für immer 'aktiv', obwohl
        seit einem Neustart niemand mehr daran arbeitet."""
        from services.database import connection
        with connection() as db:
            cur = db.execute(
                "UPDATE agent_runs SET status='unterbrochen',"
                " blockers='Prozess neu gestartet, Lauf verwaist' WHERE status='laufend'")
            return cur.rowcount

    def status_uebersicht(self) -> list[dict]:
        """Für jeden Mitarbeiter: arbeitet er gerade, oder wann/wie war sein
        letzter Lauf – nicht nur eine Liste, die meistens leer ist, weil
        gerade niemand aktiv läuft. 'Niemand arbeitet gerade' ist etwas
        anderes als 'hier steht nichts', und beides muss sich unterscheiden."""
        from services.database import connection
        with connection() as db:
            aktiv = {r["agent"]: dict(r) for r in db.execute(
                "SELECT agent,created_at,task FROM agent_runs "
                "WHERE status='laufend' ORDER BY created_at").fetchall()}
            letzte: dict[str, dict] = {}
            for r in db.execute(
                    "SELECT agent,status,created_at FROM agent_runs "
                    "WHERE status!='laufend' ORDER BY created_at DESC").fetchall():
                letzte.setdefault(r["agent"], dict(r))
        ergebnis = []
        for spec in self.svc.list():
            name = spec["name"]
            eintrag = {"agent": name, "person": spec.get("person")}
            if name in aktiv:
                eintrag.update(status="aktiv", seit=aktiv[name]["created_at"], task=aktiv[name]["task"])
            elif name in letzte:
                eintrag.update(status="idle", letzter_status=letzte[name]["status"],
                                letzter_lauf_am=letzte[name]["created_at"])
            else:
                eintrag["status"] = "noch_nie"
            ergebnis.append(eintrag)
        return ergebnis

    def _protokollieren(self, lauf_id, spec, task, status, answer, blockers,
                        modell, nachher, vorher, dauer, werkzeuge) -> None:
        """Die beim Start angelegte Zeile mit dem Ergebnis abschliessen –
        Nachweis, Fehlersuche und spaeter die Grundlage fuer das Training des
        Teams. Ein Protokollfehler darf den Lauf selbst nie scheitern lassen."""
        try:
            from services.database import connection
            with connection() as db:
                db.execute(
                    "UPDATE agent_runs SET status=?,answer=?,blockers=?,model=?,"
                    "input_tokens=?,output_tokens=?,cost_usd=?,duration_ms=?,tool_calls=? "
                    "WHERE id=?",
                    (status, (answer or "")[:8000], " | ".join(blockers)[:2000], modell,
                     nachher["input"] - vorher["input"], nachher["output"] - vorher["output"],
                     round(nachher["cost"] - vorher["cost"], 6), dauer, ",".join(werkzeuge)[:1000],
                     lauf_id))
        except Exception as exc:
            logger.warning("Agentenlauf nicht protokolliert: %s", exc)

    def router_verbrauch(self) -> dict:
        """Zwischenstand des Monatsverbrauchs – Differenz vor/nach einem Lauf
        ergibt den Verbrauch dieses Laufs."""
        try:
            usage = self.svc.router.status()["usage"]
            return {"input": int(usage["input_tokens"]), "output": int(usage["output_tokens"]),
                    "cost": float(usage["cost_usd"])}
        except Exception:
            return {"input": 0, "output": 0, "cost": 0.0}
