"""Werkzeug-Fabrik: baut die Tool-Objekte, die ein Sub-Agent im Werkzeuglauf
aufrufen kann (inform_colleague, write_copy, submit_for_review,
pruefungsliste/pruefung_entscheiden, remember_finding, report_to_tino). Aus
``services.team.SubAgentService`` ausgelagert - Teil 3, Stage C, letzter
Schritt des Refactorings (jolly-napping-tulip.md).

Haelt eine Referenz auf die Fassade selbst (``SubAgentService``), nicht auf
einzelne Mitspieler: mehrere Closures greifen quer auf Fassaden-Methoden zu
(``run``, ``remember``, ``get``, ``list``, ``_key``, ``lehre_merken``,
``_fehlende_datei``, ``_laenge_pruefen``) - genau der Zyklus, den der Plan
beschreibt (Werkzeug -> ``run`` -> ``_build_agent`` -> Werkzeug). Die Fassade
existiert laengst, wenn diese Closures GEBAUT werden; aufgerufen werden sie
erst spaeter, waehrend eines echten Werkzeuglaufs, wenn alle Delegationen der
Fassade laengst stehen.
"""

from __future__ import annotations


class ToolFactory:
    def __init__(self, svc):
        self.svc = svc

    def hinweis_tool(self, spec: dict):
        """Einen Kollegen direkt auf etwas hinweisen.

        Ein Fund ist nur etwas wert, wenn er bei dem landet, der ihn braucht.
        Der Hinweis geht ins Gedaechtnis des Kollegen und steht damit in seinem
        naechsten Systemprompt – er muss nicht danach suchen und der Beobachter
        muss nicht warten. Ueber Jude laeuft das nicht: eine Beobachtung ist
        Zuarbeit, keine Entscheidung.
        """
        from core.tool_registry import Tool

        svc = self.svc
        wer = spec.get("person") or spec["name"]

        def hinweisen(kollege: str, hinweis: str) -> dict:
            kollege = str(kollege).strip().lower()
            ziel = svc.get(kollege)
            if ziel is None:
                bekannt = ", ".join(sorted(a["name"] for a in svc.list()))
                raise ValueError(f"Kein Kollege namens {kollege!r}. Bekannt: {bekannt}.")
            if kollege == svc._key(spec["name"]):
                raise ValueError("Dir selbst brauchst du nichts mitzuteilen – nutze remember_finding.")
            if len(str(hinweis).strip()) < 20:
                raise ValueError("Der Hinweis ist zu duenn. Nenne Quelle, Kernaussage und "
                                 "warum es fuer den Kollegen brauchbar ist.")
            svc.remember(kollege, f"[Hinweis von {wer}] {hinweis}")
            # Kein weitererzaehlbarer Satz in der Rueckgabe: das Feld hiess
            # "hinweis" und enthielt "Steht in seinem naechsten Lauf im
            # Systemprompt." – dasselbe Wort wie der Eingabeparameter. Das
            # Modell las die Quittung als naechsten Hinweis und schickte sie
            # weiter (gemessen 05.09.2026: social 93x inform_colleague in
            # einem Tag, content 74x). Quittung ist jetzt ein Endzustand.
            return {"zugestellt": True, "an": ziel.get("person") or ziel["name"],
                    "erledigt": "Zustellung abgeschlossen. Nicht wiederholen, "
                                "nichts daran weitermelden."}

        return Tool(
            name="inform_colleague",
            description=("Gibt einen Fund direkt an den Kollegen weiter, der ihn brauchen kann "
                         "(z. B. 'social' fuer Beitragsideen, 'content' fuer Langformate, "
                         "'sequencer' fuer E-Mail-Themen). Nutze das, sobald du etwas findest, "
                         "das jemand verwerten kann – sammle es nicht bei dir."),
            func=hinweisen,
            param_schema={"type": "object", "properties": {
                "kollege": {"type": "string", "description":
                            "Kurzname des Kollegen, z. B. social, content, sequencer."},
                "hinweis": {"type": "string", "description":
                            "Quelle, Kernaussage und warum es fuer ihn brauchbar ist."},
            }, "required": ["kollege", "hinweis"]},
        )

    def text_tool(self, spec: dict):
        """Den Redakteur direkt beauftragen.

        Getextet und orchestriert wird von verschiedenen Modellen, weil kein
        verfuegbares beides gut kann: qwen3:8b setzt Werkzeugaufrufe zuverlaessig
        ab (gemessen 10 von 10), schreibt aber merklich schwaecher als ein 70B;
        das damalige Textmodell schrieb gut, bekam aber keinen Werkzeugaufruf
        zustande (0 von 2). Heinz laeuft deshalb auf der grossen Stufe und hat
        gar keine Werkzeuge – genau der Pfad, auf dem sie nie gescheitert ist.

        Der kurze Draht ist Absicht: Text ist keine Entscheidung, sondern
        Zuarbeit. Geprueft wird am Ende ohnehin – aber von Jude, an dem fertigen
        Erzeugnis, nicht an jedem Zwischenschritt.
        """
        from core.tool_registry import Tool
        from services.marke import BRAND_BRIEF

        svc = self.svc
        wer = spec.get("person") or spec["name"]

        def beauftragen(auftrag: str, laenge: str = "mittel", ton: str = "sachlich",
                        lead: str = "", branche: str = "", taetigkeit: str = "", kanal: str = "") -> str | dict:
            grenzen = {"kurz": "hoechstens 200 Zeichen", "mittel": "150 bis 250 Woerter",
                       "lang": "500 bis 900 Woerter"}
            # Der Empfaengerbezug trennt einen brauchbaren von einem beliebigen
            # Text: derselbe Nutzen liest sich fuer eine Tagespflege anders als
            # fuer einen Fahrdienst.
            zum_empfaenger = "\n".join(
                f"{marke}: {wert}" for marke, wert in
                (("Empfaenger", lead), ("Branche", branche), ("Taetigkeit", taetigkeit))
                if str(wert).strip())
            # Ein Text wird selten schlecht, weil Heinz schlecht schreibt – er
            # wird schlecht, weil der Auftrag nichts hergab. Das faellt sonst
            # erst auf, wenn Jude das fertige Erzeugnis zurueckweist, und
            # wiederholt sich bis dahin bei jedem Auftrag.
            fehlt = [feld for feld, wert in (("Empfaenger", lead), ("Branche", branche),
                                             ("Taetigkeit", taetigkeit))
                     if not str(wert).strip()]
            if len(fehlt) == 3:
                svc.lehre_merken(
                    spec["name"],
                    "Deine Auftraege an Heinz kamen ohne jede Angabe zum Empfaenger. "
                    "Ohne Empfaenger, Branche und Taetigkeit kann er nur Allgemeinplaetze "
                    "schreiben – gib sie mit, sonst wird der Text zurueckgewiesen.",
                    quelle="Auftragspruefung")
            ergebnis = svc.run(svc.REDAKTEUR, (
                f"Auftrag von {wer}.\n"
                f"{BRAND_BRIEF}\n"
                f"Ton: {ton}. Laenge: {grenzen.get(laenge, grenzen['mittel'])}.\n"
                + (f"Kanal: {kanal}. Halte die Kanal-Karte aus dem Qualitaets-Playbook ein (Umfang, Ton, Emojis, Hashtags, Link-Regel: LinkedIn/Xing-Link gehoert in den ersten Kommentar, nicht in den Text).\n" if kanal else "")
                + (zum_empfaenger + "\n" if zum_empfaenger else "") +
                "Gib ausschliesslich den fertigen Text zurueck – keine Vorrede, keine "
                "Erklaerung, keine Rueckfrage.\n\n"
                f"Was gebraucht wird: {auftrag}"))
            text = str(ergebnis.get("answer") or "").strip()
            if not text:
                return ("Heinz hat keinen Text geliefert (Status: "
                        f"{ergebnis.get('status')}). Versuche es mit einem genaueren Auftrag.")
            if fehlt:
                hinweis = ("Dein Auftrag enthielt "
                           + ", ".join(fehlt) + " nicht. Der Text ist dadurch allgemeiner "
                           "als noetig – gib die Angaben beim naechsten Mal mit.")
                return {"text": text, "hinweis_an_dich": hinweis}
            return text

        return Tool(
            name="write_copy",
            description=("Beauftragt Heinz, den Redakteur, direkt mit einem fertigen Text "
                         "(Post, E-Mail, Sequenzmail, Follow-up, Betreff, Abschnitt). Nutze das "
                         "IMMER, wenn Text entstehen soll, statt selbst zu formulieren – Heinz "
                         "schreibt sprachlich deutlich besser als du. Gib den Empfaenger so "
                         "genau an, wie du ihn kennst. Seinen Text legst du danach mit deinen "
                         "eigenen Werkzeugen ab und legst ihn zur Abnahme vor. Liefert bei fehlenden "
                         "Angaben zusaetzlich 'hinweis_an_dich' – lies ihn, aber lege ihn NIEMALS mit ab."),
            func=beauftragen,
            param_schema={"type": "object", "properties": {
                "auftrag": {"type": "string", "description":
                            "Was gebraucht wird: Art des Textes, Zweck, Kernaussage, Belege. "
                            "z. B. 'Mail 2 der Nurturing-Sequenz, Nachfassen nach der "
                            "Erstansprache, Ziel ist ein Telefontermin'."},
                "lead": {"type": "string", "description":
                         "Name des Empfaengers oder der Firma, falls bekannt."},
                "branche": {"type": "string", "description":
                            "z. B. ambulante Pflege, Tagespflege, Betreuungsdienst."},
                "taetigkeit": {"type": "string", "description":
                               "Was die Firma konkret macht – daraus zieht Heinz den Nutzen."},
                "laenge": {"type": "string", "enum": ["kurz", "mittel", "lang"]},
                "ton": {"type": "string", "description": "z. B. sachlich, direkt, warm"},
                "kanal": {"type": "string", "enum": ["linkedin", "xing", "instagram", "tiktok", "facebook", "youtube", "email", "blog", ""], "description": "Zielkanal – bringt Heinz die Kanal-Regeln aus dem Playbook mit."},
            }, "required": ["auftrag"]},
        )

    def review_tool(self, spec: dict):
        """Fertiges vorlegen – ohne zu warten.

        Der Mitarbeiter meldet ein Erzeugnis zur Abnahme und arbeitet sofort
        weiter. Es geht ihn erst wieder etwas an, wenn eine Revision kommt.
        """
        from core.tool_registry import Tool
        from services.review import ARTEN, ReviewQueue
        svc = self.svc
        queue = ReviewQueue()
        #: Was die Modelle statt der acht erlaubten Arten hinschreiben. Gemessen
        #: 03.09.2026: engineer, outreach und redakteur scheiterten je an
        #: "Art muss eine von [...] sein" – nicht weil die Arbeit fehlte,
        #: sondern weil sie sie anders benannten. Uebersetzen statt abweisen.
        ART_SYNONYME = {
            "blog": "dokument", "blog-artikel": "dokument", "artikel": "dokument",
            "blogartikel": "dokument", "text": "dokument", "code": "dokument",
            "bericht": "recherche", "analyse": "recherche", "studie": "recherche",
            "social": "post", "social-post": "post", "beitrag": "post",
            "karussell": "post", "reel": "post", "story": "post",
            "mail": "email", "e-mail": "email", "e_mail": "email",
            "bild": "grafik", "image": "grafik", "visual": "grafik",
            "sequence": "sequenz", "kampagne": "sequenz",
        }

        def _einreichen(art, titel, inhalt="", quelle="", ueberarbeitet="", auftrag_id=""):
            schluessel = str(art).strip().casefold()
            if schluessel not in ARTEN:
                art = ART_SYNONYME.get(schluessel, art)
            # Pfade in quelle muessen wirklich existieren: engineer und designer
            # meldeten wiederholt "fertig" mit Dateipfaden, die nie geschrieben
            # wurden (kein coding_write/generate_image im Lauf) – die Vorlage
            # sah fertig aus, war aber leer. Wer nichts geschrieben hat, kann
            # auch nichts vorlegen.
            #
            # Die Pruefung galt bis 04.09.2026 nur fuer absolute Pfade. Die
            # Mitarbeiter geben aber ausnahmslos projektrelative an, also lief
            # sie ins Leere: 41 Vorlagen zeigten auf
            # 'austausch/an-team/vorlagen/nurovelle/Infografik_2.png', eine
            # Datei, die es nie gab. Jetzt wird jeder projektrelative Pfad
            # gegen die Projektwurzel aufgeloest und geprueft.
            for pfad in str(quelle).split(","):
                fehlend = svc._fehlende_datei(pfad)
                if fehlend:
                    raise ValueError(f"Referenzierte Datei existiert nicht: {fehlend}. "
                                     f"Erst wirklich schreiben (coding_write/generate_image), "
                                     f"dann vorlegen.")
            # Eine Ueberarbeitung ist keine neue Vorlage: dieselbe Zeile geht
            # eine Runde weiter zurueck zur Pruefung. Das Kopieren der ID aus
            # dem Prompt wird zuverlaessig vergessen (7 liegengebliebene
            # Landingpage-Versuche vom 16.08. bis 03.09. ohne eine einzige
            # echte Ueberarbeitung, gemessen 03.09.2026) – darum wird eine
            # offene Revision derselben Art automatisch angeknuepft, statt
            # sich auf den vom Modell zitierten Klammer-Wert zu verlassen.
            ziel = str(ueberarbeitet).strip()
            offen = queue.offene_revisionen(spec["name"])
            bekannt = {r["id"] for r in offen}
            # Eine erfundene oder aus dem Prompt falsch abgeschriebene ID darf
            # die fertige Arbeit nicht vernichten: 'Unbekannte Vorlage.' liess
            # den Aufruf scheitern, der Text war weg (gemessen 03.09.2026 bei
            # social und outreach, je zweimal im selben Lauf). Passt die ID
            # nicht, wird wie ohne ID verfahren – offene Revision derselben
            # Art, sonst neu vorlegen.
            if ziel and ziel not in bekannt:
                ziel = ""
            if not ziel:
                treffer = next((r for r in offen if r["art"] == art), offen[0] if offen else None)
                if treffer:
                    ziel = treffer["id"]
            if not ziel:
                # Dieselbe Sache liegt schon in der Pruefung: anknuepfen statt
                # eine 70. Zeile anzulegen (siehe ReviewQueue.offene_gleiche).
                gleiche = queue.offene_gleiche(spec["name"], art, titel)
                if gleiche:
                    ziel = gleiche["id"]
            svc._laenge_pruefen(art, inhalt, neu=not ziel)
            if ziel:
                ergebnis = queue.erledigt(ziel, inhalt or None, titel or None, quelle or None)
            else:
                ergebnis = queue.vorlegen(spec["name"], art, titel, inhalt, quelle, spec.get("person"))
            if str(auftrag_id).strip():
                try:
                    from services.auftraege import Auftragsbuch
                    Auftragsbuch().verknuepfen(str(auftrag_id).strip(),
                                               ergebnis.get("id") or str(ueberarbeitet).strip())
                except Exception:
                    pass
            return ergebnis
        return Tool(
            name="submit_for_review",
            description=("Ein FERTIGES Erzeugnis Tino zur Abnahme vorlegen (Post, E-Mail, Sequenz, "
                         "Dokument, Recherche, Grafik). Du wartest nicht auf Antwort, sondern "
                         "arbeitest weiter. Lege alles vor, was rausgehen soll."),
            func=_einreichen,
            param_schema={"type": "object", "properties": {
                "art": {"type": "string", "enum": sorted(ARTEN)},
                "titel": {"type": "string"},
                "inhalt": {"type": "string", "description": "Der fertige Text bzw. eine Zusammenfassung."},
                "quelle": {"type": "string", "description": "Notion-URL oder Pfad, falls vorhanden."},
                "ueberarbeitet": {"type": "string", "description":
                                  "NUR wenn du eine Revision einarbeitest: die ID in eckigen "
                                  "Klammern aus 'ZUERST ERLEDIGEN'. Dann wird die vorhandene "
                                  "Vorlage ersetzt statt eine zweite anzulegen."},
                "auftrag_id": {"type": "string", "description": "ID aus DEINE OFFENEN AUFTRÄGE, falls dieses Ergebnis einen Auftrag erfüllt."},
            }, "required": ["art", "titel"]},
        )

    def pruefung_tool(self, spec: dict):
        """Projektleitungs aktive Pruef-Werkzeuge (04.09.2026 - loest die
        automatische Judes-Pruefung ab). Duenne Wrapper um ReviewQueue,
        die deterministischen Gates in _chefpruefung laufen weiter unveraendert
        VOR diesen Werkzeugen - was hier ankommt, hat die schon bestanden.
        """
        from core.tool_registry import Tool
        from services.review import ReviewQueue
        wer = spec.get("person") or spec["name"]

        def pruefungsliste() -> list[dict]:
            return ReviewQueue().zur_pruefung(limit=50)

        def pruefung_entscheiden(review_id: str, urteil: str, begruendung: str) -> dict:
            urteil = str(urteil).strip().lower()
            if urteil not in ("freigabe", "revision"):
                raise ValueError("urteil muss 'freigabe' oder 'revision' sein.")
            if len(str(begruendung).strip()) < 10:
                raise ValueError("Begruendung ist zu duenn - konkret benennen, woran es liegt "
                                 "bzw. warum es passt.")
            queue = ReviewQueue()
            text = f"{wer}: {str(begruendung).strip()}"
            if urteil == "freigabe":
                ergebnis = queue.freigeben(review_id, text)
                try:
                    from services.notifications import NotificationService
                    voll = queue.zeigen(review_id)
                    NotificationService().create("abnahme",
                                                 f"Zur Abnahme: {voll['titel'][:80]}",
                                                 f"Von {voll.get('person') or voll['agent']} – von {wer} freigegeben.")
                except Exception:
                    pass
            else:
                ergebnis = queue.revision(review_id, text)
                try:
                    from services.notifications import NotificationService
                    voll = queue.zeigen(review_id)
                    NotificationService().create("revision",
                                                 f"Revision an {voll.get('person') or voll['agent']}: {voll['titel'][:80]}",
                                                 f"{wer} hat Überarbeitungen angefordert: {str(begruendung)[:200]}")
                except Exception:
                    pass
            return ergebnis

        return (
            Tool(
                name="pruefungsliste",
                description=("Zeigt alle Vorlagen, die auf eine Qualitaetsentscheidung warten - "
                             "ueber alle Kolleginnen und Kollegen hinweg, aelteste zuerst, inkl. "
                             "runde. Diese Vorlagen haben bereits alle mechanischen Pruefungen "
                             "bestanden (echtes Bild vorhanden, keine Platzhalter, kein Markenname, "
                             "E-Mail nicht leer/dupliziert) - hier geht es um Ton, Nutzen und "
                             "Massstab, nicht mehr um Vollstaendigkeit."),
                func=pruefungsliste,
                param_schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="pruefung_entscheiden",
                description=("Eine Vorlage aus pruefungsliste() freigeben (dann sichtbar bei Tino) "
                             "oder zur Revision zurueckgeben (dann zurueck an die Kollegin/den "
                             "Kollegen). runde >= 3 heisst: schon zweimal zurueckgewiesen - jetzt "
                             "wirklich entscheiden, nicht noch einen dritten, neuen Einwand "
                             "nachschieben."),
                func=pruefung_entscheiden,
                param_schema={"type": "object", "properties": {
                    "review_id": {"type": "string", "description": "id aus pruefungsliste()."},
                    "urteil": {"type": "string", "enum": ["freigabe", "revision"]},
                    "begruendung": {"type": "string", "description":
                                    "Konkret, mit Bezug auf Massstab oder Auftrag - keine Floskel."},
                }, "required": ["review_id", "urteil", "begruendung"]},
            ),
        )

    def memory_tool(self, name: str):
        from core.tool_registry import Tool
        svc = self.svc
        return Tool(
            name="remember_finding",
            description=("Hält eine dauerhafte Notiz fest (z. B. kontaktierte Firma, Absage, "
                         "erfolgreiche Ansprache). Nutze das nach jedem verwertbaren Ergebnis."),
            func=lambda note: svc.remember(name, note),
            param_schema={"type": "object", "properties": {
                "note": {"type": "string", "description": "Was dauerhaft erinnert werden soll."}},
                "required": ["note"]},
        )

    def bericht_tool(self, spec: dict):
        """Tino/Jude direkt melden – anders als remember_finding (eigenes stilles
        Gedächtnis) landet das sichtbar in den Meldungen. Vor allem gedacht für
        den Fall 'nach echter Prüfung meiner Quelle gibt es heute nichts zu tun':
        eine stille Notiz reicht dafür nicht, das muss oben ankommen."""
        from core.tool_registry import Tool
        wer = spec.get("person") or spec["name"]

        def melden(nachricht: str) -> dict:
            nachricht = str(nachricht).strip()
            if len(nachricht) < 10:
                raise ValueError("Die Meldung ist zu duenn – Grund und Kontext nennen.")
            from services.notifications import NotificationService
            NotificationService.create("mitarbeiter", f"{wer} ({spec['name']})", nachricht)
            # Dieselbe Falle wie bei inform_colleague, hier zuerst aufgefallen:
            # die Rueckgabe lautete "Steht in den Meldungen für Tino." – das
            # Modell meldete diesen Satz als naechste Nachricht weiter, bekam
            # dieselbe Quittung und meldete wieder. 38 Aufrufe hintereinander
            # in einem Lauf, 116 Meldungen von einem Mitarbeiter an einem Tag
            # (gemessen 04./05.09.2026). Quittung ohne Inhalt, der sich
            # weitererzaehlen laesst.
            return {"gemeldet": True,
                    "erledigt": "Meldung zugestellt. Nicht wiederholen, "
                                "nichts daran weitermelden."}

        return Tool(
            name="report_to_tino",
            description=("Tino (und Jude) direkt eine sichtbare Meldung schicken – z. B. wenn "
                         "nach echter Prüfung deiner Quelle heute nichts zu tun war, oder ein "
                         "Ergebnis, das Tino sehen soll, aber keine Abnahme-Vorlage ist. "
                         "Anders als remember_finding: das ist sichtbar, kein stilles Notieren."),
            func=melden,
            param_schema={"type": "object", "properties": {
                "nachricht": {"type": "string", "description": "Was Tino wissen soll, mit Begründung."}},
                "required": ["nachricht"]},
        )
