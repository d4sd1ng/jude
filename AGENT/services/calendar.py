from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.database import connection

from core.paths import CALENDAR_DIR as OUTPUT_DIR


class CalendarService:
    def create_confirmed(self, title: str, starts_at: str, ends_at: str,
                         description: str = "", location: str = "") -> str:
        start, end = datetime.fromisoformat(starts_at), datetime.fromisoformat(ends_at)
        local_zone = ZoneInfo("Europe/Berlin")
        start = start.replace(tzinfo=local_zone) if start.tzinfo is None else start
        end = end.replace(tzinfo=local_zone) if end.tzinfo is None else end
        if end <= start:
            raise ValueError("Terminende muss nach dem Beginn liegen.")
        event_id = uuid.uuid4().hex
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR / f"{start.date().isoformat()}_{event_id[:8]}.ics"

        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        def ical(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if dt.tzinfo else dt.strftime("%Y%m%dT%H%M%S")
        target.write_text("\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jude//Calendar//DE", "BEGIN:VEVENT",
            f"UID:{event_id}@jude.local", f"DTSTAMP:{stamp}", f"DTSTART:{ical(start)}", f"DTEND:{ical(end)}",
            f"SUMMARY:{esc(title)}", f"DESCRIPTION:{esc(description)}", f"LOCATION:{esc(location)}",
            "END:VEVENT", "END:VCALENDAR", "",
        ]), encoding="utf-8")
        with connection() as db:
            db.execute("INSERT INTO calendar_events(id,title,starts_at,ends_at,description,location,created_at) VALUES(?,?,?,?,?,?,?)",
                       (event_id, title, start.isoformat(), end.isoformat(), description, location, datetime.now(timezone.utc).isoformat()))
        return str(target)

    @staticmethod
    def list() -> list[dict]:
        with connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM calendar_events ORDER BY starts_at").fetchall()]
