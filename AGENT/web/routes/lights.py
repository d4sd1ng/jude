from __future__ import annotations

from fastapi import APIRouter

from web.app import ha

router = APIRouter()


@router.get("/api/lights")
def lights(): return {"entities": ha.entities(), "states": ha.states() if ha.url and ha.token else []}


@router.post("/api/lights/{room}/{state}")
def switch_light(room: str, state: str): return {"result": ha.switch_light(room, state)}


@router.get("/api/home-actions")
def home_actions(): return ha.action_status()


@router.post("/api/home-actions/{group}/{action}")
def home_action(group: str, action: str): return {"result": ha.run_profile(group, action)}


@router.get("/api/grow")
def grow_metrics():
    try:
        return ha.grow_sensors()
    except Exception:
        return {"configured": False, "sensors": []}
