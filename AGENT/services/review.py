"""Abnahme fertiger Arbeitsergebnisse — ohne den Mitarbeiter aufzuhalten.

Abzugrenzen von :mod:`services.confirmations`: jene Warteschlange **blockiert**
eine Aktion, bis Tino zustimmt (E-Mail senden, Datei löschen, Code pushen). Hier
geht es um etwas anderes — ein Mitarbeiter meldet ein fertiges Erzeugnis zur
Abnahme und **arbeitet sofort weiter**. Das Ergebnis liegt bei Tino, nicht beim
Agenten; es geht ihn erst wieder etwas an, wenn eine Revision zurückkommt.

Ablauf — Jude ist Chef und sieht alles vor Tino:

1. Mitarbeiter meldet fertig  ->  ``vorlegen()``   (Status ``pruefung``)
2. Jude prüft als Vorgesetzter:
   ``freigeben()``  -> Status ``offen``     (jetzt erst bei Tino)
   ``revision()``   -> Status ``revision``  (zurück an den Mitarbeiter)
3. Tino nimmt ab              ->  ``abnehmen()``  (Status ``abgenommen``)
   oder fordert Überarbeitung ->  ``revision()``
4. Beim nächsten Lauf sieht der Mitarbeiter seine offenen Revisionen im
   Systemprompt und arbeitet sie ab.

Tinos Liste zeigt ausschließlich ``offen`` — was Jude zurückgewiesen hat,
erreicht ihn gar nicht erst.

Nichts hiervon veröffentlicht oder versendet etwas — das bleibt Tinos Sache.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from services.database import connection

#: Was ein Mitarbeiter vorlegen kann.
ARTEN = {"post", "email", "newsletter", "sequenz", "dokument", "recherche",
         "grafik", "sonstiges"}

#: Die vier Arten, die im Cockpit eine eigene Ampel bekommen – alles andere
#: sammelt sich in der Abnahme-Liste im System-Tab.
COCKPIT_ARTEN = ("grafik", "post", "email", "newsletter")


class ReviewQueue:
    @staticmethod
    def _ensure() -> None:
        with connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reviews(
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                agent TEXT NOT NULL, person TEXT,
                art TEXT NOT NULL, titel TEXT NOT NULL,
                inhalt TEXT NOT NULL DEFAULT '', quelle TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'offen',
                anmerkung TEXT NOT NULL DEFAULT '',
                verlauf TEXT NOT NULL DEFAULT '',
                entschieden_am TEXT, runde INTEGER NOT NULL DEFAULT 1)""")
            # Bestandsmigration: verlauf-Spalte nachrüsten (haelt die
            # Beanstandungs-Historie; erledigt() loeschte sie frueher).
            spalten = [z[1] for z in db.execute("PRAGMA table_info(reviews)")]
            if "verlauf" not in spalten:
                db.execute("ALTER TABLE reviews ADD COLUMN verlauf TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_agent ON reviews(agent)")

    def __init__(self):
        self._ensure()

    # ---------------------------------------------------------- Mitarbeiter

    def vorlegen(self, agent: str, art: str, titel: str, inhalt: str = "",
                 quelle: str = "", person: str | None = None) -> dict:
        """Ein fertiges Erzeugnis zur Abnahme melden. Blockiert nicht."""
        art = str(art).strip().lower()
        if art not in ARTEN:
            raise ValueError(f"Art muss eine von {sorted(ARTEN)} sein.")
        titel = str(titel).strip()
        if not titel:
            raise ValueError("Ohne Titel kann Tino nichts abnehmen.")
        eintrag_id = uuid.uuid4().hex[:12]
        with connection() as db:
            db.execute(
                "INSERT INTO reviews(id,created_at,agent,person,art,titel,inhalt,quelle,status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (eintrag_id, datetime.now(timezone.utc).isoformat(), agent, person,
                 art, titel[:300], str(inhalt)[:12000], str(quelle)[:500], "pruefung"))
        return {"vorgelegt": True, "id": eintrag_id, "art": art, "titel": titel[:120],
                "hinweis": "Geht zur Prüfung an Jude. Arbeite weiter."}

    def zur_pruefung(self, limit: int = 50) -> list[dict]:
        """Was beim Chef liegt und noch nicht bei Tino ist (älteste zuerst)."""
        limit = max(1, min(int(limit), 200))
        with connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,created_at,agent,person,art,titel,quelle,status,anmerkung,runde,"
                "substr(inhalt,1,700) AS auszug, length(inhalt) AS laenge "
                "FROM reviews WHERE status='pruefung' "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,))]

    def freigeben(self, review_id: str, anmerkung: str = "") -> dict:
        """Jude reicht nach oben durch – ab jetzt sichtbar für Tino."""
        return self._entscheiden(review_id, "offen", anmerkung)

    def offene_revisionen(self, agent: str) -> list[dict]:
        """Was dieser Mitarbeiter überarbeiten soll — kommt in seinen Prompt."""
        with connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,art,titel,anmerkung,runde FROM reviews "
                "WHERE agent=? AND status='revision' ORDER BY created_at", (agent,))]

    # ---------------------------------------------------------------- Tino

    def liste(self, status: str = "offen", limit: int = 50, art: str | None = None) -> list[dict]:
        art = (art or "").strip().lower() or None
        with connection() as db:
            # Auszug statt Volltext: bei 50 Vorlagen à 12.000 Zeichen waere die
            # Liste sonst ein halbes Megabyte. Den Rest holt die Oberflaeche
            # ueber ``zeigen`` nach, wenn Tino ihn aufklappt.
            return [dict(row) for row in db.execute(
                "SELECT id,created_at,agent,person,art,titel,quelle,status,anmerkung,runde,"
                "substr(inhalt,1,700) AS auszug, length(inhalt) AS laenge "
                "FROM reviews WHERE (?='alle' OR status=?) AND (? IS NULL OR art=?) "
                "ORDER BY created_at DESC LIMIT ?",
                (status, status, art, art, max(1, min(int(limit), 200))))]

    def zeigen(self, review_id: str) -> dict:
        with connection() as db:
            row = db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if row is None:
            raise KeyError("Unbekannte Vorlage.")
        return dict(row)

    def abnehmen(self, review_id: str, anmerkung: str = "") -> dict:
        result = self._entscheiden(review_id, "abgenommen", anmerkung)
        try:
            from services.auftraege import Auftragsbuch
            Auftragsbuch().sync_von_review(review_id, "abgenommen")
        except Exception:
            pass
        return result

    def revision(self, review_id: str, anmerkung: str) -> dict:
        """Zurück an den Mitarbeiter. Die Anmerkung ist Pflicht — ohne sie weiß
        er nicht, was zu ändern ist, und liefert dasselbe noch einmal."""
        if not str(anmerkung).strip():
            raise ValueError("Für eine Revision wird eine Anmerkung benötigt.")
        result = self._entscheiden(review_id, "revision", anmerkung)
        try:
            from services.auftraege import Auftragsbuch
            Auftragsbuch().sync_von_review(review_id, "revision")
        except Exception:
            pass
        return result

    def erledigt(self, review_id: str, inhalt: str | None = None,
                 titel: str | None = None) -> dict:
        """Der Mitarbeiter hat die Revision eingearbeitet.

        Zurück auf ``pruefung``, nicht direkt zu Tino: auch die zweite Fassung
        sieht Jude zuerst. Es entsteht keine neue Zeile – dieselbe wandert eine
        Runde weiter, damit nachvollziehbar bleibt, wie oft etwas zurückkam.
        """
        with connection() as db:
            zeile = db.execute("SELECT id, status, runde, anmerkung FROM reviews WHERE id=?",
                              (review_id,)).fetchone()
            if zeile is None:
                raise KeyError("Unbekannte Vorlage.")
            if zeile["status"] == "abgenommen":
                raise ValueError("Diese Vorlage ist bereits abgenommen – lege eine neue Vorlage an.")
            db.execute(
                "UPDATE reviews SET "
                "verlauf = CASE WHEN anmerkung != '' "
                "THEN verlauf || '— Runde ' || runde || ': ' || anmerkung || char(10) "
                "ELSE verlauf END, "
                "status='pruefung', runde=runde+1, anmerkung='', "
                "inhalt=COALESCE(?,inhalt), titel=COALESCE(?,titel), entschieden_am=NULL "
                "WHERE id=?",
                (str(inhalt)[:12000] if inhalt else None,
                 str(titel)[:300] if titel else None, review_id))
        return self.zeigen(review_id)

    def _entscheiden(self, review_id: str, status: str, anmerkung: str) -> dict:
        with connection() as db:
            treffer = db.execute("SELECT id FROM reviews WHERE id=?", (review_id,)).fetchone()
            if treffer is None:
                raise KeyError("Unbekannte Vorlage.")
            db.execute("UPDATE reviews SET status=?, anmerkung=?, entschieden_am=? WHERE id=?",
                       (status, str(anmerkung)[:2000], datetime.now(timezone.utc).isoformat(),
                        review_id))
        return self.zeigen(review_id)

    def zusammenfassung(self) -> dict:
        with connection() as db:
            zeilen = db.execute("SELECT status, COUNT(*) c FROM reviews GROUP BY status").fetchall()
        return {row["status"]: row["c"] for row in zeilen}

    def offen_nach_art(self) -> dict:
        """Wie viel je Art auf Tino wartet – speist die Ampeln im Cockpit.

        Jede Art ist immer enthalten, auch mit 0: die Ampel soll rot leuchten,
        nicht verschwinden.
        """
        with connection() as db:
            zeilen = db.execute(
                "SELECT art, COUNT(*) c FROM reviews WHERE status='offen' GROUP BY art").fetchall()
        gezaehlt = {row["art"]: row["c"] for row in zeilen}
        return {art: gezaehlt.get(art, 0) for art in COCKPIT_ARTEN}
