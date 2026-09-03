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
from services.marke import BRAND_BRIEF

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-zÄÖÜäöüß0-9 _-]{2,40}$")


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
    #: Wer ihn direkt beauftragen darf. Fuer Text braucht es keinen Umweg ueber
    #: Jude – er prueft ohnehin, was am Ende fertig vorgelegt wird. Bernd traegt
    #: nur Adressen ein, Heike macht Bilder, Joana schreibt Code.
    TEXTER = {"social", "content", "sequencer", "scraper", "leadmanager"}
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
    # 16 war zu knapp: allein die 8 VERBINDLICHEN QUELLEN (project_files/*.md)
    # kosten schon 8 Schritte, bevor die eigentliche Arbeit beginnt – gemessen
    # 02.09.2026, mehrere Laeufe liefen deshalb ins Limit statt zur echten Aufgabe.
    TOOL_SCHRITTE = 26       # lesen (8x Pflichtquellen), suchen, pruefen, ablegen, notieren
    MAX_NOTES = 500          # Obergrenze je Agent
    PROMPT_NOTES = 40        # wie viele davon in den Systemprompt wandern
    MAX_LEHREN = 40          # Beanstandungen je Mitarbeiter
    PROMPT_LEHREN = 12       # die haeufigsten davon stehen im Prompt
    #: Was nicht ins Gedaechtnis darf – Stoerungsmeldungen statt Ergebnisse.
    KEINE_NOTIZ = ("fehler beim", "fehlgeschlagen", "nicht erreichbar",
                   "kann nicht hergestellt werden", "keine verbindung",
                   "verbindung zu", "timeout", "konnte nicht abgerufen")

    def __init__(self, registry, router):
        self.registry = registry
        self.router = router
        self.path = DATA_DIR / "sub_agents.json"

    # ------------------------------------------------------------ Speicher

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    # ---------------------------------------------------------------- API

    def available_skills(self) -> list[str]:
        return sorted(self.registry.tools)

    def list(self) -> list[dict]:
        return sorted(self._load().values(), key=lambda a: a["name"].casefold())

    def get(self, name: str) -> dict | None:
        return self._load().get(self._key(name))

    def create(self, name: str, role: str, skills: list[str], model: str | None = None,
               person: str | None = None, alter: int | None = None) -> dict:
        name = str(name).strip()
        role = str(role).strip()
        if not _NAME_RE.match(name):
            raise ValueError("Name: 2–40 Zeichen, Buchstaben/Zahlen/Leerzeichen/-/_")
        if len(role) < 5:
            raise ValueError("Bitte eine Rollenbeschreibung angeben.")
        skills = list(dict.fromkeys(skills or []))
        unknown = [s for s in skills if s not in self.registry.tools]
        if unknown:
            raise ValueError("Unbekannte Skills: " + ", ".join(unknown))
        data = self._load()
        # Name und Alter geben dem Mitarbeiter eine Identitaet: der Agent stellt
        # sich damit vor und Tino kann ihn ansprechen wie einen Kollegen.
        vorhanden = data.get(self._key(name), {})
        spec = {"name": name, "role": role, "skills": skills, "model": model,
                "person": (person or vorhanden.get("person") or "").strip() or None,
                "alter": int(alter) if alter is not None else vorhanden.get("alter"),
                "created_at": vorhanden.get("created_at") or datetime.now(timezone.utc).isoformat()}
        data[self._key(name)] = spec
        self._save(data)
        return spec

    def delete(self, name: str) -> dict:
        data = self._load()
        if self._key(name) not in data:
            raise KeyError(f"Kein Sub-Agent namens {name}.")
        removed = data.pop(self._key(name))
        self._save(data)
        return {"name": removed["name"], "status": "entfernt"}

    # ------------------------------------------------------- Eigenes Gedächtnis

    def _memory_path(self, name: str) -> Path:
        return DATA_DIR / "sub_agent_memory" / f"{self._key(name)}.json"

    def notes(self, name: str) -> list[dict]:
        try:
            return json.loads(self._memory_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def remember(self, name: str, note: str) -> dict:
        """Hält eine Erkenntnis dauerhaft fest.

        Sub-Agenten wurden bisher bei jedem Aufruf neu gebaut und danach
        verworfen – ein Akquise-Agent fing damit jedes Mal bei null an. Die
        Notizen überdauern den Lauf und werden beim nächsten Aufruf wieder in
        den Systemprompt gelegt.
        """
        note = str(note).strip()
        if not note:
            raise ValueError("Die Notiz ist leer.")
        # Das Gedaechtnis ist fuer Ergebnisse da, nicht fuer Stoerungen. Bernds
        # Gedaechtnis bestand aus drei Fehlermeldungen und einem Platzhalter –
        # beim naechsten Lauf las er als 'was ich bisher weiss', dass nichts geht.
        # Stoerungen gehoeren in die Blocker des Laufs.
        if any(muster in note.casefold() for muster in self.KEINE_NOTIZ):
            raise ValueError("Stoerungen gehoeren nicht ins Gedaechtnis, sondern in die "
                             "Antwort des Laufs. Halte hier nur Ergebnisse fest "
                             "(z. B. 'Firma X, Adresse Y aus dem Impressum').")
        items = self.notes(name)
        items.append({"note": note[:800], "created_at": datetime.now(timezone.utc).isoformat()})
        items = items[-self.MAX_NOTES:]
        path = self._memory_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"agent": name, "gespeichert": note[:120], "notizen_gesamt": len(items)}

    def forget_notes(self, name: str) -> dict:
        path = self._memory_path(name)
        removed = len(self.notes(name))
        path.unlink(missing_ok=True)
        return {"agent": name, "geloescht": removed}

    # ---------------------------------------------------------- Aus Fehlern lernen

    def _lehren_pfad(self, name: str) -> Path:
        return DATA_DIR / "sub_agent_lessons" / f"{self._key(name)}.json"

    def lehren(self, name: str) -> list[dict]:
        """Was diesem Mitarbeiter schon einmal beanstandet wurde."""
        try:
            return json.loads(self._lehren_pfad(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    @staticmethod
    def _kern(text: str) -> str:
        """Vergleichsform einer Beanstandung – Interpunktion und Fuellwoerter raus."""
        return re.sub(r"[^a-z0-9 ]+", "", str(text).casefold())[:90].strip()

    def lehre_merken(self, name: str, beanstandung: str, quelle: str = "Jude") -> dict:
        """Eine Beanstandung dauerhaft festhalten – einmal, nicht hundertmal.

        Ohne das wiederholt ein Mitarbeiter denselben Fehler bei jedem Lauf:
        er sieht die Revision, arbeitet sie ab und hat beim naechsten Mal
        wieder keine Ahnung davon. Gleichlautende Beanstandungen werden
        zusammengefasst und mitgezaehlt; was oft kam, steht oben.
        """
        beanstandung = str(beanstandung).strip()
        if len(beanstandung) < 8:
            return {"gemerkt": False}
        kern = self._kern(beanstandung)
        eintraege = self.lehren(name)
        for eintrag in eintraege:
            if eintrag.get("kern") == kern:
                eintrag["anzahl"] = int(eintrag.get("anzahl", 1)) + 1
                eintrag["zuletzt"] = datetime.now(timezone.utc).isoformat()
                # Häufung = Systemfehler, nicht Einzelfall: ab 3 gleichen
                # Beanstandungen wird eine Prompt-Diagnose beauftragt (einmalig).
                if int(eintrag.get("anzahl", 1)) == 3:
                    try:
                        from services.auftraege import Auftragsbuch
                        buch = Auftragsbuch()
                        titel = f"Prompt-Diagnose {name}"
                        if not any(a["titel"] == titel and a["status"] in ("offen", "in_arbeit", "vorgelegt")
                                   for a in buch.liste("alle", limit=200)):
                            buch.erteilen("projektleitung", titel,
                                          f"Der Mitarbeiter {name} wurde 3x wegen desselben Punkts beanstandet: "
                                          f"'{eintrag.get('text', '')[:200]}'. Analysiere: Liegt es an seiner Rolle, "
                                          f"an der Auftragsformulierung oder am Prüf-Maßstab? Lies seine Rolle mit "
                                          f"list_sub_agents und mache einen konkreten Vorher/Nachher-Vorschlag. "
                                          f"Lege das Ergebnis mit submit_for_review (art dokument) vor.",
                                          quelle="jude")
                    except Exception:
                        pass
                break
        else:
            eintraege.append({"kern": kern, "text": beanstandung[:400], "quelle": quelle,
                              "anzahl": 1,
                              "zuletzt": datetime.now(timezone.utc).isoformat()})
        eintraege.sort(key=lambda e: (-int(e.get("anzahl", 1)), e.get("zuletzt", "")))
        eintraege = eintraege[:self.MAX_LEHREN]
        pfad = self._lehren_pfad(name)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(eintraege, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"gemerkt": True, "lehren_gesamt": len(eintraege)}

    def forget_lehren(self, name: str) -> dict:
        pfad = self._lehren_pfad(name)
        weg = len(self.lehren(name))
        pfad.unlink(missing_ok=True)
        return {"agent": name, "geloescht": weg}

    def _lob_pfad(self, name: str) -> Path:
        return DATA_DIR / "sub_agent_lob" / f"{self._key(name)}.json"

    def lob(self, name: str) -> list[dict]:
        """Was an diesem Mitarbeiter schon einmal gelobt wurde."""
        try:
            return json.loads(self._lob_pfad(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def lob_merken(self, name: str, anerkennung: str, quelle: str = "Jude") -> dict:
        """Das Gegenstück zu ``lehre_merken``: bisher hielt das Team nur fest,
        was schiefging, nie was gut war – ein Mitarbeiter, der etwas richtig
        gemacht hat, bekam das nie bestaetigt und wusste beim naechsten Lauf
        nicht, woran er anknuepfen soll."""
        anerkennung = str(anerkennung).strip()
        if len(anerkennung) < 8:
            return {"gemerkt": False}
        kern = self._kern(anerkennung)
        eintraege = self.lob(name)
        for eintrag in eintraege:
            if eintrag.get("kern") == kern:
                eintrag["anzahl"] = int(eintrag.get("anzahl", 1)) + 1
                eintrag["zuletzt"] = datetime.now(timezone.utc).isoformat()
                break
        else:
            eintraege.append({"kern": kern, "text": anerkennung[:400], "quelle": quelle,
                              "anzahl": 1,
                              "zuletzt": datetime.now(timezone.utc).isoformat()})
        eintraege.sort(key=lambda e: (-int(e.get("anzahl", 1)), e.get("zuletzt", "")))
        eintraege = eintraege[:self.MAX_LEHREN]
        pfad = self._lob_pfad(name)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(eintraege, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"gemerkt": True, "lob_gesamt": len(eintraege)}

    def forget_lob(self, name: str) -> dict:
        pfad = self._lob_pfad(name)
        weg = len(self.lob(name))
        pfad.unlink(missing_ok=True)
        return {"agent": name, "geloescht": weg}

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
            return {"zugestellt": True, "an": ziel.get("person") or ziel["name"],
                    "hinweis": "Steht in seinem naechsten Lauf im Systemprompt."}

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

    def _review_tool(self, spec: dict):
        """Fertiges vorlegen – ohne zu warten.

        Der Mitarbeiter meldet ein Erzeugnis zur Abnahme und arbeitet sofort
        weiter. Es geht ihn erst wieder etwas an, wenn eine Revision kommt.
        """
        from core.tool_registry import Tool
        from services.review import ARTEN, ReviewQueue
        queue = ReviewQueue()
        def _einreichen(art, titel, inhalt="", quelle="", ueberarbeitet="", auftrag_id=""):
            # Eine Ueberarbeitung ist keine neue Vorlage: dieselbe Zeile geht
            # eine Runde weiter zurueck zur Pruefung.
            if str(ueberarbeitet).strip():
                ergebnis = queue.erledigt(str(ueberarbeitet).strip(), inhalt or None, titel or None)
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
            return {"gemeldet": True, "hinweis": "Steht in den Meldungen für Tino."}

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
            sub.register(self._review_tool(spec))
            sub.register(self._bericht_tool(spec))
            sub.register(self._hinweis_tool(spec))
            if spec["name"] in self.TEXTER:
                sub.register(self._text_tool(spec))
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
                  f"ABNAHME-PFLICHT: Nichts gilt als fertig, was nicht vorgelegt wurde. "
                  f"Jedes Erzeugnis, das nach außen geht oder das Tino sehen soll (Post, "
                  f"E-Mail, Newsletter, Sequenz, Dokument, Recherche-Ergebnis, Grafik), "
                  f"legst du am Ende deines Laufs mit submit_for_review vor – auch wenn es "
                  f"schon in Notion steht; nenne dann die Notion-URL als quelle. Interne "
                  f"Zuarbeit (Notizen, Hinweise an Kollegen) braucht keine Vorlage. "
                  f"KEIN AUFTRAG HEUTE: Erst nachdem du deine Rolle wirklich geprüft hast "
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
            zeilen = "\n".join(
                f"- [{a['id']}] {a['titel']}"
                + (f" (fällig {a['faellig_am'][:10]})" if a.get("faellig_am") else "")
                + (f": {a['beschreibung'][:200]}" if a.get("beschreibung") else "")
                for a in offene_auftraege[:8])
            prompt += ("\n\nDEINE OFFENEN AUFTRÄGE – erledige sie in dieser Reihenfolge "
                       "und lege jedes Ergebnis mit submit_for_review vor; gib dabei "
                       "auftrag_id aus der eckigen Klammer an:\n" + zeilen)
        # Offene Revisionen haben Vorrang vor neuer Arbeit.
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
            prompt += ("\n\nZUERST ERLEDIGEN – Tino hat Überarbeitungen angefordert:\n"
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
                     force_model=modell)

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
        original = agent.tools.execute
        def mitschreiben(werkzeug, argumente):
            werkzeuge.append(werkzeug)
            ergebnis = original(werkzeug, argumente)
            if self._ist_fehlschlag(ergebnis):
                fehlschlaege.append(str(ergebnis).strip()[:300])
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

    #: Woran Jude ein Erzeugnis misst, bevor er es Tino vorlegt.
    #: Deterministisch prüfbare Verbotswörter (Werbedeutsch + Englisch-Ausreißer).
    #: Wortgrenzen, damit "Prozesse" im Claim nicht faelschlich anschlaegt.
    WORTFILTER = re.compile(
        r"\b(l(ö|oe)sung(en)?|effizient(er|este[rns]?)?|optimier\w*|transformier\w*|"
        r"innovativ\w*|benutzerfreundlich\w*|tools?|features?|workflows?|insights?|"
        r"reports?|scores?|templates?|setups?|game.?changer|boost\w*)\b",
        re.IGNORECASE)

    CHEF_MASSSTAB = (
        "0. Auftritt: hochwertig und ruhig, dunkelgruen und Gold auf mattem Schwarz. "
        "Marktgeschrei, Ausrufezeichen-Ketten, Emoji-Teppiche und Rabattsprache sind "
        "Ausschluss (1-2 dezente Emojis sind erlaubt, auf Xing keine; 3-6 Hashtags aus dem Strategie-Pool sind erlaubt) – wir wirken wie eine teure Manufaktur, nicht wie eine Werbeagentur.\n"
        "1. Marke: Nurovelle, 'Building intelligent System'. 'Autonova' und 'Politara' "
        "duerfen nirgends vorkommen. Von KI zu sprechen ist richtig, aber nie als "
        "Schlagwort ohne einen Ablauf, den der Leser kennt.\n"
        "2. Sprache: durchgehend Deutsch, kein englisches Wort, keine Platzhalter "
        "wie {{name}}, kein abgeschnittener Satz.\n"
        "3. Kein Werbedeutsch: 'Loesung', 'benutzerfreundlich', 'innovativ', "
        "'optimieren', 'auf Ihre Beduerfnisse zugeschnitten' sind Ausschluss.\n"
        "4. Keine erfundenen Zahlen, Kundenstimmen oder Referenzen. AUSNAHME – belegt und erlaubt sind die drei Fallstudien-Werte: API-Latenz -42 %, MTTR -32 %, Kosten -28 %. Platzhalter jeder Art "
        "sind Ausschluss – auch ein Hinweis wie 'vor Veroeffentlichung ersetzen'. Wer eine "
        "Zahl nicht belegen kann, liefert eine Fassung, die ohne sie auskommt.\n"
        "5. Konkreter Nutzen statt Schlagwort – der Empfaenger muss erkennen, "
        "was sich in seinem Alltag aendert.\n"
        "6. Kein Preis in einer Erstansprache.\n"
        "7. Der Text ist vollstaendig und koennte so rausgehen."
    )

    def _chefpruefung(self, agent_name: str) -> list[dict]:
        """Jude sieht als Vorgesetzter alles an, bevor es Tino erreicht.

        Laeuft nach dem Lauf des Mitarbeiters, nicht waehrenddessen – er ist
        dann laengst fertig und wartet auf nichts. Was Jude durchlaesst, geht
        auf ``offen`` und erscheint in Tinos Abnahme; was er beanstandet, geht
        als Revision an den Verfasser zurueck und taucht bei Tino nie auf.

        Die Pruefung laeuft ohne Werkzeuge – deshalb darf hier die schnelle
        70B-Stufe ran, die an Werkzeugketten scheitert.
        """
        from services.review import ReviewQueue

        queue = ReviewQueue()
        offene = [v for v in queue.zur_pruefung(limit=20) if v["agent"] == agent_name]
        entschieden = []
        for vorlage in offene:
            try:
                voll = queue.zeigen(vorlage["id"])
                # Deterministischer Wortfilter VOR dem Sprachmodell: Der Richter
                # vergab 92 Punkte an Seiten mit "effizienter", "Loesung" und
                # "Tools" (16.08.). Woerter zaehlt man, man erraet sie nicht.
                lesbar = voll.get("inhalt") or ""
                if "<style" in lesbar.lower() or "<html" in lesbar.lower():
                    # Nur was der Besucher liest zaehlt – 'grid-template-columns'
                    # im CSS ist kein Werbedeutsch.
                    lesbar = re.sub(r"<(style|script)\b.*?</\1\s*>", " ", lesbar,
                                    flags=re.S | re.I)
                    lesbar = re.sub(r"<[^>]+>", " ", lesbar)
                funde = {t[0].lower() if isinstance(t, tuple) else t.lower()
                         for t in self.WORTFILTER.findall(lesbar)}
                funde = {f for f in funde if f}
                if funde and int(voll.get("runde") or 1) < 3:
                    grund = ("Wortfilter: verbotene/englische Woerter im Text: "
                             + ", ".join(sorted(funde)[:10])
                             + ". Ersetze sie durch deutsche, konkrete Formulierungen.")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    self.lehre_merken(agent_name, grund)
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("revision",
                                                     f"Revision (Wortfilter): {voll['titel'][:70]}",
                                                     grund[:200])
                    except Exception:
                        pass
                    entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                        "grund": "wortfilter"})
                    continue
                # Revisionsbremse: Nach 2 Runden entscheidet Tino, nicht die
                # Schleife. Der Prüfer erfand sonst in jeder Runde neue Einwände.
                if int(voll.get("runde") or 1) >= 3:
                    rest = (voll.get("anmerkung") or "").strip()
                    queue.freigeben(vorlage["id"],
                                    "Jude: 2 Revisionen erreicht – Entscheidung bei Tino."
                                    + (f" Restpunkte: {rest[:200]}" if rest else ""))
                    entschieden.append({"id": vorlage["id"], "urteil": "vorbehalt"})
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("abnahme",
                                                     f"Zur Abnahme (mit Vorbehalt): {voll['titel'][:80]}",
                                                     f"Von {voll.get('person') or voll['agent']} – von Jude mit Vorbehalt freigegeben.")
                    except Exception:
                        pass
                    continue
                auftrag_kontext = ""
                try:
                    from services.auftraege import Auftragsbuch
                    auftrag = next((a for a in Auftragsbuch().liste("alle", limit=200)
                                    if a.get("review_id") == vorlage["id"]), None)
                    if auftrag:
                        auftrag_kontext = (f"\nAUFTRAG (daran misst du das Ergebnis): "
                                           f"{auftrag['titel']} – {auftrag['beschreibung'][:400]}\n")
                except Exception:
                    pass
                verlauf = (voll.get("verlauf") or "").strip()
                frage = (
                    "Du bist Jude, Geschaeftsfuehrer von Nurovelle. Ein Mitarbeiter legt dir "
                    "etwas Fertiges vor. Pruefe es, bevor es Tino erreicht.\n\n"
                    f"Massstab:\n{self.CHEF_MASSSTAB}\n\n{BRAND_BRIEF}\n\n"
                    "FAKTEN (keine Platzhalter, nicht beanstanden): Die kostenlose "
                    "KI-Potenzialanalyse auf nurovelle.de/analyse.html ist das echte "
                    "Kernangebot und der gewollte Handlungsaufruf. Massgeblich sind die Markenpräambel und das Qualitaets-Playbook (austausch/an-team/qualitaets-playbook.md).\n"
                    f"{auftrag_kontext}"
                    + (f"\nBISHERIGE BEANSTANDUNGEN (behobene Punkte NICHT erneut aufmachen):\n{verlauf[:800]}\n" if verlauf else "")
                    + f"\nVon: {voll.get('person') or voll['agent']}\n"
                    f"Art: {voll['art']}\nTitel: {voll['titel']}\n"
                    f"Inhalt:\n{(voll.get('inhalt') or '')[:4000]}\n\n"
                    "Antworte in GENAU ZWEI Zeilen. Zeile 1: SCORE A=<0-100> C=<0-100> "
                    "(A=Aufmerksamkeit: konkreter Alltags-Aufhaenger, Zielgruppe Mittelstand, "
                    "Kanal-Eignung; C=Conversion: genau EIN klarer naechster Schritt, Nutzen "
                    "im Alltag erkennbar, Vertrauen). Zeile 2: FREIGABE oder REVISION, danach "
                    "Doppelpunkt und kurze Begruendung. Beanstande NUR Verstoesse gegen "
                    "Massstab oder Auftrag, die du woertlich zitieren kannst."
                )
                # Nicht in die allgemeine Kette fallen lassen: die beginnt lokal,
                # und qwen kostete hier gemessen 300 s Timeout, bevor ueberhaupt
                # etwas passierte. Also der Reihe nach das erste Modell, dessen
                # Provider gerade wirklich antwortet.
                # Zweite Wahl bewusst bei einem ANDEREN Anbieter: faellt Ollama
                # Cloud als Provider aus, ist damit auch jedes Geschwistermodell
                # gesperrt. Hermes-70B kann Analyse und kostet Bruchteile eines
                # Cents. (Bis 03.09.2026 stand hier Haiku.)
                pruefmodell = self._brauchbares_modell(
                    self.TEXT_MODELL, "cloud_openrouter_hermes")
                antwort = self.router.call_with_fallback(
                    [{"role": "user", "content": frage}], force_model=pruefmodell)
                roh = str(antwort.get("content", "")).strip()
                import re as _re
                m = _re.search(r"SCORE\s+A\s*=\s*(\d{1,3})\s+C\s*=\s*(\d{1,3})", roh)
                gesamt = None
                if m:
                    gesamt = (min(int(m.group(1)), 100) + min(int(m.group(2)), 100)) // 2
                    try:
                        queue.score_setzen(vorlage["id"], gesamt)
                    except Exception:
                        pass
                urteilszeile = next((z for z in roh.splitlines()
                                     if z.strip().upper().startswith(("FREIGABE", "REVISION"))), "")
                begruendung = urteilszeile.split(":", 1)[-1].strip()[:400] or "ohne Begruendung"
                content_arten = {"post", "email", "newsletter", "sequenz"}
                if not urteilszeile:
                    # Fail-open geschlossen: Formatverletzung wird sichtbar gemacht,
                    # nicht stillschweigend als Freigabe gewertet.
                    queue.freigeben(vorlage["id"],
                                    "Jude: Pruefformat verletzt – ungeprueft durchgereicht. "
                                    "Bitte selbst pruefen.")
                    entschieden.append({"id": vorlage["id"], "urteil": "formatfehler"})
                    continue
                zu_niedrig = (voll["art"] in content_arten and gesamt is not None and gesamt < 70)
                if urteilszeile.strip().upper().startswith("REVISION") or zu_niedrig:
                    grund = begruendung if not zu_niedrig else (
                        f"Score {gesamt}/100 unter der 70er-Schwelle. {begruendung}")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    self.lehre_merken(agent_name, grund)
                    entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                        "score": gesamt})
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("revision",
                                                     f"Revision an {voll.get('person') or voll['agent']}: {voll['titel'][:80]}",
                                                     f"Jude hat Überarbeitungen angefordert: {grund[:200]}")
                    except Exception:
                        pass
                else:
                    queue.freigeben(vorlage["id"], f"Jude: {begruendung}"
                                    + (f" (Score {gesamt}/100)" if gesamt is not None else ""))
                    entschieden.append({"id": vorlage["id"], "urteil": "freigabe",
                                        "score": gesamt})
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("abnahme",
                                                     f"Zur Abnahme: {voll['titel'][:80]}",
                                                     f"Von {voll.get('person') or voll['agent']} – von Jude freigegeben.")
                    except Exception:
                        pass
            except Exception as exc:
                # Im Zweifel nach oben durchreichen: lieber legt Tino etwas
                # Mittelmaessiges beiseite, als dass Arbeit unsichtbar liegen bleibt.
                logger.warning("Chefpruefung fehlgeschlagen (%s): %s", vorlage["id"], exc)
                with suppress(Exception):
                    queue.freigeben(vorlage["id"], "Ungeprueft – Judes Pruefung fiel aus.")
                entschieden.append({"id": vorlage["id"], "urteil": "ungeprueft"})
        return entschieden

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
