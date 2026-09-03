"""Auftragsbuch: Aufträge an Mitarbeiter mit verfolgbarem Lebenszyklus.

Bisher existierte ein Auftrag nur als Chat-Nachricht — niemand wusste, dass
"Banner bis Donnerstag" bestellt war (21 Läufe, 1 Vorlage). Jetzt hat jeder
Auftrag einen Zustand:

    offen -> in_arbeit -> vorgelegt -> abgenommen
                                   \\-> abgebrochen

Verknüpft mit der Abnahme (reviews.id über review_id): legt der Mitarbeiter
sein Ergebnis mit submit_for_review vor, wandert der Auftrag auf 'vorgelegt';
nimmt Tino ab, auf 'abgenommen'. Überfällige Aufträge findet der Wächter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services.database import connection

STATUS = ("offen", "in_arbeit", "vorgelegt", "abgenommen", "abgebrochen")
_SPALTEN = ("id", "created_at", "updated_at", "titel", "beschreibung", "agent",
            "quelle", "faellig_am", "status", "review_id", "runden")


class Auftragsbuch:
    def __init__(self):
        with connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS auftraege(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                titel TEXT NOT NULL,
                beschreibung TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL,
                quelle TEXT NOT NULL DEFAULT 'tino',
                faellig_am TEXT,
                status TEXT NOT NULL DEFAULT 'offen',
                review_id TEXT,
                runden INTEGER NOT NULL DEFAULT 0)""")

    @staticmethod
    def _jetzt() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _als_dict(zeile) -> dict:
        return dict(zip(_SPALTEN, zeile))

    def erteilen(self, agent: str, titel: str, beschreibung: str = "",
                 faellig_am: str | None = None, quelle: str = "tino") -> dict:
        titel = str(titel).strip()
        if not titel:
            raise ValueError("Der Auftrag braucht einen Titel.")
        eintrag_id = uuid.uuid4().hex[:12]
        jetzt = self._jetzt()
        with connection() as db:
            db.execute(
                "INSERT INTO auftraege(id, created_at, updated_at, titel, beschreibung,"
                " agent, quelle, faellig_am) VALUES(?,?,?,?,?,?,?,?)",
                (eintrag_id, jetzt, jetzt, titel, str(beschreibung).strip(),
                 str(agent).strip().casefold(), quelle, faellig_am))
        return {"id": eintrag_id, "agent": agent, "titel": titel,
                "status": "offen", "faellig_am": faellig_am}

    def liste(self, status: str = "offen", agent: str | None = None,
              limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with connection() as db:
            zeilen = db.execute(
                f"SELECT {', '.join(_SPALTEN)} FROM auftraege"
                " WHERE (? = 'alle' OR status = ?) AND (? IS NULL OR agent = ?)"
                " ORDER BY created_at ASC LIMIT ?",
                (status, status, agent, agent and agent.casefold(), limit)).fetchall()
        return [self._als_dict(z) for z in zeilen]

    def offene_fuer(self, agent: str) -> list[dict]:
        """Alles, was der Mitarbeiter noch schuldet (offen oder in Arbeit)."""
        with connection() as db:
            zeilen = db.execute(
                f"SELECT {', '.join(_SPALTEN)} FROM auftraege"
                " WHERE agent = ? AND status IN ('offen', 'in_arbeit')"
                " ORDER BY created_at ASC LIMIT 20",
                (str(agent).strip().casefold(),)).fetchall()
        return [self._als_dict(z) for z in zeilen]

    def ueberfaellig(self) -> list[dict]:
        with connection() as db:
            zeilen = db.execute(
                f"SELECT {', '.join(_SPALTEN)} FROM auftraege"
                " WHERE status IN ('offen', 'in_arbeit') AND faellig_am IS NOT NULL"
                " AND faellig_am < ? ORDER BY faellig_am ASC LIMIT 50",
                (self._jetzt(),)).fetchall()
        return [self._als_dict(z) for z in zeilen]

    def holen(self, auftrag_id: str) -> dict | None:
        with connection() as db:
            zeile = db.execute(
                f"SELECT {', '.join(_SPALTEN)} FROM auftraege WHERE id = ?",
                (str(auftrag_id).strip(),)).fetchone()
        return self._als_dict(zeile) if zeile else None

    def status_setzen(self, auftrag_id: str, status: str) -> dict:
        if status not in STATUS:
            raise ValueError(f"Unbekannter Status {status!r}. Erlaubt: {', '.join(STATUS)}")
        with connection() as db:
            geaendert = db.execute(
                "UPDATE auftraege SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._jetzt(), str(auftrag_id).strip())).rowcount
        if not geaendert:
            raise KeyError(f"Kein Auftrag {auftrag_id!r}.")
        return {"id": auftrag_id, "status": status}

    def verknuepfen(self, auftrag_id: str, review_id: str) -> dict:
        """Vorlage eingereicht: Auftrag zeigt auf die Review-Zeile, Status 'vorgelegt'."""
        with connection() as db:
            geaendert = db.execute(
                "UPDATE auftraege SET review_id = ?, status = 'vorgelegt',"
                " runden = runden + 1, updated_at = ? WHERE id = ?",
                (str(review_id).strip(), self._jetzt(), str(auftrag_id).strip())).rowcount
        if not geaendert:
            raise KeyError(f"Kein Auftrag {auftrag_id!r}.")
        return {"id": auftrag_id, "review_id": review_id, "status": "vorgelegt"}

    def abbrechen(self, auftrag_id: str) -> dict:
        return self.status_setzen(auftrag_id, "abgebrochen")

    def sync_von_review(self, review_id: str, ereignis: str) -> None:
        """Review-Ereignis auf den verknüpften Auftrag spiegeln.

        ereignis: 'freigegeben' -> bleibt 'vorgelegt' (liegt jetzt bei Tino),
        'abgenommen' -> 'abgenommen', 'revision' -> zurück auf 'in_arbeit'.
        Fehlt die Verknüpfung, passiert nichts.
        """
        ziel = {"abgenommen": "abgenommen", "revision": "in_arbeit"}.get(ereignis)
        if ziel is None:
            return
        with connection() as db:
            db.execute(
                "UPDATE auftraege SET status = ?, updated_at = ?"
                " WHERE review_id = ? AND status NOT IN ('abgenommen', 'abgebrochen')",
                (ziel, self._jetzt(), str(review_id).strip()))
