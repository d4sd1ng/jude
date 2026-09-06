from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web.app import memory

router = APIRouter()


@router.get("/api/memory")
def memory_list(status: str | None = None):
    return {"items": memory.list(status=status), "stats": memory.training_stats()}


@router.post("/api/memory")
def memory_add(payload: dict):
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(400, "Inhalt fehlt")
    return memory.remember(content, kind="explicit", status="active", source="gui", confidence=1.0)


@router.post("/api/memory/{item_id}/approve")
def memory_approve(item_id: str):
    return memory.set_status(item_id, "active")


@router.delete("/api/memory/{item_id}")
def memory_delete(item_id: str):
    return memory.delete_id(item_id)
