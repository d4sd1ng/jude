from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/auftraege")
def auftraege_api(status: str = "alle", agent: str | None = None):
    """Auftragsbuch für den Schreibtisch-Tab UND für den Start-Knopf eines
    einzelnen Mitarbeiters (agent-Filter), der ihn gegen seinen echten
    offenen Auftrag statt einer erfundenen Pauschalaufgabe starten soll."""
    from services.auftraege import Auftragsbuch
    return Auftragsbuch().liste(status=status, agent=agent, limit=100)
