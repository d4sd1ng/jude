"""Lesezugriff auf Projektdateien – Jude selbst und ausdrücklich freigegebene Quellen.

Bis hierher durfte nur innerhalb des Jude-Projekts gelesen werden. Damit kam das
Team an die eigentlichen Vorgabedateien von Nurovelle nicht heran und arbeitete
stattdessen mit dem, was ihm jemand in den Prompt geschrieben hatte – mit dem
Ergebnis, dass es eine Positionierung vertrat, die in keinem freigegebenen
Dokument steht.

Deshalb gibt es jetzt zusätzliche Wurzeln, aus denen gelesen werden darf. Sie
sind bewusst **nur lesend**: es gibt in diesem Modul keinen Schreibpfad, und die
Wurzeln werden vor jedem Zugriff gegen den aufgelösten Zielpfad geprüft, damit
``../`` nicht hinausführt.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.tool_registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parents[2].resolve()

#: Dateinamen, die in BEIDEN Wurzeln existieren, aber völlig anderen Inhalt
#: haben: Jude/project_files/ beschreibt, wie an Jude selbst gearbeitet wird,
#: das Homepage-Repo beschreibt Nurovelle. Verwechslungsgefahr, siehe
#: _homepage_repo_wurzel() unten - deshalb eigens benannt statt implizit.
_MEHRDEUTIGE_DATEINAMEN = {"project_overview.md", "task_contract.md",
                           "decision_log.md", "todo.md"}


def _homepage_repo_wurzel() -> Path | None:
    """Findet das Nurovelle-Homepage-Repo, ohne einen konkreten Einhängepunkt
    (Laufwerksbuchstabe/Mount-Pfad) fest anzunehmen.

    Frueher stand hier ein einziger hart kodierter absoluter Pfad
    (/media/d4sd1ng/AI-Data/Projects/homepage_repo). Der ist an einen
    bestimmten Einhaengepunkt gebunden - haengt das Laufwerk (dieselbe Platte,
    derselbe Inhalt) unter einem anderen Pfad ein, etwa nach einem Neustart,
    einem Treiberwechsel oder auf einer anderen Maschine, existiert der
    hartkodierte Pfad schlicht nicht mehr. is_dir() liefert dann False, die
    Wurzel wird in _wurzeln() lautlos aus der Liste entfernt - kein Fehler,
    keine Warnung im Log. Betroffene Agenten bekamen daraufhin entweder einen
    "Pfad nicht lesbar"-Fehler (styleguide.md/assets.md/architecture.md/
    changelog.md, die es nur im Homepage-Repo gibt) oder - schlimmer, siehe
    _MEHRDEUTIGE_DATEINAMEN oben - lautlos Jude's EIGENE, inhaltlich andere
    Datei desselben Namens aus PROJECT_ROOT, ohne jeden Hinweis, dass das
    passiert ist (gemessen 06.09.2026 nach einem Wechsel von
    /media/d4sd1ng/AI-Data auf /mnt/ai-data - derselbe Fehlermodus, den der
    Code-Kommentar zu _wurzeln() schon einmal fuer eine andere Ursache
    beschreibt: 02.09.2026, falsche Reihenfolge statt fehlender Wurzel).

    Reihenfolge, erster Treffer gewinnt:
    1. JUDE_HOMEPAGE_REPO (explizite Angabe, z.B. in .env) - eigene Variable,
       nicht die allgemeine JUDE_READONLY_ROOTS, weil genau DIESE Wurzel wegen
       der mehrdeutigen Dateinamen oben besonders kritisch ist.
    2. Der historische hartkodierte Pfad - falls jemand ihn absichtlich exakt
       so eingerichtet hat, bleibt das gueltig.
    3. Geschwisterverzeichnis von Jude selbst: beide Projekte liegen immer
       nebeneinander unter .../Projects/, unabhaengig davon, wie der Teil
       davor gerade heisst - das uebersteht einen Einhaengepunkt-Wechsel ohne
       jede Konfiguration, weil PROJECT_ROOT ohnehin schon relativ zu
       __file__ aufgeloest wird (Zeile oben), nie hart kodiert ist.
    """
    kandidaten = [
        os.getenv("JUDE_HOMEPAGE_REPO", "").strip(),
        "/media/d4sd1ng/AI-Data/Projects/homepage_repo",
        str(PROJECT_ROOT.parent / "homepage_repo"),
    ]
    for pfad in kandidaten:
        if not pfad:
            continue
        try:
            aufgeloest = Path(pfad).expanduser().resolve()
        except OSError:
            continue
        if aufgeloest.is_dir():
            return aufgeloest
    logger.error(
        "Nurovelle-Homepage-Repo nicht gefunden (geprueft: %s). "
        "styleguide.md/assets.md/architecture.md/changelog.md sind damit fuer "
        "jeden Sub-Agenten unlesbar, und %s wuerden lautlos aus dem "
        "Jude-eigenen project_files/ statt aus dem Homepage-Repo bedient. "
        "JUDE_HOMEPAGE_REPO in .env setzen, um den Pfad explizit zu nennen.",
        [k for k in kandidaten if k], sorted(_MEHRDEUTIGE_DATEINAMEN))
    return None


def _wurzeln() -> list[Path]:
    """Homepage-Repo VOR dem Jude-Projekt: project_files/task_contract.md,
    decision_log.md, todo.md und project_overview.md existieren in BEIDEN
    Wurzeln mit demselben Namen, aber völlig anderem Inhalt – Jude/project_files/
    beschreibt, wie an Jude selbst gearbeitet wird, nicht Nurovelle. Stand die
    Jude-Wurzel zuerst, bekam jeder Sub-Agent, dessen Rolle "lies project_files/"
    vorschreibt, die falschen (Jude-eigenen) Dateien statt der Nurovelle-Vorgaben –
    gemessen 02.09.2026, u.a. Ursache für Sub-Agenten, die sich in read_project_file
    verhaspelten, bis das Werkzeug-Budget aufgebraucht war."""
    zusatz = [p for p in os.getenv("JUDE_READONLY_ROOTS", "").split(":") if p.strip()]
    wurzeln = []
    homepage = _homepage_repo_wurzel()
    if homepage is not None:
        wurzeln.append(homepage)
    for pfad in zusatz:
        try:
            aufgeloest = Path(pfad).expanduser().resolve()
        except OSError:
            continue
        if aufgeloest.is_dir():
            wurzeln.append(aufgeloest)
    wurzeln.append(PROJECT_ROOT)
    return wurzeln


def _pruefen(path: str) -> Path:
    """Löst den Pfad gegen die erlaubten Wurzeln auf.

    Ein absoluter Pfad wird direkt geprüft, ein relativer gegen jede Wurzel
    versucht. Entscheidend ist der **aufgelöste** Pfad – sonst käme man mit
    ``../`` aus einer Wurzel heraus.
    """
    # Harter Stopp statt stillem Fehlgriff: fuer die vier mehrdeutigen
    # Dateinamen gibt es KEINE sichere Ausweich-Wurzel - Jude's eigene Kopie
    # unter demselben Namen ist inhaltlich eine andere Datei, kein Ersatz.
    # Ohne diesen Stopp faellt path auf PROJECT_ROOT zurueck und liefert
    # lautlos Jude-eigene Inhalte unter dem Namen der Nurovelle-Vorgabedatei
    # (der genaue Fehler, der zu dieser Pruefung gefuehrt hat, 06.09.2026).
    if Path(path).name in _MEHRDEUTIGE_DATEINAMEN and _homepage_repo_wurzel() is None:
        raise ValueError(
            f"{path!r} ist mehrdeutig: existiert sowohl im Nurovelle-Homepage-Repo "
            "als auch, mit anderem Inhalt, in Jude's eigenem project_files/. Das "
            "Homepage-Repo ist gerade nicht auffindbar (siehe Log), deshalb wird "
            "hier NICHT stillschweigend Jude's eigene Datei ausgeliefert. "
            "JUDE_HOMEPAGE_REPO in .env setzen und erneut versuchen.")
    wurzeln = _wurzeln()
    kandidat = Path(path).expanduser()
    versuche = [kandidat] if kandidat.is_absolute() else [w / path for w in wurzeln]
    for versuch in versuche:
        ziel = versuch.resolve()
        if any(ziel == w or w in ziel.parents for w in wurzeln):
            if ziel.is_file():
                return ziel
    raise ValueError(
        "Pfad nicht lesbar. Erlaubt sind das Jude-Projekt und: "
        + ", ".join(str(w) for w in wurzeln[1:]))


def read_project_file(path: str, max_characters: int = 12000) -> str:
    ziel = _pruefen(path)
    return ziel.read_text(encoding="utf-8", errors="replace")[
        :max(1000, min(max_characters, 50000))]


def list_project_files(path: str = ".", pattern: str = "*") -> dict:
    """Verzeichnisinhalt einer erlaubten Wurzel – Namen, Größe, Änderungsdatum."""
    wurzeln = _wurzeln()
    kandidat = Path(path).expanduser()
    versuche = [kandidat] if kandidat.is_absolute() else [w / path for w in wurzeln]
    for versuch in versuche:
        ziel = versuch.resolve()
        if not ziel.is_dir():
            continue
        if not any(ziel == w or w in ziel.parents for w in wurzeln):
            continue
        eintraege = []
        for kind in sorted(ziel.glob(pattern))[:200]:
            eintraege.append({
                "name": kind.name,
                "art": "Ordner" if kind.is_dir() else "Datei",
                "bytes": kind.stat().st_size if kind.is_file() else None,
                "geaendert": __import__("datetime").datetime.fromtimestamp(
                    kind.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        return {"pfad": str(ziel), "anzahl": len(eintraege), "eintraege": eintraege}
    raise ValueError("Verzeichnis nicht lesbar oder ausserhalb der erlaubten Wurzeln.")


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="read_project_file",
        description=("Liest eine Textdatei aus dem Jude-Projekt oder aus dem Nurovelle-"
                     "Homepage-Repo (nur lesend). Die verbindlichen Vorgaben liegen unter "
                     "'project_files/' – vor allem project_overview.md, task_contract.md, "
                     "styleguide.md, decision_log.md, todo.md und assets.md."),
        func=read_project_file,
        param_schema={"type": "object", "properties": {
            "path": {"type": "string"},
            "max_characters": {"type": "integer", "minimum": 1000, "maximum": 50000},
        }, "required": ["path"]},
    ))
    registry.register(Tool(
        name="list_project_files",
        description=("Zeigt den Inhalt EINES konkreten Verzeichnisses im Jude-Projekt oder im "
                     "Nurovelle-Homepage-Repo (nur lesend). 'path' ist Pflicht und muss ein "
                     "konkreter Unterordner sein, z.B. 'austausch/an-team/vorlagen/nurovelle' - "
                     "NIE leer oder '.' lassen: das trifft sonst auf die Wurzel des Homepage-Repos "
                     "(46+ Eintraege, u.a. node_modules), nicht auf das, was eigentlich gemeint war "
                     "(gemessen 11.09.2026 - zwei Laeufe haengten sich an genau diesem leeren Aufruf "
                     "ueber 3 Minuten auf, bevor sie ohne Ergebnis abbrachen)."),
        func=list_project_files,
        param_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "pattern": {"type": "string"},
        }, "required": ["path"]},
    ))
