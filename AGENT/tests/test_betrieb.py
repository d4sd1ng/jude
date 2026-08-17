"""Der Betrieb des Teams: ehrliche Laufbewertung, Gedaechtnis, Abnahme.

Hintergrund dieser Tests ist eine Messung vom 13.08.2026: 11 von 11
Agentenlaeufen standen auf ``abgeschlossen``, obwohl 6 davon nichts zustande
gebracht hatten, und das Gedaechtnis des Akquise-Agenten bestand aus drei
Fehlermeldungen. Beides war nicht erkennbar. Die Tests halten fest, dass es
jetzt erkennbar ist.
"""

from __future__ import annotations

import pytest

from services.review import ReviewQueue
from services.team import SubAgentService


# --------------------------------------------------------- Laufbewertung

@pytest.mark.parametrize("ergebnis", [
    "Tool 'notion_query' fehlgeschlagen: HTTPError",
    "Tool 'gibtsnicht' nicht gefunden.",
    "Tool-Argumente müssen ein Objekt sein.",
    "Aktion konnte nicht vorgemerkt werden: keine Warteschlange",
])
def test_fehlschlag_wird_erkannt(ergebnis):
    assert SubAgentService._ist_fehlschlag(ergebnis)


def test_gutes_ergebnis_ist_kein_fehlschlag():
    assert not SubAgentService._ist_fehlschlag("3 Eintraege gefunden.")
    # Das Wort allein reicht nicht – nur das Muster der Werkzeugschicht zaehlt.
    assert not SubAgentService._ist_fehlschlag("Der Versand ist fehlgeschlagen gewesen")


def test_alles_gelaufen_ist_abgeschlossen():
    status, blocker = SubAgentService._bewerten(["notion_query", "notion_create"], [])
    assert status == "abgeschlossen" and blocker == []


def test_ohne_werkzeug_gilt_nicht_als_erledigt():
    """Mikes Lauf vom 13.08.: das Modell schrieb den Werkzeugaufruf als Text
    hin, statt ihn auszufuehren – nichts wurde abgelegt."""
    status, blocker = SubAgentService._bewerten([], [])
    assert status == "teilweise"
    assert "Kein Werkzeug benutzt" in blocker[0]


def test_alle_werkzeuge_gescheitert_ist_fehlgeschlagen():
    status, blocker = SubAgentService._bewerten(
        ["notion_query"], ["Tool 'notion_query' fehlgeschlagen: Netzwerk"])
    assert status == "fehlgeschlagen" and len(blocker) == 1


def test_teilerfolg_bleibt_sichtbar():
    status, blocker = SubAgentService._bewerten(
        ["notion_query", "scrape_public_url"], ["Tool 'scrape_public_url' fehlgeschlagen: 404"])
    assert status == "teilweise" and len(blocker) == 1


# ------------------------------------------------------------ Gedaechtnis

def test_stoerungen_kommen_nicht_ins_gedaechtnis(tmp_path, monkeypatch):
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    with pytest.raises(ValueError, match="Stoerungen"):
        dienst.remember("outreach", "Fehler beim Zugriff auf die Notion-Datenbank 'kontakte'.")
    with pytest.raises(ValueError, match="Stoerungen"):
        dienst.remember("outreach", "Die Verbindung zu 'api.notion.com' kann nicht hergestellt werden.")


def test_ergebnisse_kommen_ins_gedaechtnis(tmp_path, monkeypatch):
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    dienst.remember("outreach", "Malis Pflegedienst Marburg: info@malis-marburg.de aus dem Impressum.")
    assert len(dienst.notes("outreach")) == 1


# ---------------------------------------------------------------- Abnahme

def test_abnahme_haelt_den_mitarbeiter_nicht_auf():
    queue = ReviewQueue()
    vorlage = queue.vorlegen("social", "post", "LinkedIn: Betreuungsbericht per Diktat",
                             "Text...", person="Mike")
    assert vorlage["vorgelegt"] is True
    # Solange niemand entschieden hat, liegt beim Mitarbeiter nichts an.
    assert queue.offene_revisionen("social") == []


def test_nichts_erreicht_tino_ohne_judes_pruefung():
    """Jude ist Chef: was er nicht freigegeben hat, taucht bei Tino nicht auf."""
    queue = ReviewQueue()
    vorlage = queue.vorlegen("social", "post", "Ungeprüfter Beitrag", "Text...")

    assert queue.zeigen(vorlage["id"])["status"] == "pruefung"
    assert any(e["id"] == vorlage["id"] for e in queue.zur_pruefung())
    assert not any(e["id"] == vorlage["id"] for e in queue.liste("offen"))

    queue.freigeben(vorlage["id"], "Jude: passt.")
    assert any(e["id"] == vorlage["id"] for e in queue.liste("offen"))


