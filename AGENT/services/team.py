"""Benannte Sub-Agenten ("Mitarbeiter") mit eigener Rolle und Werkzeug-Auswahl.

Jude kann spezialisierte Sub-Agenten anlegen (Name, Rolle, erlaubte Skills),
sie wie Mitarbeiter mit Aufgaben betrauen und wieder entfernen. Jeder Sub-Agent
bekommt eine eigene, eingeschränkte Werkzeugliste; sicherheitsrelevante Aktionen
laufen weiterhin über dieselbe Bestätigungs-Warteschlange.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import logging

from core.paths import DATA_DIR
from services.database import connection
from services.marke import BRAND_BRIEF
from services.team_feedback import FeedbackStore
from services.team_gate import ContentGate
from services.team_registry import AgentRegistry

logger = logging.getLogger(__name__)


class SubAgentService:
    #: Die Mitarbeiter fuehren Werkzeugketten aus – dafuer zaehlt nicht Groesse,
    #: sondern ob das Modell einen Werkzeugaufruf tatsaechlich absetzt. Gemessen
    #: ueber 13 Laeufe: qwen3:8b 10 von 10 mit echtem Aufruf (Median 115 s),
    #: das damalige Groq-Modell 0 von 2 – es schrieb den Aufruf als Text hin.
    #: Deshalb ist die Basis lokal; fuer alles OHNE Werkzeuge zaehlt Sprache,
    #: dafuer laeuft ``TEXT_MODELL`` (siehe Judes Chefpruefung und den
    #: Redakteur sowie die Tags in ``config/models.yaml``).
    #: Judes eigenes Chat-Modell bleibt davon unberuehrt (dolphin3).
    # Seit 15.08.2026 lief das Team auf cloud_claude_haiku, weil qwen auf der
    # RX 580 (Modell nur zu 73 % in der GPU) 9+ Minuten pro Lauf brauchte.
    # Am 03.09.2026 auf Tinos Ansage abgeloest: dasselbe leistet
    # cloud_ollama_gptoss (gpt-oss-120b, 131k Kontext, werkzeugfaehig
    # gemessen) zum Preis von null statt ~1,5 Cent je Lauf. Gemessen am
    # selben content-Auftrag: 40 s und 16 echte Werkzeugaufrufe. Lokal bleibt
    # als Fallback in der Kette erhalten. Haiku ist in models.yaml weiter
    # definiert, steht aber in keiner Kette mehr.
    STANDARD_MODELL = "cloud_ollama_gptoss"
    #: Womit Jude prueft und Heinz textet: gpt-oss-120b, 128k Kontext,
    #: kostenfrei. Dem 8B-Modell sprachlich deutlich ueberlegen. Lief bis zum
    #: 02.09.2026 ueber Groq – dessen freie Stufe laesst aber nur 8000 Token pro
    #: Minute zu, weshalb dort jede Anfrage in HTTP 413 lief. Ollama Cloud
    #: liefert dasselbe Modell ohne diese Grenze.
    TEXT_MODELL = "cloud_ollama_gptoss"
    #: Der Redakteur. Schreibt alle Texte, fasst selbst nichts an.
    REDAKTEUR = "redakteur"
    #: Prueft seit 04.09.2026 aktiv, was die deterministischen Gates
    #: bestanden hat - loest die automatische Judes-Pruefung ab.
    PROJEKTLEITUNG = "projektleitung"
    #: Wer ihn direkt beauftragen darf. Fuer Text braucht es keinen Umweg ueber
    #: Jude – er prueft ohnehin, was am Ende fertig vorgelegt wird. Bernd traegt
    #: nur Adressen ein, Heike macht Bilder, Joana schreibt Code.
    #: scraper (05.09.2026, Pipeline-Umstellung Phase 1) ist bewusst NICHT mehr
    #: dabei: seine neue Rolle als Trend Scout darf laut eigener Rollenbeschreibung
    #: nie selbst Copy schreiben oder in Auftrag geben (write_copy) - das war die
    #: einzige Stelle, an der er es trotzdem konnte.
    TEXTER = {"social", "content", "sequencer", "leadmanager"}
    #: Das Werkzeug, das die eigene Rolle als Quelle vorschreibt (aus ihrem
    #: QUELLE-Abschnitt) – Pflicht, bevor "nichts zu tun" als erledigt zaehlt.
    #: Gemessen 02.09.2026: eine neutrale Auftragsformulierung allein reichte
    #: nicht – content meldete "nichts zu tun", ohne notion_query je
    #: aufzurufen, obwohl 78 unbearbeitete Eintraege in 'content_stuecke'
    #: lagen. Erzwungen wird hier die Pruefung selbst, nicht ihr Ergebnis.
    QUELLEN_PFLICHT = {
        "outreach": "notion_query", "beobachter": "news_search",
        "projektleitung": "notion_query", "scraper": "news_search",
        "content": "notion_query", "social": "notion_query",
        "sequencer": "notion_query", "analyst": "notion_query",
        "leadmanager": "notion_query",
    }
    #: Das Werkzeug, das eine Rolle tatsaechlich SCHREIBT, bevor sie etwas als
    #: fertig vorlegen darf. Ohne diese Pruefung reichte 'submit_for_review'
    #: allein: engineer legte dieselben (teils gar nicht mehr existierenden)
    #: HTML-Pfade ueber 5 Revisionsrunden hinweg erneut vor, ohne ein einziges
    #: Mal coding_write aufzurufen (gemessen 04.09.2026, agent_runs 46ddb40d/
    #: 4298eafd zeigen submit_for_review ohne jedes coding_write davor).
    #: Werte als Tupel, auch wenn meist nur ein Werkzeug noetig ist – content/
    #: social brauchen zwei (Text UND Bild). Audit 04.09.2026 (drei Durchlaeufe,
    #: volle agent_runs-Historie): dasselbe Muster wie engineer/coding_write lag
    #: unbehandelt bei sequencer/scraper (write_copy strukturell ungenutzt),
    #: content/social (generate_image ueber die GESAMTE Historie 0x, auch nach
    #: der eigenen BILD-PFLICHT-Verschaerfung vom selben Tag), designer
    #: (notion_update 0x trotz eigenem ZIEL) und leadmanager (write_copy 0x,
    #: das extremste Beispiel).
    SCHREIB_PFLICHT = {
        "engineer": ("coding_write",),
        "sequencer": ("write_copy",),
        # scraper (05.09.2026, Pipeline-Umstellung Phase 1): write_copy ist ihm
        # jetzt verboten (Trend Scout, siehe TEXTER oben) - sein tatsaechliches
        # Schreib-Werkzeug ist ingest_document, mit dem er jede neue Meldung
        # ablegt, bevor er die Rangliste vorlegt.
        "scraper": ("ingest_document",),
        "content": ("write_copy", "generate_image"),
        "social": ("write_copy", "generate_image"),
        "designer": ("notion_update",),
        "leadmanager": ("write_copy",),
    }
    # 16 war zu knapp: allein die 8 VERBINDLICHEN QUELLEN (project_files/*.md)
    # kosten schon 8 Schritte, bevor die eigentliche Arbeit beginnt – gemessen
    # 02.09.2026, mehrere Laeufe liefen deshalb ins Limit statt zur echten Aufgabe.
    # 26 war weiterhin zu knapp: im Kontrolllauf vom 03.09.2026 liefen Coder,
    # scraper und sequencer bei genau 27 Aufrufen ins Limit und zaehlten als
    # 'fehlgeschlagen', obwohl sie arbeiteten – abgeschnitten, nicht gescheitert.
    # Die drei sind die werkzeugintensivsten Rollen (Recherche ueber mehrere
    # Quellen, dann ablegen). 40 laesst dieselbe Kette zu Ende laufen.
    TOOL_SCHRITTE = 40       # lesen (8x Pflichtquellen), suchen, pruefen, ablegen, notieren
    PROMPT_NOTES = 40        # wie viele Notizen in den Systemprompt wandern (Obergrenze: FeedbackStore.MAX_NOTES)
    PROMPT_LEHREN = 12       # wie viele Lehren/Lob in den Systemprompt wandern (Obergrenze: FeedbackStore.MAX_LEHREN)

    def __init__(self, registry, router, ai_router=None):
        self.registry = registry
        self.router = router
        # AI-Router-Umbau, Stage 2: optional, None solange nichts es setzt -
        # bestehende SubAgentService(registry, router)-Aufrufe (Skripte, Tests,
        # Tools) bleiben unveraendert gueltig.
        self.ai_router = ai_router
        # Teil 3, Stage C (jolly-napping-tulip.md): Agenten-Stammdaten-CRUD
        # nach services/team_registry.py ausgelagert. Diese Klasse bleibt die
        # Fassade, an die jeder bestehende Aufrufer (tools/chef.py, web/routes,
        # Tests) unveraendert weiter andockt - siehe die Delegationen unten.
        self._agenten = AgentRegistry(registry)

    def _agenten_sicherstellen(self) -> AgentRegistry:
        # Die Testsuite baut SubAgentService teils ueber __new__ auf und
        # ueberspringt __init__ bewusst (siehe tests/test_betrieb.py::_dienst),
        # um einzelne Tool-Closures ohne vollen App-Aufbau zu pruefen - danach
        # wird nur .path gesetzt. Ohne dieses Nachruesten waere das eine
        # Verhaltensaenderung fuer einen real genutzten, aelteren Testpfad,
        # keine reine Verschiebung mehr.
        if "_agenten" not in self.__dict__:
            self._agenten = AgentRegistry(getattr(self, "registry", None))
        return self._agenten

    @property
    def path(self):
        return self._agenten_sicherstellen().path

    @path.setter
    def path(self, value) -> None:
        # Tests leiten das auf ein tmp_path um, um von der echten
        # sub_agents.json isoliert zu bleiben - muss deshalb ein echter
        # Setter sein, keine reine Lesefassade.
        self._agenten_sicherstellen().path = value

    # ------------------------------------------------------------ Speicher

    def _load(self) -> dict:
        return self._agenten_sicherstellen()._load()

    def _save(self, data: dict) -> None:
        self._agenten_sicherstellen()._save(data)

    @staticmethod
    def _key(name: str) -> str:
        return AgentRegistry._key(name)

    # ---------------------------------------------------------------- API

    def available_skills(self) -> list[str]:
        return self._agenten_sicherstellen().available_skills()

    def list(self) -> list[dict]:
        return self._agenten_sicherstellen().list()

    def get(self, name: str) -> dict | None:
        return self._agenten_sicherstellen().get(name)

    def create(self, name: str, role: str, skills: list[str], model: str | None = None,
               person: str | None = None, alter: int | None = None) -> dict:
        return self._agenten_sicherstellen().create(name, role, skills, model, person, alter)

    def delete(self, name: str) -> dict:
        return self._agenten_sicherstellen().delete(name)

    # ------------------------------------------------------- Eigenes Gedächtnis

    def _rueckmeldung_sicherstellen(self) -> FeedbackStore:
        if "_rueckmeldung" not in self.__dict__:
            self._rueckmeldung = FeedbackStore(self._key)
        return self._rueckmeldung

    def notes(self, name: str) -> list[dict]:
        return self._rueckmeldung_sicherstellen().notes(name)

    def remember(self, name: str, note: str) -> dict:
        return self._rueckmeldung_sicherstellen().remember(name, note)

    def forget_notes(self, name: str) -> dict:
        return self._rueckmeldung_sicherstellen().forget_notes(name)

    # ---------------------------------------------------------- Aus Fehlern lernen

    def lehren(self, name: str) -> list[dict]:
        """Was diesem Mitarbeiter schon einmal beanstandet wurde."""
        return self._rueckmeldung_sicherstellen().lehren(name)

    def lehre_merken(self, name: str, beanstandung: str, quelle: str = "Jude") -> dict:
        """Eine Beanstandung dauerhaft festhalten – einmal, nicht hundertmal.

        Ohne das wiederholt ein Mitarbeiter denselben Fehler bei jedem Lauf:
        er sieht die Revision, arbeitet sie ab und hat beim naechsten Mal
        wieder keine Ahnung davon. Gleichlautende Beanstandungen werden
        zusammengefasst und mitgezaehlt; was oft kam, steht oben.
        """
        return self._rueckmeldung_sicherstellen().lehre_merken(name, beanstandung, quelle)

    def forget_lehren(self, name: str) -> dict:
        return self._rueckmeldung_sicherstellen().forget_lehren(name)

    def lob(self, name: str) -> list[dict]:
        """Was an diesem Mitarbeiter schon einmal gelobt wurde."""
        return self._rueckmeldung_sicherstellen().lob(name)

    def lob_merken(self, name: str, anerkennung: str, quelle: str = "Jude") -> dict:
        """Das Gegenstück zu ``lehre_merken``: bisher hielt das Team nur fest,
        was schiefging, nie was gut war – ein Mitarbeiter, der etwas richtig
        gemacht hat, bekam das nie bestaetigt und wusste beim naechsten Lauf
        nicht, woran er anknuepfen soll."""
        return self._rueckmeldung_sicherstellen().lob_merken(name, anerkennung, quelle)

    def forget_lob(self, name: str) -> dict:
        return self._rueckmeldung_sicherstellen().forget_lob(name)

    def _hinweis_tool(self, spec: dict):
        """Einen Kollegen direkt auf etwas hinweisen.

        Ein Fund ist nur etwas wert, wenn er bei dem landet, der ihn braucht.
        Der Hinweis geht ins Gedaechtnis des Kollegen und steht damit in seinem
        naechsten Systemprompt – er muss nicht danach suchen und der Beobachter
        muss nicht warten. Ueber Jude laeuft das nicht: eine Beobachtung ist
        Zuarbeit, keine Entscheidung.
        """
        from core.tool_registry import Tool

        wer = spec.get("person") or spec["name"]

        def hinweisen(kollege: str, hinweis: str) -> dict:
            kollege = str(kollege).strip().lower()
            ziel = self.get(kollege)
            if ziel is None:
                bekannt = ", ".join(sorted(a["name"] for a in self.list()))
                raise ValueError(f"Kein Kollege namens {kollege!r}. Bekannt: {bekannt}.")
            if kollege == self._key(spec["name"]):
                raise ValueError("Dir selbst brauchst du nichts mitzuteilen – nutze remember_finding.")
            if len(str(hinweis).strip()) < 20:
                raise ValueError("Der Hinweis ist zu duenn. Nenne Quelle, Kernaussage und "
                                 "warum es fuer den Kollegen brauchbar ist.")
            self.remember(kollege, f"[Hinweis von {wer}] {hinweis}")
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

    def _text_tool(self, spec: dict):
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
                self.lehre_merken(
                    spec["name"],
                    "Deine Auftraege an Heinz kamen ohne jede Angabe zum Empfaenger. "
                    "Ohne Empfaenger, Branche und Taetigkeit kann er nur Allgemeinplaetze "
                    "schreiben – gib sie mit, sonst wird der Text zurueckgewiesen.",
                    quelle="Auftragspruefung")
            ergebnis = self.run(self.REDAKTEUR, (
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

    def _gate_sicherstellen(self) -> ContentGate:
        if "_gate" not in self.__dict__:
            self._gate = ContentGate(self._rueckmeldung_sicherstellen())
        return self._gate

    def _fehlende_datei(self, pfad: str) -> str | None:
        return self._gate_sicherstellen()._fehlende_datei(pfad)

    def _bilddateien(self, quelle: str) -> list[str]:
        return self._gate_sicherstellen()._bilddateien(quelle)

    def _laenge_pruefen(self, art: str, inhalt: str, neu: bool) -> None:
        self._gate_sicherstellen()._laenge_pruefen(art, inhalt, neu)

    def _review_tool(self, spec: dict):
        """Fertiges vorlegen – ohne zu warten.

        Der Mitarbeiter meldet ein Erzeugnis zur Abnahme und arbeitet sofort
        weiter. Es geht ihn erst wieder etwas an, wenn eine Revision kommt.
        """
        from core.tool_registry import Tool
        from services.review import ARTEN, ReviewQueue
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
                fehlend = self._fehlende_datei(pfad)
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
            self._laenge_pruefen(art, inhalt, neu=not ziel)
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

    def _pruefung_tool(self, spec: dict):
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

    def _memory_tool(self, name: str):
        from core.tool_registry import Tool
        return Tool(
            name="remember_finding",
            description=("Hält eine dauerhafte Notiz fest (z. B. kontaktierte Firma, Absage, "
                         "erfolgreiche Ansprache). Nutze das nach jedem verwertbaren Ergebnis."),
            func=lambda note: self.remember(name, note),
            param_schema={"type": "object", "properties": {
                "note": {"type": "string", "description": "Was dauerhaft erinnert werden soll."}},
                "required": ["note"]},
        )

    def _bericht_tool(self, spec: dict):
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

    def _brauchbares_modell(self, *namen: str | None) -> str | None:
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

    def _build_agent(self, spec: dict):
        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        sub = ToolRegistry()
        sub.set_confirmations(self.registry.confirmations)
        sub.agent_name = spec["name"]
        modell = self._brauchbares_modell(spec.get("model") or self.STANDARD_MODELL)
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
            sub.register(self._memory_tool(spec["name"]))
            # Redakteur legt nichts vor - das macht, wer sie beauftragt hat
            # (eigene Rolle + AUSNAHME VON DER ABNAHME-PFLICHT weiter unten).
            # Bisher nur eine Prompt-Regel: submit_for_review wurde trotzdem
            # automatisch registriert und in mehreren Laeufen tatsaechlich
            # aufgerufen (04.09.2026 Audit). Verbot jetzt auf Code-Ebene.
            if spec["name"] != self.REDAKTEUR:
                sub.register(self._review_tool(spec))
            sub.register(self._bericht_tool(spec))
            sub.register(self._hinweis_tool(spec))
            if spec["name"] in self.TEXTER:
                sub.register(self._text_tool(spec))
            if spec["name"] == self.PROJEKTLEITUNG:
                for tool in self._pruefung_tool(spec):
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
                     if spec["name"] != self.REDAKTEUR else
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
        gelernt = self.lehren(spec["name"])[:self.PROMPT_LEHREN]
        if gelernt:
            zeilen = "\n".join(
                f"- {e['text']}" + (f"  (schon {e['anzahl']}x beanstandet)"
                                    if int(e.get("anzahl", 1)) > 1 else "")
                for e in gelernt)
            prompt += ("\n\nDAS WURDE DIR SCHON BEANSTANDET – mach es nicht wieder:\n"
                       + zeilen)
        gelobt = self.lob(spec["name"])[:self.PROMPT_LEHREN]
        if gelobt:
            zeilen = "\n".join(
                f"- {e['text']}" + (f"  (schon {e['anzahl']}x bestaetigt)"
                                    if int(e.get("anzahl", 1)) > 1 else "")
                for e in gelobt)
            prompt += ("\n\nDAS HAST DU GUT GEMACHT – daran anknuepfen:\n"
                       + zeilen)
        notes = self.notes(spec["name"])
        if notes:
            recent = "\n".join(f"- {item['note']}" for item in notes[-self.PROMPT_NOTES:])
            prompt += ("\n\nWas du bisher festgehalten hast (nicht doppelt bearbeiten):\n" + recent)
        return Agent(self.router, sub, system_prompt=prompt,
                     max_tool_steps=self.TOOL_SCHRITTE if werkzeugfaehig else 0,
                     force_model=modell, agent_name=spec["name"],
                     task_type=spec.get("task_type", "routine_dispatch"),
                     use_ai_router=bool(spec.get("use_ai_router", False)),
                     ai_router=self.ai_router)

    def run(self, name: str, task: str) -> dict:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        task = str(task).strip()
        if not task:
            raise ValueError("Es wurde keine Aufgabe angegeben.")
        agent = self._build_agent(spec)
        # Werkzeugaufrufe mitschreiben: ohne sie ist hinterher nicht
        # nachvollziehbar, woran ein Lauf haengengeblieben ist.
        #
        # Die Werkzeugschicht wirft bei einem Fehlschlag keine Ausnahme, sondern
        # gibt ihn als Text zurueck (tool_registry.execute). Der Agent formuliert
        # daraus eine hoefliche Antwort, und der Lauf sah bisher gelungen aus:
        # 11 von 11 Laeufen standen auf 'abgeschlossen', obwohl 6 nichts
        # zustande gebracht hatten. Deshalb wird hier jeder Rueckgabewert geprueft.
        werkzeuge: list[str] = []
        fehlschlaege: list[str] = []
        # IDs, die submit_for_review in DIESEM Lauf tatsaechlich anlegte oder
        # weiterreichte – ohne die laesst sich eine Vorlage, die ohne die
        # Pflicht-Schreibfunktion vorgelegt wurde (SCHREIB_PFLICHT), nicht
        # gezielt zurueckweisen, nur der ganze Lauf als 'teilweise' markieren.
        eingereichte_ids: list[str] = []
        original = agent.tools.execute
        # Schutz gegen den Leerlauf, der die Werkzeugketten auffrisst: derselbe
        # Aufruf mit denselben Argumenten, unmittelbar hintereinander. Gemessen
        # 05.09.2026: von 769 Aufrufen an einem Tag waren 6 % erzeugend und
        # 67 % melden/listen – projektleitung holte 164x 'pruefungsliste' und
        # traf dabei EINE Entscheidung. Das Werkzeug liefert etwas, das wie neue
        # Arbeit aussieht, das Modell verarbeitet es und ruft erneut auf.
        #
        # Bewusst nur bei UNMITTELBARER Wiederholung: hat der Mitarbeiter
        # zwischendurch etwas anderes getan (z. B. entschieden), hat sich die
        # Lage geaendert und dieselbe Abfrage ist berechtigt. Das ist kein
        # Deckel auf die Zahl der Schritte, sondern macht Leerlauf unmoeglich.
        letzter: dict = {"schluessel": None, "ergebnis": None, "zaehler": 0}

        def _schluessel(werkzeug, argumente):
            try:
                return werkzeug + "|" + json.dumps(argumente, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                return werkzeug + "|" + repr(argumente)

        def mitschreiben(werkzeug, argumente):
            werkzeuge.append(werkzeug)
            schluessel = _schluessel(werkzeug, argumente)
            if schluessel == letzter["schluessel"]:
                letzter["zaehler"] += 1
                if letzter["zaehler"] >= 2:
                    return (f"'{werkzeug}' wurde gerade eben mit genau denselben Argumenten "
                            f"aufgerufen und liefert unveraendert dasselbe. Der Aufruf wurde "
                            f"nicht erneut ausgefuehrt. Es hat sich seitdem nichts geaendert – "
                            f"nimm das vorherige Ergebnis und mach den naechsten Schritt deiner "
                            f"Aufgabe, oder beende den Lauf, wenn nichts mehr zu tun ist.")
            else:
                letzter["schluessel"], letzter["zaehler"] = schluessel, 1
            ergebnis = original(werkzeug, argumente)
            letzter["ergebnis"] = ergebnis
            if self._ist_fehlschlag(ergebnis):
                fehlschlaege.append(str(ergebnis).strip()[:300])
            if werkzeug == "submit_for_review" and isinstance(ergebnis, dict) and ergebnis.get("id"):
                eingereichte_ids.append(ergebnis["id"])
            return ergebnis
        agent.tools.execute = mitschreiben
        ergebnis_id = uuid.uuid4().hex[:12]
        self._lauf_beginnen(ergebnis_id, spec, task)
        begonnen = time.monotonic()
        vorher = self.router_verbrauch()
        try:
            answer, status, blockers = agent.process_input(task), "abgeschlossen", []
        except Exception as exc:
            answer, status = "", "fehlgeschlagen"
            blockers = [f"{type(exc).__name__}: {exc}"]
        if status != "fehlgeschlagen":
            status, blockers = self._bewerten(werkzeuge, fehlschlaege,
                                              hat_werkzeuge=bool(agent.tools.tools))
        pflicht = self.QUELLEN_PFLICHT.get(spec["name"])
        if status == "abgeschlossen" and pflicht and pflicht not in werkzeuge:
            status = "teilweise"
            blockers = blockers + [f"Pflicht-Quelle nicht abgefragt: '{pflicht}' fehlt in den "
                                   f"Werkzeugaufrufen – 'nichts zu tun' zaehlt nur nach echter Pruefung."]
        schreib_pflicht = self.SCHREIB_PFLICHT.get(spec["name"], ())
        fehlende_pflicht = [w for w in schreib_pflicht if w not in werkzeuge]
        if fehlende_pflicht and eingereichte_ids:
            from services.review import ReviewQueue
            queue = ReviewQueue()
            fehlend_text = " und ".join(f"'{w}'" for w in fehlende_pflicht)
            for rid in eingereichte_ids:
                try:
                    voll = queue.zeigen(rid)
                    if voll and voll.get("status") == "pruefung":
                        queue.revision(rid, f"Jude: Vorgelegt, ohne {fehlend_text} in diesem Lauf "
                                        f"aufzurufen – das ist dieselbe, unveraenderte Vorlage wie zuvor. "
                                        f"Erst wirklich schreiben/erzeugen, dann vorlegen.")
                except Exception:
                    pass
            status = "teilweise"
            blockers = blockers + [f"Pflicht-Werkzeug(e) nicht aufgerufen: {fehlend_text} fehlt/fehlen – "
                                   f"vorgelegte Datei(en) automatisch zurueckgewiesen."]
        # Ergebnisformat nach dem Adapter-Vertrag der Agenten-Standards:
        # agent_id, task_id, status, output, blockers und token_usage sind Pflicht.
        nachher = self.router_verbrauch()
        dauer = int((time.monotonic() - begonnen) * 1000)
        self._protokollieren(ergebnis_id, spec, task, status, answer, blockers,
                             agent.last_model, nachher, vorher, dauer, werkzeuge)
        # Der Mitarbeiter ist hier fertig – die Chefpruefung haelt ihn nicht auf.
        # Der Redakteur legt nichts vor, bei ihm gaebe es nichts zu pruefen.
        geprueft = ([] if spec["name"] == self.REDAKTEUR
                    else self._chefpruefung(spec["name"]))
        return {
            "agent_id": self._key(spec["name"]),
            "task_id": ergebnis_id,
            "agent": spec["name"],
            "person": spec.get("person"),
            "alter": spec.get("alter"),
            "role": spec["role"],
            "skills": spec["skills"],
            "status": status,
            "output": {"answer": answer},
            "answer": answer,          # Rückwärtskompatibel für GUI und Werkzeuge
            "blockers": blockers,
            "model": agent.last_model,
            "token_usage": {
                "model": agent.last_model,
                "input_tokens": nachher["input"] - vorher["input"],
                "output_tokens": nachher["output"] - vorher["output"],
                "estimated_cost": round(nachher["cost"] - vorher["cost"], 6),
                "currency": "USD",
            },
            "duration_ms": dauer,
            "tool_calls": werkzeuge,
            "chefpruefung": geprueft,
        }

    #: Rueckgabetexte, mit denen ``ToolRegistry.execute`` einen Fehlschlag meldet.
    FEHLERMUSTER = ("' fehlgeschlagen:", "' nicht gefunden.",
                    "Tool-Argumente müssen ein Objekt sein.",
                    "Aktion konnte nicht vorgemerkt werden:")

    @classmethod
    def _ist_fehlschlag(cls, ergebnis) -> bool:
        text = str(ergebnis)
        return any(muster in text for muster in cls.FEHLERMUSTER)

    @staticmethod
    def _bewerten(werkzeuge: list[str], fehlschlaege: list[str],
                  hat_werkzeuge: bool = True) -> tuple[str, list[str]]:
        """Ehrlicher Status statt pauschalem 'abgeschlossen'.

        Bewertet wird ausschliesslich Gemessenes – welche Werkzeuge liefen und
        welche meldeten einen Fehler. Der Antworttext wird bewusst nicht
        ausgewertet: ein Modell, das seinen Misserfolg schoenschreibt, wuerde
        sonst genau die Luecke wieder aufreissen.

        ``fehlgeschlagen``  kein Werkzeug hat funktioniert
        ``teilweise``       einzelne Werkzeuge scheiterten, oder es wurde gar
                            keines benutzt (das Modell hat den Aufruf nur
                            hingeschrieben statt ihn auszufuehren)
        ``abgeschlossen``   alle Werkzeugaufrufe gingen durch
        """
        if not hat_werkzeuge:
            # Der Redakteur hat keine Werkzeuge; sein Ergebnis IST der Text.
            return "abgeschlossen", []
        if not werkzeuge:
            return "teilweise", ["Kein Werkzeug benutzt – das Ergebnis wurde nirgends abgelegt."]
        if not fehlschlaege:
            return "abgeschlossen", []
        if len(fehlschlaege) >= len(werkzeuge):
            return "fehlgeschlagen", fehlschlaege
        return "teilweise", fehlschlaege

    def _chefpruefung(self, agent_name: str) -> list[dict]:
        return self._gate_sicherstellen().pruefen(agent_name)

    def _lauf_beginnen(self, lauf_id, spec, task) -> None:
        """Sofort beim Start eine Zeile anlegen (status='laufend') – sonst
        taucht ein laufender Auftrag nirgends auf, bevor er fertig ist, und
        es ist von aussen nicht zu sehen, ob gerade jemand arbeitet."""
        try:
            from services.database import connection
            with connection() as db:
                db.execute(
                    "INSERT INTO agent_runs(id,created_at,agent,person,task,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (lauf_id, datetime.now(timezone.utc).isoformat(), spec["name"],
                     spec.get("person"), task[:4000], "laufend"))
        except Exception as exc:
            logger.warning("Laufstart nicht protokolliert: %s", exc)

    def laufende_bereinigen(self) -> int:
        """Beim Start aufräumen: ein 'laufend' aus einem früheren Prozess kann
        nur ein Geist sein – der Prozess, der ihn hätte fertigstellen können,
        ist weg. Ohne das zeigt die Live-Anzeige für immer 'aktiv', obwohl
        seit einem Neustart niemand mehr daran arbeitet."""
        from services.database import connection
        with connection() as db:
            cur = db.execute(
                "UPDATE agent_runs SET status='unterbrochen',"
                " blockers='Prozess neu gestartet, Lauf verwaist' WHERE status='laufend'")
            return cur.rowcount

    def status_uebersicht(self) -> list[dict]:
        """Für jeden Mitarbeiter: arbeitet er gerade, oder wann/wie war sein
        letzter Lauf – nicht nur eine Liste, die meistens leer ist, weil
        gerade niemand aktiv läuft. 'Niemand arbeitet gerade' ist etwas
        anderes als 'hier steht nichts', und beides muss sich unterscheiden."""
        from services.database import connection
        with connection() as db:
            aktiv = {r["agent"]: dict(r) for r in db.execute(
                "SELECT agent,created_at,task FROM agent_runs "
                "WHERE status='laufend' ORDER BY created_at").fetchall()}
            letzte: dict[str, dict] = {}
            for r in db.execute(
                    "SELECT agent,status,created_at FROM agent_runs "
                    "WHERE status!='laufend' ORDER BY created_at DESC").fetchall():
                letzte.setdefault(r["agent"], dict(r))
        ergebnis = []
        for spec in self.list():
            name = spec["name"]
            eintrag = {"agent": name, "person": spec.get("person")}
            if name in aktiv:
                eintrag.update(status="aktiv", seit=aktiv[name]["created_at"], task=aktiv[name]["task"])
            elif name in letzte:
                eintrag.update(status="idle", letzter_status=letzte[name]["status"],
                                letzter_lauf_am=letzte[name]["created_at"])
            else:
                eintrag["status"] = "noch_nie"
            ergebnis.append(eintrag)
        return ergebnis

    def _protokollieren(self, lauf_id, spec, task, status, answer, blockers,
                        modell, nachher, vorher, dauer, werkzeuge) -> None:
        """Die beim Start angelegte Zeile mit dem Ergebnis abschliessen –
        Nachweis, Fehlersuche und spaeter die Grundlage fuer das Training des
        Teams. Ein Protokollfehler darf den Lauf selbst nie scheitern lassen."""
        try:
            from services.database import connection
            with connection() as db:
                db.execute(
                    "UPDATE agent_runs SET status=?,answer=?,blockers=?,model=?,"
                    "input_tokens=?,output_tokens=?,cost_usd=?,duration_ms=?,tool_calls=? "
                    "WHERE id=?",
                    (status, (answer or "")[:8000], " | ".join(blockers)[:2000], modell,
                     nachher["input"] - vorher["input"], nachher["output"] - vorher["output"],
                     round(nachher["cost"] - vorher["cost"], 6), dauer, ",".join(werkzeuge)[:1000],
                     lauf_id))
        except Exception as exc:
            logger.warning("Agentenlauf nicht protokolliert: %s", exc)

    def router_verbrauch(self) -> dict:
        """Zwischenstand des Monatsverbrauchs – Differenz vor/nach einem Lauf
        ergibt den Verbrauch dieses Laufs."""
        try:
            usage = self.router.status()["usage"]
            return {"input": int(usage["input_tokens"]), "output": int(usage["output_tokens"]),
                    "cost": float(usage["cost_usd"])}
        except Exception:
            return {"input": 0, "output": 0, "cost": 0.0}
