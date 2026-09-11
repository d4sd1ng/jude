"""Deterministische Qualitaets-Gates, die ein Erzeugnis besteht, bevor es
ueberhaupt zu einer Ermessensfrage wird - aus ``services.team.SubAgentService``
ausgelagert (Teil 3, Stage C des Refactorings). Reine Verschiebung: jede
Pruefung, jede Reihenfolge, jeder Text ist unveraendert.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ContentGate:
    #: Prueft seit 04.09.2026 aktiv, was die deterministischen Gates
    #: bestanden hat - loest die automatische Judes-Pruefung ab.
    PROJEKTLEITUNG = "projektleitung"

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
            # .svg dazu (04.09.2026) - grafik_infografik/grafik_onepager legen
            # echte SVG-Dateien mit verlaesslich lesbarem Text ab (Wortmarke,
            # Kennzahlen), kein von einem Bildgenerator gerendertes Pixelbild.
            if not re.search(r"\.(png|jpe?g|webp|gif|svg)(\?|$)", pfad, re.I):
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
        "1. Marke: Nurovelle, 'Building intelligent systems'. 'Autonova' und 'Politara' "
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

    # Strukturierte JSON-Ausgabe der Pipeline-Rollen (Trend Scout/Research/
    # Scriptwriter, Pipeline-Umstellung Phase 1, 05.09.2026): "input" und
    # "output" sind jetzt benannte Vertraege statt Fliesstext-Uebergabe - ein
    # Vorschlag in Prosa ("Hier ist mein Ergebnis...") ist fuer die naechste
    # Stufe der Kette nicht maschinell auswertbar. PIPELINE_SCHEMAS beschreibt
    # je Rolle die Pflichtfelder; _pipeline_schema_fehler prueft ein vorgelegtes
    # 'inhalt' deterministisch dagegen, genau wie WORTFILTER/MARKENFILTER oben.
    #
    # HINWEIS zu redakteur: die Kette hier gilt fuer alle drei Rollen, aber der
    # Redakteur legt nichts mit submit_for_review vor (siehe AUSNAHME VON DER
    # ABNAHME-PFLICHT in _build_agent und den Aufrufer weiter unten:
    # "geprueft = ([] wenn spec['name'] == REDAKTEUR sonst _chefpruefung(...))")
    # - _chefpruefung laeuft fuer ihn also nie. Das redakteur-Schema ist damit
    # unter der heutigen Architektur unerreichbar; es steht trotzdem hier, weil
    # er inhaltlich derselbe Rollentyp ist und sein Ergebnis (an den Auftraggeber
    # zurueckgegeben, nicht selbst vorgelegt) demselben Format folgen soll.
    #: 0-100, ganzzahlig - fuer die *_score-Felder unten.
    _SCORE = "score"
    PIPELINE_SCHEMAS = {
        "scraper": {
            "kind": "array",
            "felder": {
                "topic": str, "viralitaet": _SCORE, "aktualitaet": _SCORE,
                "relevanz": _SCORE, "business": _SCORE, "source": str,
                "publication_date": str,
            },
        },
        "analyst": {
            "kind": "objekt",
            "felder": {
                "topic": str, "summary": str, "facts": list, "sources": list,
                "unknowns": list, "confidence": _SCORE,
            },
        },
        "redakteur": {
            "kind": "objekt",
            "felder": {
                "hook_a": str, "hook_b": str, "hook_c": str, "script": str,
                "cta": str,
            },
        },
    }

    #: Kurzbeschreibung je Rolle fuer die Rueckweisung - dieselbe Praezision
    #: wie die Tagline-Meldung: nicht nur "ungueltig", sondern was genau
    #: gebraucht wird.
    PIPELINE_SCHEMA_TEXT = {
        "scraper": ("ein JSON-Array, jedes Element ein Objekt mit genau: topic, "
                    "viralitaet (0-100), aktualitaet (0-100), relevanz (0-100), "
                    "business (0-100), source, publication_date"),
        "analyst": ("ein JSON-Objekt mit genau: topic, summary, facts (Liste), "
                    "sources (Liste), unknowns (Liste), confidence (0-100)"),
        "redakteur": ("ein JSON-Objekt mit genau: hook_a, hook_b, hook_c, script, cta"),
    }

    @classmethod
    def _pipeline_feld_fehler(cls, objekt, felder: dict) -> str | None:
        if not isinstance(objekt, dict):
            return "ist kein JSON-Objekt"
        for feld, art in felder.items():
            if feld not in objekt:
                return f"fehlendes Feld '{feld}'"
            wert = objekt[feld]
            if art == cls._SCORE:
                if not isinstance(wert, int) or isinstance(wert, bool) or not (0 <= wert <= 100):
                    return f"Feld '{feld}' muss eine ganze Zahl von 0 bis 100 sein, nicht {wert!r}"
            elif art is str:
                if not isinstance(wert, str) or not wert.strip():
                    return f"Feld '{feld}' muss nicht-leerer Text sein"
            elif art is list:
                if not isinstance(wert, list):
                    return f"Feld '{feld}' muss eine Liste sein"
        return None

    @classmethod
    def _pipeline_schema_fehler(cls, agent: str, inhalt: str) -> str | None:
        """``None`` wenn ``inhalt`` fuer ``agent`` gueltiges Pipeline-JSON ist,
        sonst ein knapper, konkreter Grund - nach demselben Prinzip wie der
        Tagline-Gate: genau benennen, was fehlt, nicht nur "ungueltig"."""
        schema = cls.PIPELINE_SCHEMAS.get(agent)
        if not schema:
            return None
        try:
            daten = json.loads(inhalt)
        except (TypeError, ValueError) as exc:
            return f"kein gueltiges JSON ({exc})"
        if schema["kind"] == "array":
            if not isinstance(daten, list) or not daten:
                return "erwartet ein nicht-leeres JSON-Array, kein Objekt/Text"
            for i, eintrag in enumerate(daten):
                fehler = cls._pipeline_feld_fehler(eintrag, schema["felder"])
                if fehler:
                    return f"Eintrag {i + 1}: {fehler}"
            return None
        return cls._pipeline_feld_fehler(daten, schema["felder"])

    def __init__(self, feedback_store):
        self._feedback = feedback_store

    def pruefen(self, agent_name: str) -> list[dict]:
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
        logger.info("pruefen(%s): %s Vorlage(n) in Pruefung - %s.", agent_name, len(offene),
                    ", ".join(f"{v['id']}/{v['art']}/Runde{v.get('runde', 1)}" for v in offene) or "keine")
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
                    self._feedback.lehre_merken(agent_name,
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
                # Strukturierte JSON-Ausgabe (Pipeline-Umstellung Phase 1,
                # 05.09.2026): Trend Scout/Research/Scriptwriter tauschen
                # untereinander benannte Datenformate aus, keine Prosa - siehe
                # PIPELINE_SCHEMAS oben. Deterministisch, wie MARKENFILTER davor:
                # ein Vorschlag in Fliesstext ("Hier ist meine Recherche...")
                # ist fuer die naechste Stufe nicht auswertbar und wird genauso
                # zurueckgewiesen wie ein Markenname. LAEUFT VOR dem Platzhalter-
                # Filter unten, nicht danach: ein echtes JSON-Array wie
                # '["Fakt 1", "Fakt 2"]' erfuellt selbst das Muster fuer einen
                # nicht ausgefuellten Klammer-Platzhalter (gemessen beim Bau
                # dieses Gates, 05.09.2026 - eine gueltige 'facts'-Liste im
                # Research Brief loeste faelschlich den Platzhalter-Filter aus).
                # Deshalb entscheidet dieses Gate zuerst, und schemakonformes
                # JSON ueberspringt den Platzhalter-Filter gleich mit (siehe dort).
                if voll["agent"] in self.PIPELINE_SCHEMAS:
                    schema_fehler = self._pipeline_schema_fehler(voll["agent"], lesbar)
                    if schema_fehler:
                        grund = (f"Kein gueltiges strukturiertes JSON: {schema_fehler}. "
                                 f"Erwartet wird {self.PIPELINE_SCHEMA_TEXT.get(voll['agent'], '')} "
                                 f"- kein Fliesstext, keine Vorrede, nur das JSON selbst.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self._feedback.lehre_merken(agent_name,
                                          "Das Ergebnis wird als strukturiertes JSON nach dem "
                                          "vorgegebenen Format vorgelegt, nicht als Fliesstext oder "
                                          "Vorschlag in Prosa - die naechste Stufe der Kette liest "
                                          "es maschinell weiter.")
                        try:
                            from services.notifications import NotificationService
                            NotificationService().create("revision",
                                                         f"Revision (JSON-Format): {voll['titel'][:70]}",
                                                         grund[:200])
                        except Exception:
                            pass
                        entschieden.append({"id": vorlage["id"], "urteil": "revision",
                                            "grund": "pipeline_json"})
                        continue
                # Deterministisch, wie der Wortfilter: eckige Klammern oder doppelte
                # geschweifte Klammern mit Text drin sind so gut wie nie Absicht,
                # sondern ein nicht ausgefuellter Platzhalter, der als fertig
                # vorgelegt wurde (Renate legte "[Titel des Beitrags]"/"[Der
                # aktualisierte Inhalt...]" als fertige Revision vor, 03.09.2026 -
                # ihre eigene Rolle verbietet das ausdruecklich, hielt sie aber nicht
                # auf; CHEF_MASSSTAB Punkt 2 nennt "{{name}}" als Beispiel). Markdown-
                # Links "[Text](url)" sind ausgenommen. Pipeline-JSON-Rollen sind
                # ausgenommen: ihr Inhalt ist per Definition ein JSON-Array/-Objekt
                # mit eckigen/geschweiften Klammern, das obige Gate hat es bereits
                # gegen sein eigenes Schema geprueft, bevor wir hier ankommen.
                platzhalter = ([] if voll["agent"] in self.PIPELINE_SCHEMAS else
                               [m.group(0) for m in re.finditer(r"\[[^\[\]\n]{2,80}\]|\{\{[^{}\n]{1,60}\}\}", lesbar)
                                if not lesbar[m.end():m.end() + 1] == "("])
                if platzhalter:
                    grund = ("Unausgefüllte Platzhalter in eckigen Klammern: "
                             + "; ".join(platzhalter[:5])
                             + ". Mit echtem Inhalt ersetzen, keine Klammer-Platzhalter vorlegen.")
                    queue.revision(vorlage["id"], f"Jude: {grund}")
                    self._feedback.lehre_merken(agent_name,
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
                # URLs koennen legitime Fremdwoerter im Pfad tragen (z. B.
                # ".../new-tools-and-pricing") - das ist eine zitierte Adresse,
                # keine Wortwahl des Verfassers, und laesst sich nicht "auf
                # Deutsch umformulieren". Ohne den Ausschluss blockierte eine
                # echte Quellenangabe den Wortfilter (scraper, Pipeline-Pilot,
                # 05.09.2026: 'tools' stammte ausschliesslich aus einer
                # zitierten Cloud-Wars-URL im 'source'-Feld; der Lauf danach in
                # eine 40x-read_project_file-Schleife, weil er nicht wusste,
                # wie er einer URL gehorchen sollte, bis der Schritt-Deckel griff).
                lesbar_ohne_urls = re.sub(r"https?://\S+", " ", lesbar)
                funde = {t[0].lower() if isinstance(t, tuple) else t.lower()
                         for t in self.WORTFILTER.findall(lesbar_ohne_urls)}
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
                    self._feedback.lehre_merken(agent_name,
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
                # Onepager (content, art='dokument') zaehlt seit 11.09.2026
                # ebenfalls dazu: contents eigene Rolle verlangt seit 07.09.2026
                # bereits, Heike per inform_colleague zu benachrichtigen, damit
                # sie grafik_onepager aufruft ("ohne diese Benachrichtigung
                # bleibt der Onepager ein reiner Text ohne Layout, das ist
                # NICHT fertig") - das stand aber nur als Prosa-Regel da, ohne
                # jede deterministische Durchsetzung. Ergebnis (gemessen
                # 11.09.2026, Tino: "to put just text on it is complete off
                # from a onepager"): reine Text-Gliederungen kamen unveraendert
                # bis zu Tino durch, weil niemand die fehlende Bilddatei
                # zaehlte. Titel-Heuristik statt fixem Art-Wert, weil 'dokument'
                # auch fuer Blog-Artikel/Whitepaper gilt, die AUSDRUECKLICH
                # KEIN Bild brauchen (content-Rolle, BILD-Abschnitt).
                if (voll["art"] == "grafik" or voll["agent"] == "designer"
                        or (voll["art"] == "post" and voll["agent"] == "social")
                        or (voll["art"] == "dokument" and voll["agent"] == "content"
                            and "onepager" in voll["titel"].lower())):
                    quelle_bild = str(voll.get("quelle") or "")
                    # Bis 04.09.2026 pruefte dieses Tor die Zeichenkette, nicht
                    # die Datei: ein korrekt geschriebenes 'Infografik_2.png'
                    # genuegte, obwohl die Datei nie existierte – 41 Vorlagen
                    # kamen so durch. Jetzt zaehlt nur eine Datei auf der Platte.
                    hat_bild = bool(self._bilddateien(quelle_bild))
                    if not hat_bild:
                        genannt = re.search(r"\.(png|jpe?g|webp|gif|svg)(\?|$|,)", quelle_bild, re.I)
                        ist_onepager = voll["art"] == "dokument" and voll["agent"] == "content"
                        if ist_onepager:
                            grund = ("Kein Layout vorgelegt: ein Onepager braucht die von Heike per "
                                     "grafik_onepager erzeugte Bilddatei in quelle, keine reine "
                                     "Text-Gliederung. Heike per inform_colleague mit Titel/Untertitel/"
                                     "Abschnitten/Abschluss benachrichtigen (siehe eigene Rolle, "
                                     "ONEPAGER-LAYOUT), dann mit deren Dateipfad erneut vorlegen.")
                        else:
                            grund = ("Keine Bilddatei vorgelegt: 'grafik' braucht ein mit generate_image "
                                     "erzeugtes Bild samt Dateipfad in quelle, keine Text-Beschreibung des "
                                     "geplanten Motivs.")
                            if genannt:
                                grund = (f"Die angegebene Bilddatei existiert nicht: {quelle_bild[:120]}. "
                                         f"Einen Dateinamen hinzuschreiben erzeugt kein Bild – erst "
                                         f"generate_image aufrufen, dann den entstandenen Pfad vorlegen.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self._feedback.lehre_merken(agent_name,
                                          "Ein Onepager ohne die von Heike erzeugte Layout-Datei wird "
                                          "nicht angenommen - erst per inform_colleague benachrichtigen "
                                          "und deren Dateipfad als quelle angeben, dann vorlegen."
                                          if ist_onepager else
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
                # Gedaechtnis angenaehert statt wortgleich uebernommen. Verifizierte
                # Quelle ist austausch/an-team/vorlagen/nurovelle/launchpost.pdf
                # (bei 300 dpi geprueft, 05.09.2026) - dort steht "Building
                # intelligent systems": Grossbuchstabe nur bei "Building", "intelligent"
                # und "systems" klein, mit 's' am Ende. Die Live-Seite (homepage/index.html)
                # ist NICHT die massgebliche Quelle (Tinos ausdrueckliche Festlegung) und
                # weicht ab ("Building Intelligent Systems", faelschlich grossgeschrieben)
                # - diese Pruefung akzeptierte bis 05.09.2026 genau diese falsche Schreibweise
                # und hätte die tatsaechlich korrekte zurueckgewiesen. Eindeutiger
                # String-Vergleich, kein Ermessen.
                if voll["agent"] == "engineer" and voll["art"] == "dokument":
                    inhalt_pruef = voll.get("inhalt") or ""
                    gefunden = re.search(r"building intelligent systems?\b", inhalt_pruef, re.I)
                    if gefunden and gefunden.group(0) != "Building intelligent systems":
                        grund = (f"Falsche Tagline-Schreibweise gefunden: '{gefunden.group(0)}'. "
                                 "Wortgleich 'Building intelligent systems' verwenden (kleines "
                                 "'i' bei intelligent, kleines 's' bei systems, mit 's' am Ende) "
                                 "- aus austausch/an-team/vorlagen/nurovelle/launchpost.pdf "
                                 "kopieren, nicht aus dem Gedaechtnis schreiben oder von der "
                                 "Live-Seite uebernehmen.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self._feedback.lehre_merken(agent_name,
                                          "Die Tagline wird wortgleich aus der echten Vorlage "
                                          "kopiert ('Building intelligent systems', aus "
                                          "launchpost.pdf), nicht angenaehert, grossgeschrieben "
                                          "oder von der Live-Seite uebernommen.")
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
                        self._feedback.lehre_merken(agent_name,
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
                    doppel = queue.anzahl_wortgleich(voll["agent"], "email", vorlage["id"], voll.get("inhalt") or "")
                    if doppel:
                        grund = (f"Wortgleiche E-Mail liegt bereits {doppel}x vor - jede E-Mail "
                                 "braucht eigenen, auf den jeweiligen Empfaenger zugeschnittenen "
                                 "Text, kein Wiederverwenden desselben Entwurfs.")
                        queue.revision(vorlage["id"], f"Jude: {grund}")
                        self._feedback.lehre_merken(agent_name,
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
                #
                # Bis 10.09.2026 war dieser Fall (alle deterministischen Gates
                # bestanden) komplett stumm - kein Log, kein Eintrag in
                # entschieden. Ein Social-Post ohne Bild kam so ungeklaert durch
                # (b6a86c63df28, 10.09.2026) und liess sich im Nachhinein nicht
                # rekonstruieren: kein Log zeigte, welche Gates ueberhaupt liefen.
                # Ab jetzt wird jeder Durchlass explizit protokolliert, damit ein
                # zweiter Fall sofort erklaerbar ist statt erneut Stunden Suche
                # zu kosten.
                logger.info("pruefen(%s): %s alle deterministischen Gates bestanden, "
                            "bleibt auf 'pruefung' (wartet auf Projektleitung).",
                            agent_name, vorlage["id"])
                entschieden.append({"id": vorlage["id"], "urteil": "gates_bestanden"})
            except Exception as exc:
                # Fail-closed, nicht fail-open: ein Fehler IM Gate ist kein Grund,
                # das Gate zu umgehen - genau das tat dieser Block bis 11.09.2026
                # (automatisches freigeben() bei jeder Exception, egal welcher).
                # Ein Social-Post ohne Bild (b6a86c63df28, 10.09.2026) blieb dadurch
                # unerklaerbar: kein Log zeigte, ob ein Gate-Fehler die Ursache war,
                # weil der Fehlerfall selbst zur automatischen Freigabe fuehrte statt
                # zu einem sichtbaren Halt. Bleibt jetzt auf 'pruefung' stehen wie
                # jeder andere bestandene/unentschiedene Fall auch - Projektleitung
                # sieht es ueber pruefungsliste(), Tino verliert nichts unbemerkt.
                logger.warning("Chefpruefung fehlgeschlagen (%s): %s - bleibt auf 'pruefung'.",
                                vorlage["id"], exc)
                try:
                    from services.notifications import NotificationService
                    NotificationService().create("fehler",
                                                 f"Pruefung fehlgeschlagen: {vorlage.get('titel', vorlage['id'])[:70]}",
                                                 str(exc)[:200])
                except Exception:
                    pass
                entschieden.append({"id": vorlage["id"], "urteil": "gate_fehler"})
        if entschieden:
            logger.info("pruefen(%s) fertig: %s.", agent_name,
                        "; ".join(f"{e['id']}={e['urteil']}" + (f"({e['grund']})" if e.get('grund') else "")
                                  for e in entschieden))
        return entschieden
