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

import os
from pathlib import Path

from core.tool_registry import Tool, ToolRegistry

PROJECT_ROOT = Path(__file__).parents[2].resolve()

#: Zusätzliche Wurzeln, aus denen gelesen werden darf – niemals geschrieben.
#: Das Homepage-Repo ist die Quelle für Vorgaben, Texte, Bilder und Icons.
#: Über ``JUDE_READONLY_ROOTS`` (durch Doppelpunkt getrennt) erweiterbar.
_STANDARD_QUELLEN = ("/media/d4sd1ng/AI-Data/Projects/homepage_repo",)


def _wurzeln() -> list[Path]:
    zusatz = [p for p in os.getenv("JUDE_READONLY_ROOTS", "").split(":") if p.strip()]
    wurzeln = [PROJECT_ROOT]
    for pfad in (*_STANDARD_QUELLEN, *zusatz):
        try:
            aufgeloest = Path(pfad).expanduser().resolve()
        except OSError:
            continue
        if aufgeloest.is_dir():
            wurzeln.append(aufgeloest)
    return wurzeln


def _pruefen(path: str) -> Path:
    """Löst den Pfad gegen die erlaubten Wurzeln auf.

    Ein absoluter Pfad wird direkt geprüft, ein relativer gegen jede Wurzel
    versucht. Entscheidend ist der **aufgelöste** Pfad – sonst käme man mit
    ``../`` aus einer Wurzel heraus.
    """
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
    if ".." in pattern or "/" in pattern or "\\" in pattern:
        raise ValueError("Ungültiges Glob-Muster: '..' und Pfadtrenner sind nicht erlaubt.")
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
        description=("Zeigt den Inhalt eines Verzeichnisses im Jude-Projekt oder im "
                     "Nurovelle-Homepage-Repo (nur lesend). Damit findest du Vorgabedateien, "
                     "Bilder und Icons, bevor du sie verwendest."),
        func=list_project_files,
        param_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "pattern": {"type": "string"},
        }},
    ))
