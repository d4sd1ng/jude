"""Chef- und Betriebswerkzeuge für Jude selbst.

Abnahme und Team-Läufe gab es bisher nur als GUI-Endpunkte –
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

    def auftrag_erteilen(agent: str, titel: str, beschreibung: str = "",
                         faellig_am: str = "", sofort_starten: bool = True) -> dict:
        """Auftrag ins Auftragsbuch UND (standardmäßig) sofort losarbeiten lassen."""
        from services.auftraege import Auftragsbuch
        from services.team import SubAgentService
        dienst = SubAgentService(registry, router)
        bekannt = {a["name"].casefold() for a in dienst.list()}
        if str(agent).strip().casefold() not in bekannt:
            raise ValueError(f"Kein Mitarbeiter namens {agent!r}. Bekannt: {', '.join(sorted(bekannt))}.")
        buch = Auftragsbuch()
        a = buch.erteilen(agent, titel, beschreibung, str(faellig_am).strip() or None)
        if sofort_starten:
            buch.status_setzen(a["id"], "in_arbeit")
            lauf = dienst.run(agent, f"AUFTRAG [{a['id']}]: {titel}\n{beschreibung}\n"
                              f"Lege das Ergebnis mit submit_for_review vor und gib "
                              f"auftrag_id='{a['id']}' an.")
            return {"auftrag": a, "lauf_status": lauf.get("status"),
                    "antwort": (lauf.get("answer") or "")[:400]}
        return {"auftrag": a, "hinweis": "Notiert. Der Wächter oder ein späterer Start fasst nach."}

    def auftraege_liste(status: str = "offen") -> list[dict]:
        from services.auftraege import Auftragsbuch
        return Auftragsbuch().liste(status=status, limit=100)

    def auftrag_abbrechen(auftrag_id: str) -> dict:
        from services.auftraege import Auftragsbuch
        return Auftragsbuch().abbrechen(auftrag_id)

    def team_tagesrunde() -> dict:
        """Für den Scheduler: JEDEN Mitarbeiter einmal täglich laufen lassen –
        nicht nur die, die schon einen offenen Auftrag haben. Vorher lief das
        Team nur, wenn jemand von Hand einen Auftrag anlegte; Wochen ohne
        neuen Auftrag bedeuteten Wochen ohne jede Aktivität, unbemerkt."""
        from services.team import SubAgentService
        dienst = SubAgentService(registry, router)
        ergebnisse = []
        for spec in dienst.list():
            name = spec["name"]
            try:
                lauf = dienst.run(name,
                                  "TAGESRUNDE: Erledige deine Aufgabe für heute gemäß deiner Rolle "
                                  "(QUELLE/ARBEIT/ZIEL/FERTIG). Offene Aufträge und Revisionen oben im "
                                  "Systemprompt zuerst. PFLICHT DANACH, auch ohne offenen Auftrag: führe "
                                  "den in deiner Rolle unter QUELLE genannten Schritt WIRKLICH aus – "
                                  "notion_query, scrape_public_url, news_search o. ä., je nachdem was "
                                  "deine Rolle vorschreibt. 'Nichts zu tun' ohne diesen Schritt zählt "
                                  "nicht, das Themenfeld ist nie leer, nur ungeprüft. Erst wenn der "
                                  "wirkliche Check nichts Neues ergibt, rufst du remember_finding mit "
                                  "einer knappen Begründung auf – das zählt dann als erledigter Check.")
                ergebnisse.append({"agent": name, "status": lauf.get("status")})
            except Exception as exc:
                ergebnisse.append({"agent": name, "fehler": str(exc)[:200]})
        try:
            from services.notifications import NotificationService
            zusammen = {}
            for e in ergebnisse:
                schluessel = e.get("status") or "fehler"
                zusammen[schluessel] = zusammen.get(schluessel, 0) + 1
            text = ", ".join(f"{v}x {k}" for k, v in zusammen.items())
            NotificationService().create("team", f"Tagesrunde: {len(ergebnisse)} Mitarbeiter gelaufen", text)
        except Exception:
            pass
        return {"gelaufen": len(ergebnisse), "ergebnisse": ergebnisse}

    def revisionen_nacharbeiten() -> dict:
        """Jeder Mitarbeiter mit einer offenen Revision (Vorlage oder zurückgewiesene
        Aktion), der gerade nichts tut, bekommt sie direkt zur Bearbeitung – statt
        tagelang liegenzubleiben, bis zufällig ein anderer Auftrag ihn ohnehin
        laufen lässt. Anders als team_tagesrunde läuft hier NICHT jeder Mitarbeiter
        mit, nur wer wirklich etwas offen hat und gerade frei ist.

        Läuft parallel statt nacheinander: jeder Kandidat startet in einem
        eigenen Thread, statt einen einzigen Scheduler-Worker-Slot für die
        gesamte Kette zu blockieren – sonst wartet z. B. content auf sechs
        fremde Revisionen vor ihm in der Schlange, obwohl zwei weitere
        Worker-Slots die ganze Zeit frei wären."""
        import threading
        from services.review import ReviewQueue
        from services.team import SubAgentService
        from services.database import connection
        with connection() as db:
            laufend = {r["agent"] for r in db.execute(
                "SELECT DISTINCT agent FROM agent_runs WHERE status='laufend'")}
        offen: dict[str, int] = {}
        for row in ReviewQueue().agenten_mit_offenen_revisionen():
            offen[row["agent"]] = offen.get(row["agent"], 0) + row["anzahl"]
        with connection() as db:
            for row in db.execute(
                    "SELECT agent, COUNT(*) AS anzahl FROM confirmations "
                    "WHERE status='revision' AND agent!='' GROUP BY agent"):
                offen[row["agent"]] = offen.get(row["agent"], 0) + row["anzahl"]
        dienst = SubAgentService(registry, router)
        bekannt = {a["name"] for a in dienst.list()}
        # Obergrenze gleichzeitiger Laeufe aus diesem Tick: drei parallele
        # cloud_openai_terra-Aufrufe haben am 05.09.2026 das gesamte OpenAI-
        # Guthaben in ~3 Minuten verbraucht (Sperre bis Mitternacht). Wer hier
        # nicht drankommt, wartet einfach bis zum naechsten Tick (5 Min.) -
        # kein Datenverlust, nur gestaffelt statt aller auf einmal.
        MAX_GLEICHZEITIG = 2

        def _starte(agent_name: str) -> None:
            try:
                dienst.run(agent_name,
                          "Du hast offene Revisionen – Vorlagen oder zurückgewiesene Aktionen, "
                          "die auf deine Überarbeitung warten (siehe oben im Systemprompt). "
                          "Arbeite sie ab, bevor du etwas Neues beginnst.")
            except Exception as exc:
                try:
                    from services.notifications import NotificationService
                    NotificationService().create(
                        "scheduled_error", f"Revisionsnacharbeit {agent_name} fehlgeschlagen", str(exc)[:300])
                except Exception:
                    pass

        gestartet, uebersprungen, wartet = [], [], []
        for agent_name, anzahl in offen.items():
            if agent_name not in bekannt:
                continue
            if agent_name in laufend:
                uebersprungen.append(agent_name)
                continue
            if len(gestartet) >= MAX_GLEICHZEITIG:
                wartet.append(agent_name)
                continue
            threading.Thread(target=_starte, args=(agent_name,), daemon=True).start()
            gestartet.append({"agent": agent_name, "anzahl": anzahl})
        return {"gestartet": gestartet, "schon_aktiv_uebersprungen": uebersprungen,
                "wartet_auf_naechsten_tick": wartet}

    def rolle_aktualisieren(agent: str, neuer_text: str) -> dict:
        """Rollen-Prompt eines Mitarbeiters ersetzen – nur nach Tinos Bestätigung."""
        from services.team import SubAgentService
        dienst = SubAgentService(registry, router)
        spec = dienst.get(agent)
        if spec is None:
            raise KeyError(f"Kein Mitarbeiter namens {agent!r}.")
        neuer_text = str(neuer_text).strip()
        if len(neuer_text) < 20:
            raise ValueError("Der neue Rollen-Text ist zu kurz.")
        daten = dienst._load()
        daten[dienst._key(agent)]["role"] = neuer_text
        dienst._save(daten)
        return {"agent": spec["name"], "rolle_aktualisiert": True}

    registry.register(Tool("dokument_zustellen",
                           "Ein Dokument aus austausch/an-team den betreffenden Mitarbeitern zustellen "
                           "(landet als Notiz in deren nächstem Lauf).",
                           dokument_zustellen,
                           _schema({"datei": {"type": "string"},
                                    "empfaenger": {"type": "array", "items": {"type": "string"}},
                                    "hinweis": {"type": "string"}}, ["datei", "empfaenger"])))

    registry.register(Tool("auftrag_erteilen",
                           "Einem Mitarbeiter einen verfolgbaren Auftrag erteilen (Auftragsbuch) und "
                           "ihn standardmäßig sofort daran arbeiten lassen.",
                           auftrag_erteilen,
                           _schema({"agent": {"type": "string"}, "titel": {"type": "string"},
                                    "beschreibung": {"type": "string"},
                                    "faellig_am": {"type": "string", "description": "ISO-Datum/Zeit, optional"},
                                    "sofort_starten": {"type": "boolean"}},
                                   ["agent", "titel"])))
    registry.register(Tool("auftraege_liste",
                           "Aufträge im Auftragsbuch auflisten (Status: offen, in_arbeit, vorgelegt, "
                           "abgenommen, abgebrochen oder alle).",
                           auftraege_liste, _schema({"status": {"type": "string"}})))
    registry.register(Tool("auftrag_abbrechen",
                           "Einen Auftrag abbrechen – nur wenn Tino es sagt.",
                           auftrag_abbrechen,
                           _schema({"auftrag_id": {"type": "string"}}, ["auftrag_id"])))
    registry.register(Tool("team_tagesrunde",
                           "Jeden Mitarbeiter einmal laufen lassen, nicht nur die mit offenem Auftrag "
                           "(nutzt der Scheduler täglich; manuell aufrufbar).",
                           team_tagesrunde, _schema({})))
    registry.register(Tool("rolle_aktualisieren",
                           "Rollen-Prompt eines Mitarbeiters ersetzen (nach Prompt-Diagnose) – "
                           "erfordert Tinos ausdrückliche Bestätigung.",
                           rolle_aktualisieren,
                           _schema({"agent": {"type": "string"}, "neuer_text": {"type": "string"}},
                                   ["agent", "neuer_text"]),
                           confirm_action="update_agent"))

    registry.register(Tool("chefpruefung_nachholen",
                           "Hängende Chefprüfungen sofort durchführen, damit fertige Vorlagen bei Tino ankommen.",
                           chefpruefung_nachholen, _schema({})))
    registry.register(Tool("revisionen_nacharbeiten",
                           "Jeden Mitarbeiter mit offener Revision (Vorlage oder zurückgewiesene Aktion), "
                           "der gerade nichts tut, direkt daran arbeiten lassen – nur diese, nicht das "
                           "ganze Team (nutzt der Scheduler periodisch; manuell aufrufbar).",
                           revisionen_nacharbeiten, _schema({})))
