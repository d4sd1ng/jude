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
    #: Prueft seit 04.09.2026 aktiv, was die deterministischen Gates
    #: bestanden hat - loest die automatische Judes-Pruefung ab.
    PROJEKTLEITUNG = "projektleitung"
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
        "scraper": ("write_copy",),
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

    #: Was ein Erzeugnis je Art mindestens an Text haben muss, damit es eine
    #: Abnahme wert ist. Gemessen 04.09.2026: 86 der 295 Vorlagen lagen unter
    #: 100 Zeichen, die kuerzeste E-Mail enthielt als Inhalt exakt ihren
    #: eigenen Titel ("Kostenlose KI-Potenzialanalyse", 30 Zeichen). Es gab
    #: dafuer bis dahin keine untere Schranke – ``inhalt`` hatte schlicht "" als
    #: Vorgabewert. Bei 'grafik' ist der Text nur das Konzept, das Erzeugnis ist
    #: die Bilddatei; deshalb dort die niedrigste Schranke.
    MINDESTLAENGE = {"email": 300, "newsletter": 500, "sequenz": 300,
                     "dokument": 300, "recherche": 250, "post": 150,
                     "grafik": 80, "sonstiges": 80}

    #: Ordner, unter denen ein projektrelativer Pfad wirklich liegen kann. Nur
    #: fuer diese wird die Existenz erzwungen – so bleibt eine Quellenangabe wie
    #: 'nurovelle.de/analyse.html' oder eine http-Adresse unangetastet.
    PROJEKT_ORDNER = ("austausch/", "data/", "project_files/", "images/",
                      "AGENT/", "backups/", "models/")

    @classmethod
    def _fehlende_datei(cls, pfad: str) -> str | None:
        """Gibt den Pfad zurueck, wenn er eine Datei meint, die es nicht gibt."""
        from core.paths import JUDE_DIR
        pfad = str(pfad).strip().strip('"').strip("'")
        if not pfad or "://" in pfad or pfad.lower().startswith("www."):
            return None
        kandidat = Path(pfad)
        if kandidat.is_absolute():
            return None if kandidat.is_file() else pfad
        if not pfad.startswith(cls.PROJEKT_ORDNER):
            return None
        return None if (JUDE_DIR / pfad).is_file() else pfad

    @classmethod
    def _bilddateien(cls, quelle: str) -> list[str]:
        """Die in ``quelle`` genannten Bilder, die es wirklich gibt."""
        from core.paths import JUDE_DIR
        treffer = []
        for pfad in str(quelle).split(","):
            pfad = pfad.strip().strip('"').strip("'")
            if not re.search(r"\.(png|jpe?g|webp|gif)(\?|$)", pfad, re.I):
                continue
            voll = Path(pfad) if Path(pfad).is_absolute() else JUDE_DIR / pfad
            if voll.is_file():
                treffer.append(pfad)
        return treffer

    @classmethod
    def _laenge_pruefen(cls, art: str, inhalt: str, neu: bool) -> None:
        """Zu kurz ist kein Erzeugnis. Bei einer Ueberarbeitung ohne neuen Text
        bleibt der alte stehen (COALESCE in ``ReviewQueue.erledigt``) – dann
        gibt es hier nichts zu pruefen."""
        text = str(inhalt or "").strip()
        if not neu and not text:
            return
        mindest = cls.MINDESTLAENGE.get(str(art).strip().lower(), 80)
        if len(text) < mindest:
            raise ValueError(
                f"Zu kurz zum Vorlegen: {len(text)} Zeichen, '{art}' braucht mindestens "
                f"{mindest}. Der Inhalt gehoert vollstaendig in 'inhalt' – nicht nur der "
                f"Titel, keine Ankuendigung, kein Verweis auf eine Datei. Erst schreiben, "
                f"dann vorlegen.")

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
                ergebnis = queue.erledigt(ziel, inhalt or None, titel or None)
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
                     force_model=modell, agent_name=spec["name"])

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
        def mitschreiben(werkzeug, argumente):
            werkzeuge.append(werkzeug)
            ergebnis = original(werkzeug, argumente)
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

    #: Woran Jude ein Erzeugnis misst, bevor er es Tino vorlegt.
    #: Deterministisch prüfbare Verbotswörter (Werbedeutsch + Englisch-Ausreißer).
    #: Wortgrenzen, damit "Prozesse" im Claim nicht faelschlich anschlaegt.
    WORTFILTER = re.compile(
        r"\b(l(ö|oe)sung(en)?|effizient(er|este[rns]?)?|optimier\w*|transformier\w*|"
        r"innovativ\w*|benutzerfreundlich\w*|tools?|features?|workflows?|insights?|"
        r"reports?|scores?|templates?|setups?|game.?changer|boost\w*)\b",
        re.IGNORECASE)

    # CHEF_MASSSTAB Punkt 1 verbietet die alten Markennamen ausdruecklich – bislang
    # gab es dafuer, anders als fuer Werbedeutsch, ueberhaupt keine deterministische
    # Rueckfallebene, nur das Urteil des Sprachmodells. Der falsche Firmenname in
    # einem Erzeugnis waere ein schwererer Fehler als jedes Werbewort.
    MARKENFILTER = re.compile(r"\b(autonova|politara)\b", re.IGNORECASE)

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
                # Markennamen-Filter zuerst, vor allem anderen: CHEF_MASSSTAB Punkt 1
                # verbietet 'Autonova'/'Politara' ausdruecklich, aber bislang gab es
                # dafuer keine deterministische Ebene, nur das Sprachmodell-Urteil –
                # der schwerste denkbare Fehler haengt am selben unzuverlaessigen
                # Mechanismus wie ein Werbewort.
                # Kein Runden-Deckel: ein alter Markenname ist keine Ermessensfrage,
                # die nach zwei Runden an Tino durchgereicht werden darf (03.09.2026 -
                # eine bildlose 'grafik' entkam der Prüfung genau ueber diesen Deckel).
                marken_funde = {m.group(0).lower() for m in self.MARKENFILTER.finditer(lesbar)}
                if marken_funde:
                    grund = ("Alte(r) Markenname(n) im Text gefunden: " + ", ".join(sorted(marken_funde))
                             + ". Es gibt nur Nurovelle.")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    self.lehre_merken(agent_name,
                                      "Alte Markennamen (Autonova, Politara) duerfen nirgends "
                                      "vorkommen – es gibt nur Nurovelle.")
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("revision",
                                                     f"Revision (Markenname): {voll['titel'][:70]}",
                                                     grund[:200])
                    except Exception:
                        pass
                    entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                        "grund": "markenname"})
                    continue
                # Deterministisch, wie der Wortfilter: eckige Klammern oder doppelte
                # geschweifte Klammern mit Text drin sind so gut wie nie Absicht,
                # sondern ein nicht ausgefuellter Platzhalter, der als fertig
                # vorgelegt wurde (Renate legte "[Titel des Beitrags]"/"[Der
                # aktualisierte Inhalt...]" als fertige Revision vor, 03.09.2026 -
                # ihre eigene Rolle verbietet das ausdruecklich, hielt sie aber nicht
                # auf; CHEF_MASSSTAB Punkt 2 nennt "{{name}}" als Beispiel). Markdown-
                # Links "[Text](url)" sind ausgenommen.
                platzhalter = [m.group(0) for m in re.finditer(r"\[[^\[\]\n]{2,80}\]|\{\{[^{}\n]{1,60}\}\}", lesbar)
                              if not lesbar[m.end():m.end() + 1] == "("]
                if platzhalter:
                    grund = ("Unausgefüllte Platzhalter in eckigen Klammern: "
                             + "; ".join(platzhalter[:5])
                             + ". Mit echtem Inhalt ersetzen, keine Klammer-Platzhalter vorlegen.")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    self.lehre_merken(agent_name,
                                      "Erzeugnisse mit unausgefüllten Platzhaltern in eckigen "
                                      "Klammern (z. B. '[Titel des Beitrags]') gelten nicht als "
                                      "fertig – erst wirklich schreiben, dann vorlegen.")
                    try:
                        from services.notifications import NotificationService
                        NotificationService().create("revision",
                                                     f"Revision (Platzhalter): {voll['titel'][:70]}",
                                                     grund[:200])
                    except Exception:
                        pass
                    entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                        "grund": "platzhalter"})
                    continue
                funde = {t[0].lower() if isinstance(t, tuple) else t.lower()
                         for t in self.WORTFILTER.findall(lesbar)}
                funde = {f for f in funde if f}
                if funde:
                    grund = ("Wortfilter: verbotene/englische Woerter im Text: "
                             + ", ".join(sorted(funde)[:10])
                             + ". Ersetze sie durch deutsche, konkrete Formulierungen.")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    # Immer derselbe, verallgemeinerte Text statt der konkreten Trefferliste:
                    # sonst zaehlte "insights, tool" als andere Lehre als "insights, reports,
                    # tool" und dieselbe Angewohnheit fragmentierte sich in mehrere
                    # Eintraege mit je anzahl=1, statt sich zu einer starken Lehre zu summieren
                    # (gemessen: engineer, drei separate Eintraege fuer denselben Fehler).
                    self.lehre_merken(agent_name,
                                      "Wortfilter schlägt wiederholt an: keine englischen "
                                      "Marketing-Woerter/Anglizismen (z. B. insights, tool, "
                                      "report, optimierung, loesung) – immer deutsche, "
                                      "konkrete Formulierungen statt Werbedeutsch.")
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
                # Deterministisch VOR dem Sprachmodell, wie der Wortfilter: eine
                # "grafik"-Vorlage ohne echte Bilddatei ist eine Beschreibung,
                # kein Bild – die PREMIUM-MASSSTAB-Regel in BRAND_BRIEF allein
                # wurde ignoriert (Heike legte ein Text-Konzept als 'grafik' vor,
                # quelle='', 03.09.2026). Das reicht kein Sprachmodell-Urteil,
                # das laesst sich zaehlen. Gilt auch, wenn Heike unter einer
                # anderen Art (z. B. 'sonstiges') vorlegt: ihre eigene Rolle
                # schreibt ausschliesslich art='grafik' vor, ein anderes Label
                # ist selbst schon die Umgehung dieser Pruefung (gemessen
                # 04.09.2026 - dieselbe Bild-Beschreibung ohne Datei, diesmal
                # als 'sonstiges' statt 'grafik' vorgelegt).
                # 'post' von social zaehlt hier mit dazu: PREMIUM-MASSSTAB gilt
                # auch fuer Beitraege, nicht nur fuer 'grafik' (04.09.2026 -
                # social legte Post-/Reel-Text ohne jedes Bild vor, obwohl die
                # eigene Rolle jetzt generate_image dafuer vorschreibt).
                if (voll["art"] == "grafik" or voll["agent"] == "designer"
                        or (voll["art"] == "post" and voll["agent"] == "social")):
                    quelle_bild = str(voll.get("quelle") or "")
                    # Bis 04.09.2026 pruefte dieses Tor die Zeichenkette, nicht
                    # die Datei: ein korrekt geschriebenes 'Infografik_2.png'
                    # genuegte, obwohl die Datei nie existierte – 41 Vorlagen
                    # kamen so durch. Jetzt zaehlt nur eine Datei auf der Platte.
                    hat_bild = bool(self._bilddateien(quelle_bild))
                    if not hat_bild:
                        genannt = re.search(r"\.(png|jpe?g|webp|gif)(\?|$|,)", quelle_bild, re.I)
                        grund = ("Keine Bilddatei vorgelegt: 'grafik' braucht ein mit generate_image "
                                 "erzeugtes Bild samt Dateipfad in quelle, keine Text-Beschreibung des "
                                 "geplanten Motivs.")
                        if genannt:
                            grund = (f"Die angegebene Bilddatei existiert nicht: {quelle_bild[:120]}. "
                                     f"Einen Dateinamen hinzuschreiben erzeugt kein Bild – erst "
                                     f"generate_image aufrufen, dann den entstandenen Pfad vorlegen.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self.lehre_merken(agent_name,
                                          "Eine 'grafik'-Vorlage ohne echte Bilddatei wird nicht "
                                          "angenommen – erst generate_image aufrufen und den "
                                          "Dateipfad als quelle angeben, dann vorlegen.")
                        try:
                            from services.notifications import NotificationService
                            NotificationService().create("revision",
                                                         f"Revision (kein Bild): {voll['titel'][:70]}",
                                                         grund[:200])
                        except Exception:
                            pass
                        entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                            "grund": "kein_bild"})
                        continue
                # HTML-Vorlagen von engineer: die Tagline wird wiederholt aus dem
                # Gedaechtnis angenaehert statt wortgleich uebernommen (gemessen
                # 04.09.2026: "BUILDING INTELLIGENT SYSTEM" statt der echten
                # "Building Intelligent Systems" - Grossschreibung UND das
                # fehlende 's'). Eindeutiger String-Vergleich, kein Ermessen.
                if voll["agent"] == "engineer" and voll["art"] == "dokument":
                    inhalt_pruef = voll.get("inhalt") or ""
                    falsch = re.search(r"BUILDING INTELLIGENT SYSTEMS?\b", inhalt_pruef)
                    richtig = "Building Intelligent Systems" in inhalt_pruef
                    if falsch and not richtig:
                        grund = (f"Falsche Tagline-Schreibweise gefunden: '{falsch.group(0)}'. "
                                 "Wortgleich 'Building Intelligent Systems' verwenden (nicht "
                                 "grossgeschrieben, mit 's' am Ende) - aus homepage/index.html "
                                 "Zeile ~1542 kopieren, nicht aus dem Gedaechtnis schreiben.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self.lehre_merken(agent_name,
                                          "Die Tagline wird wortgleich aus der echten Quelldatei "
                                          "kopiert ('Building Intelligent Systems'), nicht "
                                          "angenaehert oder grossgeschrieben.")
                        try:
                            from services.notifications import NotificationService
                            NotificationService().create("revision",
                                                         f"Revision (Tagline): {voll['titel'][:70]}",
                                                         grund[:200])
                        except Exception:
                            pass
                        entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                            "grund": "tagline_falsch"})
                        continue
                # Deterministisch, wie der Wortfilter: eine 'email' unter einer
                # Mindestlaenge ist keine E-Mail, nur die CTA-Zeile ohne Anrede/Text
                # (Tom/sequencer legte 52x nur "Kostenlose KI-Potenzialanalyse", 30
                # Zeichen, als 'email' vor, 03./04.09.2026 - das laesst sich zaehlen,
                # kein Sprachmodell-Urteil noetig).
                if voll["art"] == "email":
                    email_text = (voll.get("inhalt") or "").strip()
                    if len(email_text) < 100:
                        grund = (f"E-Mail zu kurz ({len(email_text)} Zeichen) - das ist keine "
                                 "E-Mail, nur eine Zeile. Mit write_copy einen echten Text mit "
                                 "Anrede, Inhalt und Grussformel erzeugen lassen, dann vorlegen.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self.lehre_merken(agent_name,
                                          "Eine 'email'-Vorlage unter 100 Zeichen wird nicht "
                                          "angenommen - erst write_copy fuer einen echten Text "
                                          "beauftragen, dann vorlegen.")
                        try:
                            from services.notifications import NotificationService
                            NotificationService().create("revision",
                                                         f"Revision (zu kurz): {voll['titel'][:70]}",
                                                         grund[:200])
                        except Exception:
                            pass
                        entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                            "grund": "email_zu_kurz"})
                        continue
                    # Dupliktat: dieselbe E-Mail wortgleich fuer mehrere Kontakte
                    # vorgelegt statt pro Kontakt personalisiert (Tom legte denselben
                    # generischen Text 12x identisch vor, 04.09.2026).
                    with connection() as db:
                        doppel = db.execute(
                            "SELECT COUNT(*) FROM reviews WHERE agent=? AND art='email' "
                            "AND id!=? AND inhalt=? AND status IN ('offen','abgenommen','pruefung')",
                            (voll["agent"], vorlage["id"], voll.get("inhalt") or "")).fetchone()[0]
                    if doppel:
                        grund = (f"Wortgleiche E-Mail liegt bereits {doppel}x vor - jede E-Mail "
                                 "braucht eigenen, auf den jeweiligen Empfaenger zugeschnittenen "
                                 "Text, kein Wiederverwenden desselben Entwurfs.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self.lehre_merken(agent_name,
                                          "Dieselbe E-Mail wortgleich fuer mehrere Kontakte "
                                          "vorzulegen wird nicht angenommen - pro Kontakt einzeln "
                                          "bei write_copy Empfaenger/Branche/Taetigkeit angeben.")
                        try:
                            from services.notifications import NotificationService
                            NotificationService().create("revision",
                                                         f"Revision (Duplikat): {voll['titel'][:70]}",
                                                         grund[:200])
                        except Exception:
                            pass
                        entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                            "grund": "email_duplikat"})
                        continue
                # Projektleitungs eigene Vorlagen (z.B. Prompt-Diagnose-Dokumente)
                # koennen nicht auf sie selbst als Pruefinstanz warten - zirkulaer.
                # Sie ist von der aktiven Pruefung ausgenommen wie der Redakteur
                # vom Vorlegen: die deterministischen Gates oben gelten weiter
                # unveraendert fuer sie, aber danach direkt durch.
                if agent_name == self.PROJEKTLEITUNG:
                    queue.freigeben(vorlage["id"], "Automatisch freigegeben – "
                                    "Projektleitung ist selbst die Pruefinstanz.")
                    entschieden.append({"id": vorlage["id"], "urteil": "freigabe"})
                    continue
                # Kein automatisches Urteil mehr ab hier (04.09.2026 - Jude raus
                # aus der Pruefung, das uebernimmt Projektleitung aktiv ueber
                # pruefungsliste()/pruefung_entscheiden()). Was die deterministischen
                # Gates oben besteht, bleibt einfach auf 'pruefung' stehen - das
                # ist kein Fehlerfall, das ist der neue Normalzustand: es wartet.
                #
                # Die alte Revisionsbremse (Runde >= 3 -> automatischer Durchlass
                # "mit Vorbehalt") faellt als Automatismus weg, nicht als Konzept:
                # runde steht bereits in pruefungsliste()'s Ausgabe, Projektleitungs
                # Rolle bekommt die Anweisung, Runde-3+-Faelle bewusst zu entscheiden
                # statt neue Einwaende nachzuschieben - dieselbe Absicht (keine
                # endlose Rueckweisungsschleife), aber als Urteil einer echten
                # Pruefinstanz statt als blinder Bypass.
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
