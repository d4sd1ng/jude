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
    """Sperrt jeden neuen Lauf UND bricht einen gerade laufenden so bald wie
    moeglich ab (vor dem naechsten Werkzeugaufruf - siehe ABBRUCH_ANGEFORDERT
    in team_runner.py; ein bereits laufender einzelner Werkzeugaufruf laeuft
    noch zu Ende, kein Python-Thread laesst sich von aussen sauber toeten)."""
    from services.team_runner import ABBRUCH_ANGEFORDERT
    ABBRUCH_ANGEFORDERT.add(name)
    return team.set_pausiert(name, True)


@router.post("/api/agents/{name}/start")
def agents_start(name: str):
    """'Start' im Cockpit hiess bisher nur 'darf wieder', nicht 'tut jetzt
    etwas' - set_pausiert(False) hob lediglich die Sperre auf, der
    Mitarbeiter blieb untaetig, bis ihn zufaellig ein anderer Trigger
    (Auftrag, Revision, naechster Tick) erreichte. Tino: 'no matter what if
    i hit start on any employee they have to start immediatly with working'
    (11.09.2026) - danach hier zusaetzlich sofort ueber denselben
    begrenzten Worker-Pool wie eine Revision einreihen (dispatch_now)."""
    ergebnis = team.set_pausiert(name, False)
    from services.scheduler import SchedulerService
    sched = SchedulerService.aktuelle()
    if sched is not None:
        sched.dispatch_now(name, "Beginne sofort mit deiner naechsten anstehenden Aufgabe "
                                  "gemaess deiner Rolle (siehe oben im Systemprompt: QUELLE/ARBEIT).")
    return ergebnis


@router.get("/api/agents/aktiv")
def agents_aktiv():
    """Wer gerade arbeitet, wer nicht, und wann zuletzt – für die Live-Anzeige
    im Schreibtisch."""
    return team.status_uebersicht()
