"""Benannte Sub-Agenten ("Mitarbeiter") mit eigener Rolle und Werkzeug-Auswahl.

Jude kann spezialisierte Sub-Agenten anlegen (Name, Rolle, erlaubte Skills),
sie wie Mitarbeiter mit Aufgaben betrauen und wieder entfernen. Jeder Sub-Agent
bekommt eine eigene, eingeschränkte Werkzeugliste; sicherheitsrelevante Aktionen
laufen weiterhin über dieselbe Bestätigungs-Warteschlange.
"""

from __future__ import annotations

# DATA_DIR wird in dieser Datei selbst nicht mehr gelesen (die Pfad-Logik
# steckt jetzt in team_registry.py/team_feedback.py) - der Import bleibt
# trotzdem stehen: die Testsuite patcht "services.team.DATA_DIR" gezielt an
# mehreren Stellen, und monkeypatch.setattr verlangt, dass das Attribut
# ueberhaupt existiert.
from core.paths import DATA_DIR
from services.team_builder import AgentBuilder
from services.team_feedback import FeedbackStore
from services.team_gate import ContentGate
from services.team_registry import AgentRegistry
from services.team_runner import AgentRunner
from services.team_tools import ToolFactory


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
        # generate_image war hier bis 07.09.2026 mit dabei - widersprach aber
        # der tatsaechlichen Arbeitsteilung (Heike/designer macht ALLE
        # Bildmotive, siehe ihre eigene Rolle: "Du machst die Bildmotive").
        # Jede Ad-hoc-Anweisung "ruf generate_image nicht selbst auf" lief
        # gegen diese Pflicht - ein Mitarbeiter, der sie befolgte, bekam sein
        # eigenes Erzeugnis automatisch zur Revision zurueckgeschickt, weil
        # die Pflicht-Werkzeug-Pruefung generate_image vermisste.
        "content": ("write_copy",),
        "social": ("write_copy",),
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

    def set_pausiert(self, name: str, pausiert: bool) -> dict:
        return self._agenten_sicherstellen().set_pausiert(name, pausiert)

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

    def _werkzeuge_sicherstellen(self) -> ToolFactory:
        if "_werkzeuge" not in self.__dict__:
            self._werkzeuge = ToolFactory(self)
        return self._werkzeuge

    def _hinweis_tool(self, spec: dict):
        return self._werkzeuge_sicherstellen().hinweis_tool(spec)

    def _text_tool(self, spec: dict):
        return self._werkzeuge_sicherstellen().text_tool(spec)

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
        return self._werkzeuge_sicherstellen().review_tool(spec)

    def _pruefung_tool(self, spec: dict):
        return self._werkzeuge_sicherstellen().pruefung_tool(spec)

    def _memory_tool(self, name: str):
        return self._werkzeuge_sicherstellen().memory_tool(name)

    def _bericht_tool(self, spec: dict):
        return self._werkzeuge_sicherstellen().bericht_tool(spec)

    def _baumeister_sicherstellen(self) -> AgentBuilder:
        if "_baumeister" not in self.__dict__:
            self._baumeister = AgentBuilder(self, self._werkzeuge_sicherstellen())
        return self._baumeister

    def _brauchbares_modell(self, *namen: str | None) -> str | None:
        return self._baumeister_sicherstellen().brauchbares_modell(*namen)

    def _build_agent(self, spec: dict):
        return self._baumeister_sicherstellen().build_agent(spec)

    def _ausfuehrung_sicherstellen(self) -> AgentRunner:
        if "_ausfuehrung" not in self.__dict__:
            self._ausfuehrung = AgentRunner(self, self._baumeister_sicherstellen())
        return self._ausfuehrung

    def run(self, name: str, task: str) -> dict:
        return self._ausfuehrung_sicherstellen().run(name, task)

    @classmethod
    def _ist_fehlschlag(cls, ergebnis) -> bool:
        return AgentRunner._ist_fehlschlag(ergebnis)

    @staticmethod
    def _bewerten(werkzeuge: list[str], fehlschlaege: list[str],
                  hat_werkzeuge: bool = True) -> tuple[str, list[str]]:
        return AgentRunner._bewerten(werkzeuge, fehlschlaege, hat_werkzeuge)

    def _chefpruefung(self, agent_name: str) -> list[dict]:
        return self._gate_sicherstellen().pruefen(agent_name)

    def _lauf_beginnen(self, lauf_id, spec, task) -> None:
        self._ausfuehrung_sicherstellen()._lauf_beginnen(lauf_id, spec, task)

    def laufende_bereinigen(self) -> int:
        return self._ausfuehrung_sicherstellen().laufende_bereinigen()

    def status_uebersicht(self) -> list[dict]:
        return self._ausfuehrung_sicherstellen().status_uebersicht()

    def _protokollieren(self, lauf_id, spec, task, status, answer, blockers,
                        modell, nachher, vorher, dauer, werkzeuge) -> None:
        self._ausfuehrung_sicherstellen()._protokollieren(
            lauf_id, spec, task, status, answer, blockers, modell, nachher, vorher, dauer, werkzeuge)

    def router_verbrauch(self) -> dict:
        return self._ausfuehrung_sicherstellen().router_verbrauch()
