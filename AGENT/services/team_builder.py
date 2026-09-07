"""Baut lauffertige Agent-Instanzen aus einer Mitarbeiter-Spezifikation:
Modell waehlen, Werkzeugliste zusammenstellen, den vollen Systemprompt
(Rolle, Marke, offene Auftraege, Revisionen, Lehren/Lob, Notizen) zusammen-
setzen. Aus ``services.team.SubAgentService`` ausgelagert - Teil 3, Stage C,
letzter Schritt des Refactorings (jolly-napping-tulip.md).

Die einzige der vier extrahierten Sammlungen, die wirklich das volle Tripel
(registry, router, ai_router) braucht - alle anderen kommen mit weit weniger
aus (siehe die Abhaengigkeits-Kartierung im Plan).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from services.marke import BRAND_BRIEF

logger = logging.getLogger(__name__)


class AgentBuilder:
    #: Wohin eskaliert wird, wenn ein Mitarbeiter mehrfach in Folge nicht
    #: wirklich fertig wurde - kein Dauerwechsel, nur fuer den naechsten
    #: Lauf (siehe _eskalation_noetig unten). Staerkeres Modell, teurer,
    #: aber sorgfaeltiger bei genauem Befolgen von Vorgaben - genau das,
    #: woran cloud_ollama_gptoss wiederholt scheiterte (07.09.2026: eine
    #: seit Tagen erledigte Stoerung fuer aktuell gehalten, ein mehrfach
    #: gezaehltes Motiv trotz expliziter Stueckzahl nicht eingehalten).
    ESKALATIONS_MODELL = "cloud_claude_sonnet"
    #: Zweite Wahl, falls Anthropic gerade nicht verfuegbar ist (gemessen
    #: 07.09.2026: Anthropic-Guthaben erschoepft, "credit balance too low",
    #: bis Mitternacht gesperrt). Ohne diese Stufe fiel eine Eskalation dann
    #: direkt auf das eigene, oft gerade selbst instabile Modell zurueck
    #: (zeitgleich haeufige 500er bei cloud_ollama_gptoss) - Eskalation und
    #: Fallback scheiterten am selben Tag aus zwei unabhaengigen Gruenden.
    #: OpenAI lief zu diesem Zeitpunkt nachweislich stabil weiter.
    ESKALATIONS_MODELL_ALT = "cloud_openai_terra"
    #: Ab wie vielen NICHT erfolgreich abgeschlossenen Laeufen IN FOLGE
    #: eskaliert wird. Bewusst niedrig (Tinos Vorgabe: "mehrere Fehler in
    #: Folge"), nicht bei einem einzelnen - ein 'teilweise' allein ist
    #: normaler Betrieb, keine Stoerung.
    ESKALATION_AB_FOLGEFEHLERN = 2

    def __init__(self, svc, tools):
        self.svc = svc
        # getattr statt direktem Attributzugriff: dieselbe __new__-Bypass-
        # Vorsicht wie bei AgentRegistry/FeedbackStore/ContentGate - falls
        # __init__ nie lief, sollen registry/router/ai_router einfach None
        # sein statt eines AttributeError beim Bau dieser Klasse selbst.
        self.registry = getattr(svc, "registry", None)
        self.router = getattr(svc, "router", None)
        self.ai_router = getattr(svc, "ai_router", None)
        self.tools = tools

    def brauchbares_modell(self, *namen: str | None) -> str | None:
        """Das erste vorgegebene Modell, das gerade wirklich geht – sonst None.

        Zwoelf der dreizehn Mitarbeiter sind auf cloud_ollama_gptoss festgelegt.
        Steht dessen Provider gerade ohne Guthaben da oder fehlt der Schluessel,
        soll der Lauf nicht daran kleben bleiben. None heisst: der Router waehlt
        selbst und arbeitet die Fallback-Kette ab. Sobald Tino auflaedt, greift
        die Vorgabe von allein wieder – ohne dass jemand Konfiguration anfasst.
        """
        from core.model_router import provider_gesperrt
        for name in namen:
            spec = self.router.models.get(name) if name else None
            if spec is None:
                continue
            if provider_gesperrt(spec.provider) or not self.router._provider_enabled(spec.provider):
                logger.info("Modell %s gerade nicht verfuegbar – naechste Wahl.", name)
                continue
            return name
        return None

    #: Ab welcher Revisionsrunde eines EINZELNEN Erzeugnisses eskaliert wird -
    #: unabhaengig von agent_runs.status. Gemessen 07.09.2026: ein Bildmotiv
    #: ging durch 7+ Revisionsrunden, waehrend jeder einzelne Lauf technisch
    #: sauber als 'abgeschlossen' protokolliert wurde (Werkzeuge liefen
    #: fehlerfrei) - der Fehlschlag lag ausschliesslich im Inhalt, den nur
    #: die Chefpruefung/Renate/Tino beurteilen, nicht agent_runs.status.
    ESKALATION_AB_REVISIONSRUNDE = 3

    def _eskalation_noetig(self, agent_name: str) -> bool:
        """True, wenn entweder (a) die letzten ESKALATION_AB_FOLGEFEHLERN
        Laeufe dieses Mitarbeiters ALLE nicht 'abgeschlossen' waren - dafuer
        reicht agent_runs.status - ODER (b) ein einzelnes Erzeugnis bereits
        ESKALATION_AB_REVISIONSRUNDE-fach abgelehnt wurde (reviews.runde),
        selbst wenn jeder einzelne Lauf technisch 'abgeschlossen' war. Beides
        braucht kein Flag, das jemand zuruecksetzen muesste - sobald ein Lauf
        wieder abschliesst bzw. eine Revision endlich durchgeht, greift die
        jeweilige Pruefung von selbst nicht mehr."""
        from services.database import connection
        with connection() as db:
            rows = db.execute(
                "SELECT status FROM agent_runs WHERE agent=? AND status!='laufend' "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_name, self.ESKALATION_AB_FOLGEFEHLERN)).fetchall()
            if len(rows) == self.ESKALATION_AB_FOLGEFEHLERN and all(r["status"] != "abgeschlossen" for r in rows):
                return True
            hartnaeckig = db.execute(
                "SELECT 1 FROM reviews WHERE agent=? AND status IN ('pruefung','revision') "
                "AND runde>=? LIMIT 1",
                (agent_name, self.ESKALATION_AB_REVISIONSRUNDE)).fetchone()
        return hartnaeckig is not None

    def build_agent(self, spec: dict):
        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        svc = self.svc
        sub = ToolRegistry()
        sub.set_confirmations(self.registry.confirmations)
        sub.agent_name = spec["name"]
        eigenes_modell = spec.get("model") or svc.STANDARD_MODELL
        if self._eskalation_noetig(spec["name"]):
            logger.info("%s: %s Laeufe in Folge nicht abgeschlossen - eskaliere diesen Lauf auf %s.",
                       spec["name"], self.ESKALATION_AB_FOLGEFEHLERN, self.ESKALATIONS_MODELL)
            modell = self.brauchbares_modell(self.ESKALATIONS_MODELL, self.ESKALATIONS_MODELL_ALT,
                                             eigenes_modell)
        else:
            modell = self.brauchbares_modell(eigenes_modell)
        # Ein Mitarbeiter, dessen Modell keine Werkzeuge bedienen kann, bekommt
        # auch keine. Sonst entsteht genau der Schaden, der Mike lahmgelegt hat:
        # das Modell schreibt den Aufruf als Fliesstext hin, nichts wird abgelegt
        # und die Antwort sieht trotzdem aus wie Arbeit.
        werkzeugfaehig = "tools" in getattr(
            self.router.models.get(modell), "tags", ["tools"])
        if werkzeugfaehig:
            for skill in spec["skills"]:
                tool = self.registry.tools.get(skill)
                if tool is not None:
                    sub.register(tool)
            sub.register(self.tools.memory_tool(spec["name"]))
            # Redakteur legt nichts vor - das macht, wer sie beauftragt hat
            # (eigene Rolle + AUSNAHME VON DER ABNAHME-PFLICHT weiter unten).
            # Bisher nur eine Prompt-Regel: submit_for_review wurde trotzdem
            # automatisch registriert und in mehreren Laeufen tatsaechlich
            # aufgerufen (04.09.2026 Audit). Verbot jetzt auf Code-Ebene.
            if spec["name"] != svc.REDAKTEUR:
                sub.register(self.tools.review_tool(spec))
            sub.register(self.tools.bericht_tool(spec))
            sub.register(self.tools.hinweis_tool(spec))
            if spec["name"] in svc.TEXTER:
                sub.register(self.tools.text_tool(spec))
            if spec["name"] == svc.PROJEKTLEITUNG:
                for tool in self.tools.pruefung_tool(spec):
                    sub.register(tool)
        person, alter = spec.get("person"), spec.get("alter")
        wer = f"{person} ({alter})" if person and alter else (person or spec["name"])
        vorstellung = (f"Du heißt {person} und bist {alter} Jahre alt. " if person and alter
                       else f"Du heißt {person}. " if person else "")
        prompt = (f"Du bist {wer}, Mitarbeiter im Team von Jude, zuständig als '{spec['name']}'. "
                  f"{vorstellung}"
                  f"Deine Rolle: {spec['role']}. Nutze ausschließlich deine zugewiesenen Werkzeuge, "
                  f"bleibe bei deiner Aufgabe und antworte knapp und umsetzbar. "
                  f"Wenn du dich meldest, nenne deinen Namen. "
                  # Ohne diese Zeile antworten qwen/llama englisch – und alles, was an der
                  # Chefpruefung vorbeigeht (Antworten, Notizen, Notion), kommt englisch an.
                  f"SPRACHE: Du arbeitest durchgehend auf Deutsch – jede Antwort, Notiz, "
                  f"jeder Notion-Eintrag und jede Datei. Englisch nur, wenn der Auftrag es "
                  f"ausdrücklich verlangt. "
                  # 21 Läufe, 1 Vorlage: Die Arbeit ging nach Notion und galt damit als
                  # fertig – bei Tino kam nie etwas zur Abnahme an. Deshalb Pflicht.
                  #
                  # Ausnahme Redakteur: er widersprach sich selbst, weil diese Pflicht
                  # unbedingt formuliert war UND seine eigene Rolle "NICHT: legst du
                  # nichts ab" sagt – zwei gegensaetzliche Anweisungen im selben Prompt
                  # (Audit-Befund F-03, 03.09.2026). Er liefert an den Auftraggeber
                  # zurueck, der reicht es ein.
                  + (f"ABNAHME-PFLICHT: Nichts gilt als fertig, was nicht vorgelegt wurde. "
                     f"Jedes Erzeugnis, das nach außen geht oder das Tino sehen soll (Post, "
                     f"E-Mail, Newsletter, Sequenz, Dokument, Recherche-Ergebnis, Grafik), "
                     f"legst du am Ende deines Laufs mit submit_for_review vor – auch wenn es "
                     f"schon in Notion steht; nenne dann die Notion-URL als quelle. Interne "
                     f"Zuarbeit (Notizen, Hinweise an Kollegen) braucht keine Vorlage. "
                     if spec["name"] != svc.REDAKTEUR else
                     f"AUSNAHME VON DER ABNAHME-PFLICHT: Du legst NICHTS selbst mit "
                     f"submit_for_review vor – auch keine fertigen Texte. Du lieferst den "
                     f"Text an den zurück, der dich beauftragt hat; er reicht ihn ein. ")
                  + f"KEIN AUFTRAG HEUTE: Erst nachdem du deine Rolle wirklich geprüft hast "
                  f"(die dort genannte Quelle abgefragt, nicht nur deine offenen Aufträge "
                  f"angeschaut) und dabei nichts gefunden hast, meldest du das – nicht nur "
                  f"mit remember_finding. Melde es zusätzlich mit inform_colleague an "
                  f"'projektleitung' (außer du bist selbst projektleitung) UND mit "
                  f"report_to_tino an Tino. Beides ist Pflicht, keine Option.")
        prompt += f"\n\n{BRAND_BRIEF}"
        # Offene Aufträge: ohne diesen Block wusste niemand, dass etwas
        # bestellt war – 21 Läufe, 1 Vorlage, die Donnerstag-Bestellung
        # versickerte. Jetzt steht jede Schuld im Prompt.
        try:
            from services.auftraege import Auftragsbuch
            offene_auftraege = Auftragsbuch().offene_fuer(spec["name"])
        except Exception:
            offene_auftraege = []
        if offene_auftraege:
            jetzt = datetime.now(timezone.utc)
            def _ueberfaellig(a: dict) -> bool:
                try:
                    return bool(a.get("faellig_am")) and datetime.fromisoformat(a["faellig_am"]) < jetzt
                except Exception:
                    return False
            zeilen = "\n".join(
                ("- ÜBERFÄLLIG: " if _ueberfaellig(a) else "- ")
                + f"[{a['id']}] {a['titel']}"
                + (f" (fällig {a['faellig_am'][:10]})" if a.get("faellig_am") else "")
                + (f": {a['beschreibung'][:200]}" if a.get("beschreibung") else "")
                for a in offene_auftraege[:8])
            prompt += ("\n\nDEINE OFFENEN AUFTRÄGE – erledige sie in dieser Reihenfolge "
                       "und lege jedes Ergebnis mit submit_for_review vor; gib dabei "
                       "auftrag_id aus der eckigen Klammer an:\n" + zeilen)
        # Offene Revisionen: eigener Altbestand, NICHT automatisch Vorrang vor
        # der jetzt gestellten Aufgabe. "ZUERST ERLEDIGEN" stand hier fruher -
        # das Modell nahm es woertlich und ordnete eine explizit gestellte NEUE
        # Aufgabe einem alten, aehnlich klingenden Revisions-Eintrag unter,
        # arbeitete den alten Eintrag statt der neuen Aufgabe ab (social,
        # Pipeline-Pilot 05.09.2026: eine 2 Tage alte, andere Revision zu
        # "Angebotsuebergabe im Handwerk" wurde mit der frisch gestellten
        # Aufgabe zum Thema "KI-Agenten mit Computerbedienung" verwechselt,
        # weil beide aehnliche Automatisierungs-Ablaeufe beschreiben). Jetzt
        # ausdruecklich als eigener, getrennter Altbestand markiert: nur
        # abarbeiten, wenn die aktuelle Aufgabe unten NICHT bereits etwas
        # anderes verlangt - und auf keinen Fall mit ihr vermischen.
        from services.review import ReviewQueue
        revisionen = ReviewQueue().offene_revisionen(spec["name"])
        if revisionen:
            zeilen = []
            for r in revisionen:
                zeile = f"- [{r['id']}] {r['titel']} (Runde {r['runde']}): {r['anmerkung']}"
                # Ohne die bisherige Historie sah der Mitarbeiter bei einem
                # Stueck in hoher Runde immer nur die eine aktuelle Anmerkung,
                # nie das Muster ueber die vorherigen Runden - er konnte nicht
                # erkennen, ob er denselben Einwand wiederholt uebersieht
                # (gemessen 07.09.2026: Runde 11, verlauf zeigte dasselbe wie
                # Runde 1 nie ausgewertet). Ab Runde 3 wird das explizit dazu-
                # gegeben, nicht schon ab Runde 1 - dort ist die aktuelle
                # Anmerkung allein noch genug Kontext.
                if r["runde"] >= 3 and r.get("verlauf"):
                    zeile += f"\n  BISHERIGE RUNDEN (Muster erkennen, nicht denselben Fehler wiederholen):\n  {r['verlauf'].strip()}"
                if r.get("inhalt"):
                    indented = r['inhalt'][:1500].replace('\n', '\n  ')
                    zeile += f"\n  BISHERIGER INHALT (gezielt verbessern, nicht neu erfinden):\n  {indented}"
                zeilen.append(zeile)
            prompt += ("\n\nGETRENNTER ALTBESTAND – frueher zurueckgewiesene Vorlagen, die noch "
                       "auf Ueberarbeitung warten. WICHTIG: das ist NICHT automatisch deine "
                       "jetzige Aufgabe. Verlangt die unten gestellte Aufgabe etwas anderes "
                       "oder ein anderes Thema, bearbeite ZUERST die gestellte Aufgabe vollstaendig "
                       "und wirklich nur sie - verwechsle sie nicht mit einem der folgenden Eintraege, "
                       "auch wenn ein Thema aehnlich klingt. Erst wenn die gestellte Aufgabe erledigt "
                       "ist oder keine eigene Aufgabe vorliegt, arbeite diese Liste ab:\n"
                       + "\n".join(zeilen) +
                       "\nArbeite jede Anmerkung ein und lege das Ergebnis erneut mit "
                       "submit_for_review vor – dabei die ID aus der eckigen Klammer "
                       "als 'ueberarbeitet' mitgeben, sonst bleibt die alte Fassung "
                       "offen und du bekommst sie beim naechsten Lauf wieder vorgelegt. "
                       "Bei Dokumenten oder Grafiken ist die Revision erst erledigt, "
                       "wenn Wortmarke, Tagline, Farben, Verläufe, Karten und Icons "
                       "geprüft und korrekt umgesetzt sind.")
            prompt += f"\n\n{BRAND_BRIEF}"
        # Zurückgewiesene Bestätigungen (z.B. code_write) haben ebenfalls Vorrang:
        # ohne diesen Block sah der Mitarbeiter nie, dass Tino eine Anmerkung dazu
        # hatte, und rief das Werkzeug bestenfalls blind noch einmal auf.
        from services.confirmations import ConfirmationQueue
        offene_bestaetigungen = ConfirmationQueue().offene_revisionen(spec["name"])
        if offene_bestaetigungen:
            zeilen = [f"- [{b['action_type']}] {b['summary'][:150]} (Runde {b['runde']}): {b['anmerkung']}"
                      for b in offene_bestaetigungen]
            prompt += ("\n\nZURÜCKGEWIESENE BESTÄTIGUNGEN – ruf das gleiche Werkzeug erneut mit "
                       "korrigierten Angaben auf, um die Anmerkung einzuarbeiten:\n" + "\n".join(zeilen))
        gelernt = svc.lehren(spec["name"])[:svc.PROMPT_LEHREN]
        if gelernt:
            zeilen = "\n".join(
                f"- {e['text']}" + (f"  (schon {e['anzahl']}x beanstandet)"
                                    if int(e.get("anzahl", 1)) > 1 else "")
                for e in gelernt)
            prompt += ("\n\nDAS WURDE DIR SCHON BEANSTANDET – mach es nicht wieder:\n"
                       + zeilen)
        gelobt = svc.lob(spec["name"])[:svc.PROMPT_LEHREN]
        if gelobt:
            zeilen = "\n".join(
                f"- {e['text']}" + (f"  (schon {e['anzahl']}x bestaetigt)"
                                    if int(e.get("anzahl", 1)) > 1 else "")
                for e in gelobt)
            prompt += ("\n\nDAS HAST DU GUT GEMACHT – daran anknuepfen:\n"
                       + zeilen)
        notes = svc.notes(spec["name"])
        if notes:
            recent = "\n".join(f"- {item['note']}" for item in notes[-svc.PROMPT_NOTES:])
            prompt += ("\n\nWas du bisher festgehalten hast (nicht doppelt bearbeiten):\n" + recent)
        return Agent(self.router, sub, system_prompt=prompt,
                     max_tool_steps=svc.TOOL_SCHRITTE if werkzeugfaehig else 0,
                     force_model=modell, agent_name=spec["name"],
                     task_type=spec.get("task_type", "routine_dispatch"),
                     use_ai_router=bool(spec.get("use_ai_router", False)),
                     ai_router=self.ai_router)
