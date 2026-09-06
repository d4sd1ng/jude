from __future__ import annotations

from fastapi import APIRouter

from web.app import scheduler

router = APIRouter()


@router.get("/api/tasks")
def tasks_list():
    return {"tasks": scheduler.list()}


@router.post("/api/tasks")
def tasks_create(payload: dict):
    return scheduler.create(str(payload.get("name", "")), str(payload.get("action_type", "prompt")),
                            at=payload.get("at"), every_minutes=payload.get("every_minutes"),
                            prompt=payload.get("prompt"), tool=payload.get("tool"),
                            tool_args=payload.get("tool_args"), speak=bool(payload.get("speak", True)))


@router.delete("/api/tasks/{task_id}")
def tasks_delete(task_id: str):
    return scheduler.delete(task_id)
