"""ReviewQueue: entschieden_von unterscheidet, WER eine Entscheidung traf.

Beobachtet 07.09.2026: status='revision' sah fuer Renates Zurueckweisung und
Tinos eigene identisch aus - die GUI behalf sich mit einem rein clientseitigen
Set, das bei jedem Reload wieder leer war ("every bullshit is again in my
queue"). Diese Tests sichern die serverseitige, dauerhafte Variante.
"""

from __future__ import annotations

from services.review import ReviewQueue


def _vorlage(queue: ReviewQueue) -> str:
    v = queue.vorlegen("content", "dokument", "Testbeitrag", "x" * 320)
    return v["id"]


def test_tinos_revision_setzt_entschieden_von():
    queue = ReviewQueue()
    rid = _vorlage(queue)
    queue.revision(rid, "Zu allgemein", akteur="tino")
    zeile = queue.zeigen(rid)
    assert zeile["status"] == "revision"
    assert zeile["entschieden_von"] == "tino"


def test_renates_revision_setzt_ihren_eigenen_namen():
    queue = ReviewQueue()
    rid = _vorlage(queue)
    queue.revision(rid, "Werbedeutsch drin", akteur="projektleitung")
    zeile = queue.zeigen(rid)
    assert zeile["entschieden_von"] == "projektleitung"


def test_erneutes_vorlegen_setzt_entschieden_von_zurueck():
    """Reicht der Mitarbeiter eine ueberarbeitete Fassung ein, ist das eine
    neue Entscheidungsrunde - die alte Zuordnung darf nicht am neuen,
    ungeprueften Text kleben bleiben."""
    queue = ReviewQueue()
    rid = _vorlage(queue)
    queue.revision(rid, "Zu allgemein", akteur="tino")
    queue.erledigt(rid, inhalt="x" * 320)
    zeile = queue.zeigen(rid)
    assert zeile["status"] == "pruefung"
    assert zeile["entschieden_von"] == ""


def test_liste_gibt_entschieden_von_mit():
    queue = ReviewQueue()
    rid = _vorlage(queue)
    queue.revision(rid, "Zu allgemein", akteur="tino")
    treffer = [v for v in queue.liste(status="alle", limit=200) if v["id"] == rid]
    assert treffer and treffer[0]["entschieden_von"] == "tino"


def test_status_aktiv_laesst_abgeschlossenes_weg():
    """'aktiv' ist fuer die Bestaetigungsliste gedacht: pruefung/offen/revision,
    NICHT abgenommen/abgebrochen. Ohne diese Stufe holte 'alle' bei jedem
    8s-Refresh saemtliche jemals abgenommenen Vorlagen mit (43 von 55 Zeilen
    gemessen 07.09.2026) - reine Ballastfracht ("cleaner? yeah right...")."""
    queue = ReviewQueue()
    offen_id = _vorlage(queue)
    abgenommen_id = _vorlage(queue)
    queue.abnehmen(abgenommen_id, akteur="tino")

    aktiv = {v["id"] for v in queue.liste(status="aktiv", limit=200)}
    assert offen_id in aktiv
    assert abgenommen_id not in aktiv


def test_offene_revisionen_gibt_verlauf_mit():
    """Beobachtet 07.09.2026: ein Stueck in Runde 11 zeigte dem Mitarbeiter
    beim naechsten Lauf nur die aktuelle Anmerkung ('zu wenig'), nie die
    Historie der zehn Runden davor - offene_revisionen() (das den Prompt
    speist) selektierte 'verlauf' gar nicht erst."""
    queue = ReviewQueue()
    rid = _vorlage(queue)
    queue.revision(rid, "Erster Kritikpunkt", akteur="tino")
    queue.erledigt(rid, inhalt="x" * 320)
    queue.revision(rid, "Zweiter Kritikpunkt", akteur="tino")

    treffer = [r for r in queue.offene_revisionen("content") if r["id"] == rid]
    assert treffer
    assert "Erster Kritikpunkt" in treffer[0]["verlauf"]
