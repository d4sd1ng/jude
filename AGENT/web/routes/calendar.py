from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from web.app import calendar

router = APIRouter()


@router.get("/api/calendar")
def calendar_list(): return calendar.list()


@router.get("/api/calendar/{event_id}/ics")
def calendar_ics(event_id: str):
    if not event_id.isalnum():
        raise HTTPException(400, "Ungültige Termin-ID")
    from core.paths import CALENDAR_DIR
    matches = list(CALENDAR_DIR.glob(f"*_{event_id[:8]}.ics"))
    if len(matches) != 1:
        raise HTTPException(404, "Kalenderdatei nicht gefunden")
    return FileResponse(matches[0], media_type="text/calendar", filename=matches[0].name)
