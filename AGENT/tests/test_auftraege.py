"""Auftragsbuch: Dopplungssperre in auftrag_erteilen.

Beobachtet 07.09.2026: der Wächter/projektleitung erkannte dieselbe Lücke
wiederholt neu und legte jedes Mal einen weiteren Auftrag an, statt den
vorhandenen offenen zu nutzen - Ergebnis waren 45 gleichzeitig unerledigte
Aufträge, allein 23 für "social" (u.a. acht separate "Wochen-Beitrag
fehlt"-Aufträge für dieselbe Lücke). Diese Tests sichern die harte Sperre.
"""

from __future__ import annotations

import pytest

from core.tool_registry import ToolRegistry
from services.auftraege import Auftragsbuch


class _FakeDienst:
    """Ersetzt SubAgentService: kein echter Agenten-Lauf, keine Kosten."""

    REDAKTEUR = "redakteur"  # auftrag_erteilen prueft dieses Attribut direkt

    def __init__(self, *_a, **_k):
        pass

    def list(self):
        return [{"name": "social"}, {"name": "redakteur"}]

    def run(self, *_a, **_k):
        raise AssertionError("run() sollte hier nicht aufgerufen werden (sofort_starten=False)")


def _auftrag_erteilen(monkeypatch):
    import services.team as team_mod
    monkeypatch.setattr(team_mod, "SubAgentService", _FakeDienst)
    from tools import chef
    registry = ToolRegistry()
    chef.register_context(registry, router=object())
    return registry.tools["auftrag_erteilen"].func


def test_unerledigt_fuer_zaehlt_offen_in_arbeit_vorgelegt_nicht_abgenommen_oder_abgebrochen():
    buch = Auftragsbuch()
    a = buch.erteilen("social", "Test A")
    b = buch.erteilen("social", "Test B")
    buch.status_setzen(b["id"], "vorgelegt")
    c = buch.erteilen("social", "Test C")
    buch.status_setzen(c["id"], "abgenommen")
    d = buch.erteilen("social", "Test D")
    buch.status_setzen(d["id"], "abgebrochen")

    ids = {u["id"] for u in buch.unerledigt_fuer("social")}
    assert ids == {a["id"], b["id"]}


def test_auftrag_erteilen_sperrt_ab_drittem_unerledigten_auftrag(monkeypatch):
    func = _auftrag_erteilen(monkeypatch)
    for i in range(3):
        func(agent="social", titel=f"Auftrag {i}", sofort_starten=False)

    with pytest.raises(ValueError, match="unerledigte Aufträge"):
        func(agent="social", titel="Ein Auftrag zu viel", sofort_starten=False)


def test_auftrag_erteilen_erlaubt_neuen_auftrag_nach_abschluss_eines_alten(monkeypatch):
    func = _auftrag_erteilen(monkeypatch)
    ergebnisse = [func(agent="social", titel=f"Auftrag {i}", sofort_starten=False)
                  for i in range(3)]

    buch = Auftragsbuch()
    buch.status_setzen(ergebnisse[0]["auftrag"]["id"], "abgenommen")

    # Sperre greift nicht mehr, weil nur noch zwei unerledigt sind.
    func(agent="social", titel="Wieder Platz", sofort_starten=False)


def test_auftrag_erteilen_lehnt_redakteur_ab(monkeypatch):
    """redakteur legt nie selbst mit submit_for_review vor (siehe seine Rolle
    und team_builder.py's explizites Verbot dieses Werkzeugs fuer ihn) - ein
    direkt an ihn erteilter Auftrag kann nie 'vorgelegt' erreichen. Beobachtet
    07.09.2026: zwei so erteilte Auftraege blieben trotz fertigem Text fuer
    Tage auf 'in_arbeit' stehen."""
    func = _auftrag_erteilen(monkeypatch)
    with pytest.raises(ValueError, match="legt nie selbst etwas vor"):
        func(agent="redakteur", titel="Skript schreiben", sofort_starten=False)
