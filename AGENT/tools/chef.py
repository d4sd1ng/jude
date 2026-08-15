"""Chef- und Betriebswerkzeuge für Jude selbst.

Abnahme, Groq-Kontingent und Team-Läufe gab es bisher nur als GUI-Endpunkte –
im Gespräch konnte Jude die Fragen "gibt es was abzunehmen?", "wer hat die
Tokens verbraucht?" oder "was hat das Team heute produziert?" gar nicht
beantworten und schwafelte stattdessen. Diese Werkzeuge schließen die Lücke.
"""

from __future__ import annotations

from core.tool_registry import Tool, ToolRegistry


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    result = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def abnahme_liste() -> dict:
    """Offene Abnahmen (bei Tino) und hängende Chefprüfungen (bei Jude)."""
    from services.review import ReviewQueue
    queue = ReviewQueue()
    kurz = lambda v: {"id": v["id"], "agent": v.get("person") or v["agent"],
                      "art": v["art"], "titel": v["titel"], "seit": v["created_at"][:16]}
    return {"zur_abnahme_bei_tino": [kurz(v) for v in queue.liste(status="offen")],
            "wartet_auf_chefpruefung": [kurz(v) for v in queue.zur_pruefung()]}


def abnahme_zeigen(review_id: str) -> dict:
    from services.review import ReviewQueue
    return ReviewQueue().zeigen(review_id)


def abnahme_abnehmen(review_id: str, anmerkung: str = "") -> dict:
    from services.review import ReviewQueue
    return ReviewQueue().abnehmen(review_id, anmerkung)


def abnahme_revision(review_id: str, anmerkung: str) -> dict:
    from services.review import ReviewQueue
    return ReviewQueue().revision(review_id, anmerkung)


def groq_kontingent() -> dict:
    from services import kontingent
    return kontingent.stand()


def team_laeufe(limit: int = 10) -> list[dict]:
    """Die letzten Läufe der Mitarbeiter: wer, wann, womit, wie teuer."""
    from services.database import connection
    limit = max(1, min(int(limit), 50))
    with connection() as db:
        zeilen = db.execute(
            "SELECT created_at, agent, model, status, task, "
            "COALESCE(input_tokens,0)+COALESCE(output_tokens,0) AS tokens "
            "FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"wann": z[0][:16], "agent": z[1], "modell": z[2], "status": z[3],
             "aufgabe": (z[4] or "")[:80], "tokens": z[5]} for z in zeilen]


def register(registry: ToolRegistry) -> None:
    registry.register(Tool("abnahme_liste",
                           "Auflisten, was zur Abnahme ansteht: offen bei Tino und hängend in Judes Chefprüfung.",
                           abnahme_liste, _schema({})))
    registry.register(Tool("abnahme_zeigen",
                           "Eine Abnahme-Vorlage vollständig anzeigen (Inhalt, Verfasser, Historie).",
                           abnahme_zeigen, _schema({"review_id": {"type": "string"}}, ["review_id"])))
    registry.register(Tool("abnahme_abnehmen",
                           "Eine offene Vorlage abnehmen – nur wenn Tino es ausdrücklich sagt.",
                           abnahme_abnehmen,
                           _schema({"review_id": {"type": "string"}, "anmerkung": {"type": "string"}},
                                   ["review_id"])))
    registry.register(Tool("abnahme_revision",
                           "Eine Vorlage mit Anmerkung zur Überarbeitung an den Verfasser zurückgeben.",
                           abnahme_revision,
                           _schema({"review_id": {"type": "string"}, "anmerkung": {"type": "string"}},
                                   ["review_id", "anmerkung"])))
    registry.register(Tool("groq_kontingent",
                           "Aktuellen Stand des Groq-Tageskontingents anzeigen (Rest, Limit, gemessen am).",
                           groq_kontingent, _schema({})))
    registry.register(Tool("team_laeufe",
                           "Letzte Läufe der Mitarbeiter anzeigen: wer lief wann, mit welchem Modell, Status, Tokens.",
                           team_laeufe, _schema({"limit": {"type": "integer"}})))


def register_context(registry: ToolRegistry, router=None, **_kontext) -> None:
    if router is None:
        return

    def chefpruefung_nachholen() -> dict:
        """Hängende Chefprüfungen jetzt durchführen statt auf den nächsten
        Lauf des Verfassers zu warten – sonst bleibt eine Vorlage tagelang
        liegen (Beispiel: Launch-Post vom 13.08.)."""
        from services.review import ReviewQueue
        from services.team import SubAgentService
        agenten = sorted({v["agent"] for v in ReviewQueue().zur_pruefung()})
        dienst = SubAgentService(registry, router)
        ergebnisse = []
        for name in agenten:
            try:
                ergebnisse.extend(dienst._chefpruefung(name))
            except Exception as exc:
                ergebnisse.append({"agent": name, "fehler": str(exc)})
        return {"geprueft": len(ergebnisse), "ergebnisse": ergebnisse}

    def dokument_zustellen(datei: str, empfaenger: list, hinweis: str = "") -> dict:
        """Ein von Tino übergebenes Dokument den betreffenden Mitarbeitern
        zustellen: die Notiz steht in deren nächstem Systemprompt."""
        from tools.austausch import AUSTAUSCH_DIR, _dateiname
        from services.team import SubAgentService
        name = _dateiname(datei)
        pfad = AUSTAUSCH_DIR / "an-team" / name
        if not pfad.is_file():
            vorhanden = ", ".join(p.name for p in sorted((AUSTAUSCH_DIR / "an-team").glob("*"))
                                  if p.is_file()) or "keine"
            raise ValueError(f"Keine Datei {name!r} in an-team/. Vorhanden: {vorhanden}.")
        dienst = SubAgentService(registry, router)
        bekannt = {a["name"].casefold(): a["name"] for a in dienst.list()}
        zugestellt, unbekannt = [], []
        for kollege in (empfaenger if isinstance(empfaenger, list) else [empfaenger]):
            schluessel = str(kollege).strip().casefold()
            if schluessel not in bekannt:
                unbekannt.append(str(kollege))
                continue
            dienst.remember(bekannt[schluessel],
                            f"[Dokument von Tino] austausch/an-team/{name}"
                            + (f" – {hinweis}" if hinweis else "")
                            + f". Lies es mit read_project_file('austausch/an-team/{name}').")
            zugestellt.append(bekannt[schluessel])
        return {"datei": name, "zugestellt": zugestellt, "unbekannt": unbekannt}

    registry.register(Tool("dokument_zustellen",
                           "Ein Dokument aus austausch/an-team den betreffenden Mitarbeitern zustellen "
                           "(landet als Notiz in deren nächstem Lauf).",
                           dokument_zustellen,
                           _schema({"datei": {"type": "string"},
                                    "empfaenger": {"type": "array", "items": {"type": "string"}},
                                    "hinweis": {"type": "string"}}, ["datei", "empfaenger"])))

    registry.register(Tool("chefpruefung_nachholen",
                           "Hängende Chefprüfungen sofort durchführen, damit fertige Vorlagen bei Tino ankommen.",
                           chefpruefung_nachholen, _schema({})))
