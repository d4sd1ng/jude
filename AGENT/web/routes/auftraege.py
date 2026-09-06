from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/auftraege")
def auftraege_api(status: str = "alle"):
    """Auftragsbuch für den Schreibtisch-Tab."""
    from services.auftraege import Auftragsbuch
    return Auftragsbuch().liste(status=status, limit=100)
