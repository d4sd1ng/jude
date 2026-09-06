from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import team

router = APIRouter()


@router.get("/api/agents")
def agents_list():
    return {"agents": team.list(), "skills": team.available_skills()}


@router.post("/api/agents")
def agents_create(payload: dict):
    return team.create(str(payload.get("name", "")), str(payload.get("role", "")),
                       list(payload.get("skills", [])), payload.get("model"))


@router.delete("/api/agents/{name}")
def agents_delete(name: str):
    return team.delete(name)


@router.post("/api/agents/{name}/run")
async def agents_run(name: str, payload: dict):
    return await asyncio.to_thread(team.run, name, str(payload.get("task", "")))


@router.post("/api/agents/{name}/stop")
def agents_stop(name: str):
    """Kein Abbruch eines laufenden Laufs (siehe team_runner.py) - nur eine
    Sperre gegen jeden neuen. Ein bereits laufender Auftrag arbeitet zu Ende."""
    return team.set_pausiert(name, True)


@router.post("/api/agents/{name}/start")
def agents_start(name: str):
    return team.set_pausiert(name, False)


@router.get("/api/agents/aktiv")
def agents_aktiv():
    """Wer gerade arbeitet, wer nicht, und wann zuletzt – für die Live-Anzeige
    im Schreibtisch."""
    return team.status_uebersicht()