def test_judes_revision_erreicht_tino_nie():
    queue = ReviewQueue()
    vorlage = queue.vorlegen("content", "dokument", "Zu werblich", "Text...")
    queue.revision(vorlage["id"], "Jude: 'Lösung' ist Werbedeutsch.")

    assert not any(e["id"] == vorlage["id"] for e in queue.liste("offen"))
    assert [e["id"] for e in queue.offene_revisionen("content")] == [vorlage["id"]]


def test_revision_geht_an_den_verfasser_zurueck():
    queue = ReviewQueue()
    vorlage = queue.vorlegen("social", "post", "Zu werblich", "Text...")
    queue.freigeben(vorlage["id"])
    queue.revision(vorlage["id"], "Kein Preis im ersten Absatz.")

    offen = queue.offene_revisionen("social")
    assert [e["id"] for e in offen] == [vorlage["id"]]
    assert offen[0]["anmerkung"] == "Kein Preis im ersten Absatz."
    assert queue.offene_revisionen("content") == []      # betrifft nur den Verfasser

    # Nachgearbeitet: dieselbe Zeile, eine Runde weiter, zurueck zu Judes Pruefung.
    danach = queue.erledigt(vorlage["id"], inhalt="Neue Fassung ohne Preis.")
    assert danach["status"] == "pruefung" and danach["runde"] == 2
    assert danach["inhalt"] == "Neue Fassung ohne Preis."
    assert queue.offene_revisionen("social") == []      # liegt dem Verfasser nicht mehr an
    assert not any(e["id"] == vorlage["id"] for e in queue.liste("offen"))


def test_ueberarbeitung_legt_keine_zweite_vorlage_an(tmp_path, monkeypatch):
    """Ohne das blieb die beanstandete Fassung fuer immer auf 'revision' und
    wurde dem Mitarbeiter bei jedem weiteren Lauf erneut vorgelegt."""
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    queue = ReviewQueue()
    vorher = len(queue.liste("alle", limit=200))
    vorlage = queue.vorlegen("social", "post", "Erste Fassung", "Text A")
    queue.revision(vorlage["id"], "Zu werblich.")
    queue.erledigt(vorlage["id"], inhalt="Text B", titel="Zweite Fassung")

    alle = queue.liste("alle", limit=200)
    assert len(alle) == vorher + 1                     # eine Zeile, nicht zwei
    aktuell = queue.zeigen(vorlage["id"])
    assert aktuell["titel"] == "Zweite Fassung" and aktuell["runde"] == 2


def test_revision_ohne_anmerkung_wird_abgelehnt():
    """Ohne Begruendung liefert der Mitarbeiter dasselbe noch einmal."""
    queue = ReviewQueue()
    vorlage = queue.vorlegen("content", "dokument", "Langformat")
    with pytest.raises(ValueError):
        queue.revision(vorlage["id"], "   ")


def test_abgenommenes_taucht_nicht_mehr_auf():
    queue = ReviewQueue()
    vorlage = queue.vorlegen("content", "dokument", "Langformat")
    queue.freigeben(vorlage["id"])
    queue.abnehmen(vorlage["id"])
    assert not any(e["id"] == vorlage["id"] for e in queue.liste("offen"))
    assert queue.zeigen(vorlage["id"])["status"] == "abgenommen"


def test_unbekannte_vorlage():
    with pytest.raises(KeyError):
        ReviewQueue().zeigen("gibtsnicht")


def test_unbekannte_art_wird_abgelehnt():
    with pytest.raises(ValueError):
        ReviewQueue().vorlegen("social", "newsletterchen", "Titel")


# ------------------------------------------------------------- Dienstplan

from datetime import datetime, timedelta, timezone           # noqa: E402

from services.scheduler import SchedulerService              # noqa: E402

BERLIN = timezone(timedelta(hours=2))


def _taeglich(at: str, last_run: str | None = None, **rest) -> dict:
    return {"id": "t1", "name": "Test", "enabled": True,
            "schedule": {"type": "daily", "at": at}, "last_run": last_run, **rest}


def test_taeglich_feuert_nach_der_uhrzeit():
    jetzt = datetime(2026, 8, 14, 7, 5, tzinfo=BERLIN)
    assert SchedulerService._is_due(_taeglich("07:00"), jetzt)
    assert not SchedulerService._is_due(_taeglich("08:00"), jetzt)


def test_kurz_nach_mitternacht_laeuft_nicht_endlos():
    """00:30 Ortszeit wird als 22:30 UTC des Vortags abgelegt. Vor der
    Zeitzonen-Umrechnung galt die Aufgabe danach bis 02:00 als nicht gelaufen
    und feuerte im 30-Sekunden-Takt erneut."""
    jetzt = datetime(2026, 8, 14, 1, 0, tzinfo=BERLIN)
    aufgabe = _taeglich("00:30", last_run="2026-08-13T22:35:00+00:00")
    assert not SchedulerService._is_due(aufgabe, jetzt)


def test_gestern_gelaufen_ist_heute_wieder_faellig():
    jetzt = datetime(2026, 8, 14, 7, 5, tzinfo=BERLIN)
    assert SchedulerService._is_due(_taeglich("07:00", "2026-08-13T05:00:00+00:00"), jetzt)


