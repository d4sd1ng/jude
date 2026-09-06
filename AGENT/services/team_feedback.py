"""Drei Formen von Rueckmeldung an einen Sub-Agenten ueber seine eigenen
Laeufe hinweg: stille Notizen (``notes``), Beanstandungen (``lehren``) und
Lob (``lob``). Aus ``services.team.SubAgentService`` ausgelagert - Teil 3,
Stage C des Refactorings.

Alle drei teilen sich dieselbe Grundmechanik (Pfad je Mitarbeiter unter
einem eigenen Unterordner, JSON-Liste lesen/schreiben, Obergrenze) - die
ist hier einmal zusammengezogen. Wo sie sich wirklich unterscheiden, bleibt
getrennt: ``notes`` haengt nur an, ``lehren``/``lob`` fassen gleichlautende
Eintraege zusammen und zaehlen mit, und nur ``lehre_merken`` loest bei der
dritten gleichen Beanstandung eine Prompt-Diagnose im Auftragsbuch aus -
das ist der eine Punkt, an dem die drei NICHT identisch sind.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.paths import DATA_DIR


class FeedbackStore:
    MAX_NOTES = 500          # Obergrenze je Agent
    MAX_LEHREN = 40          # Beanstandungen UND Lob je Mitarbeiter (dieselbe Grenze wie im Original)
    #: Was nicht ins Gedaechtnis darf – Stoerungsmeldungen statt Ergebnisse.
    KEINE_NOTIZ = ("fehler beim", "fehlgeschlagen", "nicht erreichbar",
                   "kann nicht hergestellt werden", "keine verbindung",
                   "verbindung zu", "timeout", "konnte nicht abgerufen")

    def __init__(self, key_fn):
        #: dieselbe Normalisierung wie AgentRegistry._key - als Funktion
        #: uebergeben statt importiert, damit diese Klasse nicht von
        #: services.team_registry abhaengt (beide haengen sonst nur an
        #: derselben trivialen Regel, nicht aneinander).
        self._key = key_fn

    # ------------------------------------------------------- gemeinsame Basis

    def _pfad(self, unterordner: str, name: str) -> Path:
        return DATA_DIR / unterordner / f"{self._key(name)}.json"

    @staticmethod
    def _lesen(pfad: Path) -> list[dict]:
        try:
            return json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    @staticmethod
    def _schreiben(pfad: Path, eintraege: list[dict]) -> None:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(eintraege, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _kern(text: str) -> str:
        """Vergleichsform einer Beanstandung/eines Lobs – Interpunktion und Fuellwoerter raus."""
        return re.sub(r"[^a-z0-9 ]+", "", str(text).casefold())[:90].strip()

    def _zusammenfassen(self, pfad: Path, text: str, quelle: str, obergrenze: int,
                        bei_dritter_wiederholung=None) -> dict:
        """Gemeinsamer Kern von lehre_merken/lob_merken: gleiche Kernaussage
        zaehlt mit statt eine neue Zeile anzulegen, sortiert nach Haeufigkeit/
        Aktualitaet, auf ``obergrenze`` gekappt."""
        kern = self._kern(text)
        eintraege = self._lesen(pfad)
        for eintrag in eintraege:
            if eintrag.get("kern") == kern:
                eintrag["anzahl"] = int(eintrag.get("anzahl", 1)) + 1
                eintrag["zuletzt"] = datetime.now(timezone.utc).isoformat()
                if bei_dritter_wiederholung and int(eintrag.get("anzahl", 1)) == 3:
                    bei_dritter_wiederholung(eintrag)
                break
        else:
            eintraege.append({"kern": kern, "text": text[:400], "quelle": quelle,
                              "anzahl": 1,
                              "zuletzt": datetime.now(timezone.utc).isoformat()})
        eintraege.sort(key=lambda e: (-int(e.get("anzahl", 1)), e.get("zuletzt", "")))
        eintraege = eintraege[:obergrenze]
        self._schreiben(pfad, eintraege)
        return {"gemerkt": True, "gesamt": len(eintraege)}

    # ------------------------------------------------------- Notizen (notes)

    def notes(self, name: str) -> list[dict]:
        return self._lesen(self._pfad("sub_agent_memory", name))

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
        # Das Gedaechtnis ist fuer Ergebnisse da, nicht fuer Stoerungen. Bernds
        # Gedaechtnis bestand aus drei Fehlermeldungen und einem Platzhalter –
        # beim naechsten Lauf las er als 'was ich bisher weiss', dass nichts geht.
        # Stoerungen gehoeren in die Blocker des Laufs.
        if any(muster in note.casefold() for muster in self.KEINE_NOTIZ):
            raise ValueError("Stoerungen gehoeren nicht ins Gedaechtnis, sondern in die "
                             "Antwort des Laufs. Halte hier nur Ergebnisse fest "
                             "(z. B. 'Firma X, Adresse Y aus dem Impressum').")
        pfad = self._pfad("sub_agent_memory", name)
        items = self._lesen(pfad)
        items.append({"note": note[:800], "created_at": datetime.now(timezone.utc).isoformat()})
        items = items[-self.MAX_NOTES:]
        self._schreiben(pfad, items)
        return {"agent": name, "gespeichert": note[:120], "notizen_gesamt": len(items)}

    def forget_notes(self, name: str) -> dict:
        pfad = self._pfad("sub_agent_memory", name)
        removed = len(self._lesen(pfad))
        pfad.unlink(missing_ok=True)
        return {"agent": name, "geloescht": removed}

    # --------------------------------------------------- Beanstandungen (lehren)

    def lehren(self, name: str) -> list[dict]:
        """Was diesem Mitarbeiter schon einmal beanstandet wurde."""
        return self._lesen(self._pfad("sub_agent_lessons", name))

    def lehre_merken(self, name: str, beanstandung: str, quelle: str = "Jude") -> dict:
        """Eine Beanstandung dauerhaft festhalten – einmal, nicht hundertmal.

        Ohne das wiederholt ein Mitarbeiter denselben Fehler bei jedem Lauf:
        er sieht die Revision, arbeitet sie ab und hat beim naechsten Mal
        wieder keine Ahnung davon. Gleichlautende Beanstandungen werden
        zusammengefasst und mitgezaehlt; was oft kam, steht oben.
        """
        beanstandung = str(beanstandung).strip()
        if len(beanstandung) < 8:
            return {"gemerkt": False}

        def _dritte_wiederholung(eintrag: dict) -> None:
            # Häufung = Systemfehler, nicht Einzelfall: ab 3 gleichen
            # Beanstandungen wird eine Prompt-Diagnose beauftragt (einmalig).
            try:
                from services.auftraege import Auftragsbuch
                buch = Auftragsbuch()
                titel = f"Prompt-Diagnose {name}"
                if not any(a["titel"] == titel and a["status"] in ("offen", "in_arbeit", "vorgelegt")
                           for a in buch.liste("alle", limit=200)):
                    buch.erteilen("projektleitung", titel,
                                  f"Der Mitarbeiter {name} wurde 3x wegen desselben Punkts beanstandet: "
                                  f"'{eintrag.get('text', '')[:200]}'. Analysiere: Liegt es an seiner Rolle, "
                                  f"an der Auftragsformulierung oder am Prüf-Maßstab? Lies seine Rolle mit "
                                  f"list_sub_agents und mache einen konkreten Vorher/Nachher-Vorschlag. "
                                  f"Lege das Ergebnis mit submit_for_review (art dokument) vor.",
                                  quelle="jude")
            except Exception:
                pass

        ergebnis = self._zusammenfassen(self._pfad("sub_agent_lessons", name), beanstandung, quelle,
                                        self.MAX_LEHREN, bei_dritter_wiederholung=_dritte_wiederholung)
        return {"gemerkt": True, "lehren_gesamt": ergebnis["gesamt"]}

    def forget_lehren(self, name: str) -> dict:
        pfad = self._pfad("sub_agent_lessons", name)
        weg = len(self._lesen(pfad))
        pfad.unlink(missing_ok=True)
        return {"agent": name, "geloescht": weg}

    # --------------------------------------------------------------- Lob

    def lob(self, name: str) -> list[dict]:
        """Was an diesem Mitarbeiter schon einmal gelobt wurde."""
        return self._lesen(self._pfad("sub_agent_lob", name))

    def lob_merken(self, name: str, anerkennung: str, quelle: str = "Jude") -> dict:
        """Das Gegenstück zu ``lehre_merken``: bisher hielt das Team nur fest,
        was schiefging, nie was gut war – ein Mitarbeiter, der etwas richtig
        gemacht hat, bekam das nie bestaetigt und wusste beim naechsten Lauf
        nicht, woran er anknuepfen soll."""
        anerkennung = str(anerkennung).strip()
        if len(anerkennung) < 8:
            return {"gemerkt": False}
        ergebnis = self._zusammenfassen(self._pfad("sub_agent_lob", name), anerkennung, quelle,
                                        self.MAX_LEHREN)
        return {"gemerkt": True, "lob_gesamt": ergebnis["gesamt"]}

    def forget_lob(self, name: str) -> dict:
        pfad = self._pfad("sub_agent_lob", name)
        weg = len(self._lesen(pfad))
        pfad.unlink(missing_ok=True)
        return {"agent": name, "geloescht": weg}
