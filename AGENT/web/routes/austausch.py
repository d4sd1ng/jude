from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.paths import IMAGES_DIR

router = APIRouter()


@router.post("/api/austausch/upload")
async def austausch_upload(file: UploadFile = File(...)):
    """Dokument von Tino entgegennehmen: landet in austausch/an-team."""
    from tools.austausch import AUSTAUSCH_DIR, _dateiname
    name = _dateiname(file.filename or "dokument")
    ziel = AUSTAUSCH_DIR / "an-team" / name
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(await file.read())
    return {"name": name, "pfad": str(ziel)}


@router.get("/api/austausch/datei")
def austausch_datei(pfad: str):
    """Datei aus dem Austausch ODER aus den erzeugten Bildern fuer die
    Vorschau ausliefern – NUR von dort. Ohne den harten Pfad-Zaun waere das
    ein Lese-Zugang auf die ganze Platte.

    IMAGES_DIR war bis 06.09.2026 nicht in den erlaubten Wurzeln: jedes von
    generate_image erzeugte Bild liegt dort (nicht in austausch/), weshalb
    JEDE Grafik-Vorschau in der Abnahme mit 403 scheiterte, obwohl die Datei
    real existierte - gemessen am Pipeline-Piloten (social, 05.09.2026):
    Renate stufte 'Visual Consistency: bestanden' korrekt ein, Tino sah in
    der Abnahme trotzdem keine Grafik."""
    from tools.austausch import AUSTAUSCH_DIR
    wurzeln = (AUSTAUSCH_DIR.resolve(), IMAGES_DIR.resolve())
    roh = pfad.strip()
    ziel = (Path(roh) if roh.startswith("/") else AUSTAUSCH_DIR.resolve().parent / roh).resolve()
    if (not any(w in ziel.parents for w in wurzeln) or not ziel.is_file()) and roh.startswith("/"):
        # In reviews.quelle/auftraege.beschreibung stehen teils absolute Pfade
        # aus einem frueheren Projekt-Wurzelverzeichnis (vor dem Umzug nach
        # /mnt/ai-data/ - z.B. '/media/d4sd1ng/AI-Data/Projects/Jude/images/...').
        # Diese Zeilen wurden nicht migriert, darum 404/403 trotz real
        # vorhandener Datei (gemessen 07.09.2026: Grafik-Vorschau blieb leer,
        # obwohl dieselbe Datei unter dem aktuellen Pfad existiert). Fallback:
        # Basisname unter der aktuellen Wurzel suchen, statt den alten
        # absoluten Pfad woertlich zu nehmen.
        kandidat = IMAGES_DIR.resolve() / Path(roh).name
        if kandidat.is_file():
            ziel = kandidat
    if not any(w in ziel.parents for w in wurzeln) or not ziel.is_file():
        raise HTTPException(403, "Nur Dateien im Austausch-Ordner oder den erzeugten Bildern.")
    return FileResponse(ziel)