def test_fehlgeschlagener_lauf_wird_nachgeholt():
    """Der Netzaussetzer am 13.08. kostete den ganzen Tageslauf – die Aufgabe
    galt als erledigt und war bis zum Folgetag gesperrt."""
    jetzt = datetime(2026, 8, 14, 9, 0, tzinfo=BERLIN)
    heute_gelaufen = "2026-08-14T06:00:00+00:00"
    assert not SchedulerService._is_due(_taeglich("07:00", heute_gelaufen), jetzt)
    faellig = _taeglich("07:00", heute_gelaufen, retry_at="2026-08-14T06:30:00+00:00")
    assert SchedulerService._is_due(faellig, jetzt)
    noch_nicht = _taeglich("07:00", heute_gelaufen, retry_at="2026-08-14T09:30:00+00:00")
    assert not SchedulerService._is_due(noch_nicht, jetzt)


def test_abgeschaltete_aufgabe_bleibt_stumm():
    jetzt = datetime(2026, 8, 14, 9, 0, tzinfo=BERLIN)
    aufgabe = _taeglich("07:00", retry_at="2026-08-14T06:30:00+00:00")
    aufgabe["enabled"] = False
    assert not SchedulerService._is_due(aufgabe, jetzt)


# ---------------------------------------------------------- Aus Fehlern lernen

def test_gleiche_beanstandung_wird_nicht_gesammelt(tmp_path, monkeypatch):
    """Derselbe Einwand darf nicht hundertmal im Prompt stehen – er wird
    zusammengefasst und mitgezaehlt, damit sein Gewicht sichtbar bleibt."""
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    for _ in range(3):
        dienst.lehre_merken("social", "Kein Werbedeutsch: 'Lösung' und 'optimieren'.")
    dienst.lehre_merken("social", "Kein Preis in der Erstansprache.")

    gelernt = dienst.lehren("social")
    assert len(gelernt) == 2
    assert gelernt[0]["anzahl"] == 3          # das Haeufigste steht oben
    assert gelernt[1]["anzahl"] == 1


def test_lehren_bleiben_beim_verfasser(tmp_path, monkeypatch):
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    dienst.lehre_merken("social", "Kein Hashtag-Teppich.")
    assert dienst.lehren("content") == []


def test_belanglose_beanstandung_wird_verworfen(tmp_path, monkeypatch):
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    assert dienst.lehre_merken("social", "nein")["gemerkt"] is False
    assert dienst.lehren("social") == []


# ------------------------------------------------------- Hinweis an Kollegen

def _dienst(tmp_path, monkeypatch, agenten: dict):
    monkeypatch.setattr("services.team.DATA_DIR", tmp_path)
    dienst = SubAgentService.__new__(SubAgentService)
    dienst.path = tmp_path / "sub_agents.json"
    dienst._save(agenten)
    return dienst


def test_fund_landet_im_gedaechtnis_des_kollegen(tmp_path, monkeypatch):
    """Ein Fund ist nur etwas wert, wenn er den erreicht, der ihn braucht."""
    dienst = _dienst(tmp_path, monkeypatch, {
        "beobachter": {"name": "beobachter", "person": "Silke", "role": "x", "skills": []},
        "social": {"name": "social", "person": "Mike", "role": "x", "skills": []}})
    werkzeug = dienst._hinweis_tool({"name": "beobachter", "person": "Silke"})

    ergebnis = werkzeug.func("social", "Anbieter XY wirbt seit dem 10.08. mit Diktat für "
                                       "Pflegeberichte – dasselbe Modul, das wir anbieten.")
    assert ergebnis["zugestellt"] is True and ergebnis["an"] == "Mike"
    notiz = dienst.notes("social")[0]["note"]
    assert notiz.startswith("[Hinweis von Silke]") and "Anbieter XY" in notiz
    assert dienst.notes("beobachter") == []      # beim Absender bleibt nichts liegen


def test_hinweis_an_unbekannten_kollegen(tmp_path, monkeypatch):
    dienst = _dienst(tmp_path, monkeypatch,
                     {"beobachter": {"name": "beobachter", "role": "x", "skills": []}})
    werkzeug = dienst._hinweis_tool({"name": "beobachter", "person": "Silke"})
    with pytest.raises(ValueError, match="Kein Kollege"):
        werkzeug.func("marketing", "Ein ausreichend langer Hinweis mit Substanz dahinter.")


def test_duenner_hinweis_wird_abgewiesen(tmp_path, monkeypatch):
    """'Interessant' hilft dem Kollegen nicht – Quelle und Kernaussage müssen mit."""
    dienst = _dienst(tmp_path, monkeypatch, {
        "beobachter": {"name": "beobachter", "role": "x", "skills": []},
        "social": {"name": "social", "role": "x", "skills": []}})
    werkzeug = dienst._hinweis_tool({"name": "beobachter", "person": "Silke"})
    with pytest.raises(ValueError, match="zu duenn"):
        werkzeug.func("social", "interessant")
