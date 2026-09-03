"""Datei-Austausch zwischen Tino und dem Team.

Drei Ordner unter ``austausch/`` im Projektstamm:

  an-team/      Tino legt hier Dokumente fürs Team ab; die Mitarbeiter lesen
                sie über read_project_file (der Ordner ist Lese-Wurzel).
  vom-team/     Mitarbeiter legen fertige Dateien (Posts, Dokumente) zur
                Freigabe ab.
  freigegeben/  Was Tino freigegeben hat – von dort verwendet er es weiter.

Bewusst nur Ordner und drei kleine Werkzeuge: kein eigener Sync, keine GUI.
Veröffentlicht oder versendet wird hier nichts.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from core.tool_registry import Tool, ToolRegistry

# Im Projektstamm (dort liegt für Tino alles Greifbare), nicht im Daten-
# verzeichnis: der Projektstamm ist ohnehin Lese-Wurzel der Mitarbeiter.
AUSTAUSCH_DIR = Path(__file__).parents[2].resolve() / "austausch"
ORDNER = ("an-team", "vom-team", "freigegeben")
_MAX_ZEICHEN = 2_000_000


def _verzeichnis(name: str) -> Path:
    if name not in ORDNER:
        raise ValueError("Unbekannter Austausch-Ordner: " + name)
    ziel = AUSTAUSCH_DIR / name
    ziel.mkdir(parents=True, exist_ok=True)
    return ziel


def _dateiname(name: str) -> str:
    sauber = re.sub(r"[^\w.\-() äöüÄÖÜß]", "_", str(name).strip()).lstrip(". ")
    if not sauber:
        raise ValueError("Ungültiger Dateiname.")
    return sauber


def liste() -> dict:
    """Alle Dateien im Austausch, je Ordner mit Größe und Änderungszeit."""
    ergebnis: dict[str, list[dict]] = {}
    for ordner in ORDNER:
        eintraege = []
        for pfad in sorted(_verzeichnis(ordner).iterdir()):
            if pfad.is_file():
                stat = pfad.stat()
                eintraege.append({"name": pfad.name, "bytes": stat.st_size,
                                  "geaendert": time.strftime("%Y-%m-%d %H:%M",
                                                             time.localtime(stat.st_mtime))})
        ergebnis[ordner] = eintraege
    return ergebnis


def ablegen(name: str, content: str) -> dict:
    """Textdatei atomar in vom-team/ ablegen (zur Freigabe durch Tino)."""
    inhalt = str(content)
    if len(inhalt) > _MAX_ZEICHEN:
        raise ValueError("Inhalt zu groß (max. 2 Mio. Zeichen).")
    ziel = _verzeichnis("vom-team") / _dateiname(name)
    tmp = ziel.with_name(ziel.name + ".tmp")
    tmp.write_text(inhalt, encoding="utf-8")
    tmp.replace(ziel)
    return {"abgelegt": str(ziel), "bytes": ziel.stat().st_size}


def freigeben(name: str) -> dict:
    """Datei aus vom-team/ nach freigegeben/ verschieben – auf Tinos Ansage."""
    quelle = _verzeichnis("vom-team") / _dateiname(name)
    if not quelle.is_file():
        raise ValueError("Keine solche Datei in vom-team/: " + name)
    ziel = _verzeichnis("freigegeben") / quelle.name
    if ziel.exists():
        stempel = time.strftime("%Y%m%d-%H%M%S")
        ziel = ziel.with_name(f"{ziel.stem}-{stempel}{ziel.suffix}")
    shutil.move(str(quelle), str(ziel))
    return {"freigegeben": str(ziel)}


def vorlage_ansehen(datei: str) -> dict:
    """Referenzbild aus dem Austausch als echtes Bild ansehen.

    Liefert das Bild als Anthropic-Bildblock: Mitarbeiter auf Haiku SEHEN
    damit Tinos Vorlagen wirklich, statt eine Textbeschreibung zu raten.
    Verkleinert auf max. 768 px Kante (JPEG), damit der Verlauf schlank bleibt.
    """
    import base64
    import io

    name = str(datei).strip().split("/")[-1]
    treffer = [p for p in AUSTAUSCH_DIR.rglob(name) if p.is_file()]
    if not treffer:
        kandidaten = sorted(p.name for p in (AUSTAUSCH_DIR / "an-team" / "vorlagen").glob("*")
                            if p.is_file())[:40]
        raise ValueError("Keine solche Datei im Austausch: " + name
                         + ". Vorhandene Vorlagen: " + ", ".join(kandidaten))
    pfad = treffer[0].resolve()
    if AUSTAUSCH_DIR not in pfad.parents:
        raise ValueError("Pfad liegt außerhalb des Austauschs.")
    if pfad.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise ValueError("Nur Bilddateien (png/jpg/webp/gif) lassen sich ansehen; "
                         "Texte liest du mit read_project_file.")
    from PIL import Image
    with Image.open(pfad) as bild:
        bild = bild.convert("RGB")
        bild.thumbnail((768, 768))
        puffer = io.BytesIO()
        bild.save(puffer, format="JPEG", quality=80)
    daten = base64.standard_b64encode(puffer.getvalue()).decode("ascii")
    return {
        "_bildbloecke": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": daten}},
            {"type": "text",
             "text": f"Vorlage {pfad.name} (verkleinert auf max. 768 px). "
                     "Übernimm Bildaufbau, Typografie und Stimmung – nicht den Wortlaut."},
        ],
        "text": f"[Bild-Anlage {pfad.name} – dieses Modell kann sie nicht sehen; "
                "beschreibe dem Designer stattdessen, was du brauchst.]",
    }


def register(registry: ToolRegistry) -> None:
    def _schema(properties: dict, required: list[str] | None = None) -> dict:
        result = {"type": "object", "properties": properties}
        if required:
            result["required"] = required
        return result

    registry.register(Tool("austausch_liste",
                           "Dateien im Austausch-Ordner auflisten (an-team, vom-team, freigegeben).",
                           liste, _schema({})))
    registry.register(Tool("austausch_ablegen",
                           "Fertige Textdatei in den Austausch (vom-team) zur Freigabe durch Tino legen.",
                           ablegen,
                           _schema({"name": {"type": "string"}, "content": {"type": "string"}},
                                   ["name", "content"])))
    registry.register(Tool("vorlage_ansehen",
                           "Referenzbild aus austausch/ (z. B. an-team/vorlagen/) als echtes Bild "
                           "ansehen – Pflicht vor jeder Gestaltungsarbeit.",
                           vorlage_ansehen,
                           _schema({"datei": {"type": "string",
                                              "description": "Dateiname der Vorlage, z. B. 1.png"}},
                                   ["datei"])))
    registry.register(Tool("austausch_freigeben",
                           "Datei aus vom-team nach freigegeben verschieben – nur wenn Tino es sagt.",
                           freigeben,
                           _schema({"name": {"type": "string"}}, ["name"])))
