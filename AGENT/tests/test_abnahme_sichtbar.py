"""Nichts, was auf Abnahme wartet, darf unsichtbar sein.

Am 02.09.2026 lag eine Vorlage der Art ``dokument`` seit dem 17.08. auf
``offen`` und war in der Oberfläche nirgends zu sehen. Zwei Ursachen, beide
hier festgenagelt:

1. ``offen_nach_art`` zählte nur die vier Cockpit-Arten. Ein offenes
   ``dokument`` tauchte in keiner Ampel auf.
2. Der Klick auf eine Ampel führte per ``show('System')`` auf eine Seite, auf
   der das Listenelement ``#reviews`` gar nicht liegt – es steht auf dem
   Schreibtisch. Die Liste wurde damit nie geladen.

Die zweite Prüfung liest bewusst die Auslieferungsdateien statt die Logik
nachzubauen: der Fehler bestand genau darin, dass Seite und Element
auseinanderliefen, und das sieht man nur an den echten Dateien.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.review import ARTEN, COCKPIT_ARTEN, ReviewQueue

STATISCH = Path(__file__).resolve().parents[1] / "web" / "static"


def test_offen_nach_art_kennt_jede_art():
    """Keine Art darf aus der Zählung fallen – sonst ist sie unsichtbar."""
    gezaehlt = ReviewQueue().offen_nach_art()
    fehlend = ARTEN - set(gezaehlt)
    assert not fehlend, f"Arten ohne Zählung und damit ohne Ampel: {sorted(fehlend)}"
    for art in COCKPIT_ARTEN:
        assert art in gezaehlt, f"Feste Cockpit-Ampel {art} fehlt"


def test_ampelklick_fuehrt_auf_die_seite_mit_der_liste():
    """Die Ampel muss auf die Seite führen, auf der #reviews wirklich steht."""
    html = (STATISCH / "index.html").read_text(encoding="utf-8")
    js = (STATISCH / "app.js").read_text(encoding="utf-8")

    # Auf welcher Seite liegt #reviews?
    abschnitte = [(m.start(), m.group(1))
                  for m in re.finditer(r'<section[^>]*data-page="([^"]+)"', html)]
    treffer = re.search(r'id="reviews"', html)
    assert treffer, "#reviews fehlt in index.html"
    seite_der_liste = [name for start, name in abschnitte if start < treffer.start()][-1]

    # Wohin schickt der Ampelklick?
    umschalter = re.search(r"function revToggle\(art\)\{(.+?)\n", js, re.S)
    assert umschalter, "revToggle nicht gefunden – wurde die Ampel-Logik umgebaut?"
    ziel = re.search(r"show\('([^']+)'\)", umschalter.group(1))
    assert ziel, "revToggle wechselt die Seite nicht"
    assert ziel.group(1) == seite_der_liste, (
        f"Ampel fuehrt nach '{ziel.group(1)}', die Liste steht aber auf "
        f"'{seite_der_liste}' – genau der Fehler vom 02.09.2026")

    # Und die Zielseite muss die Liste auch laden.
    zweig = re.search(r"if\(p==='" + seite_der_liste + r"'\)\{([^}]*)\}", js)
    assert zweig and "loadReviews()" in zweig.group(1), (
        f"Seite '{seite_der_liste}' ruft loadReviews() nicht auf")


def test_jede_art_ohne_feste_ampel_bekommt_einen_platz():
    """Für die Arten ohne feste Ampel muss ein Container existieren."""
    html = (STATISCH / "index.html").read_text(encoding="utf-8")
    js = (STATISCH / "app.js").read_text(encoding="utf-8")
    ohne_feste = sorted(ARTEN - set(COCKPIT_ARTEN))
    assert ohne_feste, "Testannahme hinfaellig: alle Arten haben feste Ampeln"
    assert 'id="revExtra"' in html, "Container fuer die uebrigen Arten fehlt"
    assert "#revExtra" in js, "ampeln() fuellt den Container nicht"
