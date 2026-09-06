from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web.app import confirmations, executor

router = APIRouter()


@router.get("/api/confirmations")
def confirmation_list(): return confirmations.list()


@router.post("/api/confirmations")
def confirmation_request(payload: dict):
    return confirmations.request(str(payload["action_type"]), str(payload["summary"]), dict(payload["payload"]))


@router.post("/api/confirmations/{action_id}/{decision}")
def confirmation_decide(action_id: str, decision: str, payload: dict | None = None):
    if decision not in {"approve", "reject", "revision"}:
        raise HTTPException(400, "Entscheidung muss approve, reject oder revision sein")
    if decision == "revision":
        anmerkung = str((payload or {}).get("anmerkung", "")).strip()
        if not anmerkung:
            raise HTTPException(400, "Für eine Revision wird eine Anmerkung benötigt")
        try:
            return confirmations.revision(action_id, anmerkung)
        except KeyError:
            raise HTTPException(404, "Unbekannte Bestätigung")
    return confirmations.decide(action_id, decision == "approve", executor)
