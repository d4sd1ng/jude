from __future__ import annotations

from fastapi import APIRouter

from web.app import notifications

router = APIRouter()


@router.get("/api/notifications")
def notification_list(unread_only: bool = True): return notifications.list(unread_only)


@router.post("/api/notifications/{notification_id}/read")
def notification_read(notification_id: str): return notifications.mark_read(notification_id)


@router.post("/api/notifications/read_all")
def notification_read_all(): return notifications.mark_all_read()
