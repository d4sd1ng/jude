from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services.database import connection
from web.app import meals, recipes

router = APIRouter()


@router.get("/api/recipes/today")
def recipe_today():
    """Rezept des Tages aus Notion. Ist Notion nicht eingerichtet, meldet der
    Endpunkt das, statt zu werfen – das Cockpit zeigt dann einen Hinweis."""
    if not recipes.available():
        return {"configured": False, "recipe": None}
    try:
        return {"configured": True, "recipe": recipes.today()}
    except Exception as exc:
        return {"configured": True, "recipe": None, "error": str(exc)[:200]}


@router.get("/api/meals/current")
def meal_current():
    """Der Tag des zuletzt erzeugten Plans, der auf heute fällt. Läuft der Plan
    aus, bleibt kein Tag übrig und das Cockpit zeigt einen Hinweis."""
    with connection() as db:
        row = db.execute("SELECT created_at, plan_json FROM meal_plans ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        return {"day": None}
    plan = json.loads(row["plan_json"])
    days = plan.get("days", [])
    created = datetime.fromisoformat(row["created_at"])
    index = (datetime.now(timezone.utc) - created).days
    return {"day": days[index] if 0 <= index < len(days) else None}


@router.post("/api/meals")
async def meal(payload: dict):
    return await asyncio.to_thread(meals.generate, int(payload.get("days", 7)), int(payload.get("people", 2)))


@router.get("/api/meals/{plan_id}/pdf")
def meal_pdf(plan_id: str):
    with connection() as db:
        row = db.execute("SELECT pdf_path FROM meal_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Essensplan nicht gefunden")
    path = Path(row["pdf_path"]).resolve()
    from core.paths import MEALS_DIR
    allowed = MEALS_DIR.resolve()
    if allowed not in path.parents or not path.is_file():
        raise HTTPException(404, "PDF nicht gefunden")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
