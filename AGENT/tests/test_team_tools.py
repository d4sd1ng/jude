"""submit_for_review <-> Auftragsbuch-Verknuepfung.

Beobachtet 07.09.2026: auftrag_id war rein optional und wurde fast immer
vergessen - von 38 an einem Tag abgenommenen Vorlagen waren nur 9 ueberhaupt
mit einem Auftrag verknuepft. Die 44 offenen Auftraege blieben trotzdem
offen, obwohl ihre Arbeit laengst abgenommen war ("all reviews wasnt related
to the open jobs"). Gleiches Prinzip wie die bereits bestehende Absicherung
fuer 'ueberarbeitet' (vergessene/erfundene Revisions-ID): fehlt oder passt
auftrag_id nicht, wird automatisch der aelteste eigene offene Auftrag
verknuepft.
"""

from __future__ import annotations

from unittest.mock import Mock

from core.tool_registry import Tool, ToolRegistry
from services.auftraege import Auftragsbuch
from services.team import SubAgentService
from services.team_tools import ToolFactory


def _service(tmp_path):
    registry = ToolRegistry()
    registry.register(Tool("noop", "d", lambda: "ok", {"type": "object", "properties": {}}))
    svc = SubAgentService(registry, Mock())
    svc.path = tmp_path / "agents.json"
    svc.create("Tester", "Rolle.", ["noop"])
    return svc


def test_ohne_auftrag_id_wird_der_aelteste_offene_auftrag_verknuepft(tmp_path):
    svc = _service(tmp_path)
    spec = svc.get("tester")
    buch = Auftragsbuch()
    aeltester = buch.erteilen("tester", "Erster Auftrag")
    juengerer = buch.erteilen("tester", "Zweiter Auftrag")

    tool = ToolFactory(svc).review_tool(spec)
    tool.func(art="dokument", titel="Testbeitrag", inhalt="x" * 320)

    assert buch.holen(aeltester["id"])["status"] == "vorgelegt"
    assert buch.holen(juengerer["id"])["status"] == "offen"


def test_erfundene_auftrag_id_faellt_auf_eigenen_offenen_auftrag_zurueck(tmp_path):
    svc = _service(tmp_path)
    spec = svc.get("tester")
    buch = Auftragsbuch()
    richtiger = buch.erteilen("tester", "Richtiger Auftrag")

    tool = ToolFactory(svc).review_tool(spec)
    tool.func(art="dokument", titel="Testbeitrag", inhalt="x" * 320, auftrag_id="erfunden-123")

    assert buch.holen(richtiger["id"])["status"] == "vorgelegt"


def test_ohne_offenen_auftrag_bleibt_die_vorlage_trotzdem_moeglich(tmp_path):
    svc = _service(tmp_path)
    spec = svc.get("tester")

    tool = ToolFactory(svc).review_tool(spec)
    ergebnis = tool.func(art="dokument", titel="Testbeitrag", inhalt="x" * 320)
    assert ergebnis.get("id")


def test_hinweisen_loest_vornamen_auf_agentenschluessel_auf(tmp_path):
    """Rollentexte nennen Kollegen ueberall beim Vornamen ('Heike/designer'),
    Modelle sprechen sie darum auch bei inform_colleague-Aufrufen so an,
    obwohl das Werkzeug den Mitarbeiter-Schluessel braucht. Beobachtet
    07.09.2026: inform_colleague('heike', ...) schlug fehl, obwohl 'designer'
    eindeutig gemeint war."""
    svc = _service(tmp_path)
    svc.create("Designer", "Rolle.", ["noop"], person="Heike")
    absender = svc.get("tester")

    tool = ToolFactory(svc).hinweis_tool(absender)
    ergebnis = tool.func(kollege="heike", hinweis="x" * 25)

    assert ergebnis["zugestellt"] is True
    assert ergebnis["an"] == "Heike"
    notizen = svc.notes("designer")
    assert any("Hinweis von Tester" in n.get("note", "") for n in notizen)


def test_zum_auftrag_gehoerige_revision_hat_vorrang_vor_gleicher_art(tmp_path):
    """Vor dem Fix (07.09.2026) landete jede Vorlage derselben Art desselben
    Mitarbeiters in der erstbesten offenen Revision - unabhaengig vom Inhalt.
    Ein Beitragstext geriet so in eine voellig unabhaengige, mehrere Runden
    alte Onepager-Revision, nur weil beide 'dokument' waren. Der konkret
    verknuepfte Auftrag muss Vorrang vor einer blossen Art-Uebereinstimmung
    haben."""
    from services.database import connection
    from services.review import ReviewQueue

    svc = _service(tmp_path)
    spec = svc.get("tester")
    buch = Auftragsbuch()
    ReviewQueue()  # legt die Tabelle an

    a_egal = buch.erteilen("tester", "Unabhaengiger Auftrag")
    a_ziel = buch.erteilen("tester", "Zielauftrag")

    with connection() as db:
        db.execute(
            "INSERT INTO reviews(id, created_at, agent, art, titel, quelle, inhalt, status, runde) "
            "VALUES('rev_egal','2026-09-01T00:00:00',?,'dokument','Unabhaengig','tester','x','revision',2)",
            (spec["name"],))
        db.execute(
            "INSERT INTO reviews(id, created_at, agent, art, titel, quelle, inhalt, status, runde) "
            "VALUES('rev_ziel','2026-09-02T00:00:00',?,'dokument','Ziel','tester','x','revision',2)",
            (spec["name"],))
    buch.verknuepfen(a_ziel["id"], "rev_ziel")

    tool = ToolFactory(svc).review_tool(spec)
    tool.func(art="dokument", titel="Ueberarbeiteter Zieltext", inhalt="x" * 320,
             auftrag_id=a_ziel["id"])

    with connection() as db:
        ziel = dict(db.execute("SELECT status, runde FROM reviews WHERE id='rev_ziel'").fetchone())
        egal = dict(db.execute("SELECT status, runde FROM reviews WHERE id='rev_egal'").fetchone())
    assert ziel["status"] == "pruefung" and ziel["runde"] == 3
    assert egal["status"] == "revision" and egal["runde"] == 2  # unangetastet
    assert buch.holen(a_ziel["id"])["status"] == "vorgelegt"
    assert buch.holen(a_egal["id"])["status"] == "offen"  # unangetastet
