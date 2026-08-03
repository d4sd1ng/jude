from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from services.database import connection


class NotificationService:
    @staticmethod
    def create(kind: str, title: str, message: str, payload: dict | None = None) -> dict:
        item = {"id": uuid.uuid4().hex[:16], "kind": kind, "title": title, "message": message,
                "payload": payload or {}, "created_at": datetime.now(timezone.utc).isoformat(), "read_at": None}
        with connection() as db:
            db.execute("INSERT INTO notifications(id,kind,title,message,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                       (item["id"], kind, title, message, json.dumps(item["payload"], ensure_ascii=False), item["created_at"]))
        return item

    @staticmethod
    def list(unread_only: bool = True, limit: int = 100) -> list[dict]:
        with connection() as db:
            query = ("SELECT * FROM notifications WHERE read_at IS NULL ORDER BY created_at DESC LIMIT ?"
                     if unread_only else "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?")
            rows = db.execute(query, (max(1, min(limit, 500)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    @staticmethod
    def mark_read(notification_id: str) -> dict:
        when = datetime.now(timezone.utc).isoformat()
        with connection() as db:
            updated = db.execute("UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL", (when, notification_id))
        if updated.rowcount != 1:
            raise ValueError("Benachrichtigung unbekannt oder bereits gelesen.")
        return {"id": notification_id, "read_at": when}
