from __future__ import annotations

from fastapi import APIRouter

from web.app import agent

router = APIRouter()


@router.get("/api/router/providers")
def router_providers():
    """Welche Provider gerade gesperrt sind (429 oder leeres Guthaben) – ohne
    Neustart einsehbar. Provider, die noch nie gesperrt waren, tauchen nicht auf."""
    from core.model_router import provider_sperren_uebersicht
    return {"providers": provider_sperren_uebersicht()}


@router.post("/api/router/providers/{provider}/unlock")
def router_provider_unlock(provider: str):
    """Sperre eines Providers sofort aufheben, z. B. direkt nachdem Guthaben
    aufgeladen wurde – bisher gab es dafuer nur einen Neustart oder eine
    Python-Konsole am laufenden Prozess."""
    from core.model_router import provider_gesperrt, provider_sperre_aufheben
    war_gesperrt = provider_gesperrt(provider)
    provider_sperre_aufheben(provider)
    return {"provider": provider, "war_gesperrt": war_gesperrt, "jetzt_gesperrt": provider_gesperrt(provider)}


@router.post("/api/routing/{route_id}/feedback")
def routing_feedback(route_id: str, payload: dict):
    return agent.router.feedback(route_id, int(payload.get("value", 0)))
