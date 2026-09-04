from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from services.database import audit, connection

Executor = Callable[[str, dict], str]


class ConfirmationQueue:
    ALLOWED_ACTIONS = {"mail_send", "mail_delete", "git_merge", "file_delete", "external_write", "calendar_create",
                       "home_switch", "home_action", "mail_archive", "code_write", "code_commit",
                       "code_push", "code_pr", "code_branch", "code_clone", "code_pull",
                       "shell_command", "create_agent", "ssh_command", "scp_transfer", "notion_write"}

    def request(self, action_type: str, summary: str, payload: dict, agent: str = "") -> dict:
        if action_type not in self.ALLOWED_ACTIONS:
            raise ValueError("Dieser Aktionstyp ist nicht bestätigungsfähig.")
        if not summary.strip() or not isinstance(payload, dict):
            raise ValueError("Bestätigung benötigt Zusammenfassung und Payload.")
        agent = str(agent or "").strip()
        created = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False)
        # Ein Mitarbeiter, der eine Revision überarbeitet, ruft dasselbe Werkzeug
        # einfach erneut auf – ohne diesen Abgleich häufte sich pro Runde eine
        # weitere Zeile an, und die alte Revision blieb für immer offen im Prompt.
        # Galt bisher nur für 'revision': eine noch UNENTSCHIEDENE ('pending')
        # Anfrage wurde nie abgeglichen, darum haeufte jeder stuendliche
        # Auftragswaechter-Lauf eine weitere Zeile fuer dieselbe Datei an, ohne
        # dass Tino je entschieden hatte – 73 haengende Bestaetigungen fuer
        # 3 Dateien, gemessen 03.09.2026.
        if agent:
            with connection() as db:
                laufend = db.execute(
                    "SELECT id, payload_json FROM confirmations WHERE agent=? AND action_type=? "
                    "AND status IN ('revision','pending') ORDER BY created_at DESC", (agent, action_type),
                ).fetchall()
            pfad = payload.get("path")
            treffer = None
            for row in laufend:
                if pfad is not None and json.loads(row["payload_json"]).get("path") == pfad:
                    treffer = row["id"]
                    break
            if treffer is None and pfad is None and laufend:
                treffer = laufend[0]["id"]
            if treffer is not None:
                with connection() as db:
                    db.execute(
                        "UPDATE confirmations SET status='pending', summary=?, payload_json=?, "
                        "runde=runde+1, anmerkung='', decided_at=NULL, result=NULL, created_at=? WHERE id=?",
                        (summary, payload_json, created, treffer),
                    )
                audit("confirmation_requested", treffer, {"action_type": action_type, "summary": summary, "ueberarbeitet": True}, True)
                try:
                    from services.notifications import NotificationService
                    NotificationService().create("bestaetigung", f"Freigabe nötig: {action_type}", summary[:200])
                except Exception:
                    pass
                return {"id": treffer, "action_type": action_type, "summary": summary, "status": "pending", "created_at": created}
        action_id = uuid.uuid4().hex[:16]
        with connection() as db:
            db.execute(
                "INSERT INTO confirmations(id,action_type,summary,payload_json,created_at,agent) VALUES(?,?,?,?,?,?)",
                (action_id, action_type, summary, payload_json, created, agent),
            )
        audit("confirmation_requested", action_id, {"action_type": action_type, "summary": summary}, True)
        # Ohne Meldung wartete die Schleuse stumm: Joanas Landingpage-Schreib-
        # vorgänge lagen hier, ohne dass Tino je davon erfuhr (16.08.).
        try:
            from services.notifications import NotificationService
            NotificationService().create("bestaetigung",
                                         f"Freigabe nötig: {action_type}",
                                         summary[:200])
        except Exception:
            pass
        return {"id": action_id, "action_type": action_type, "summary": summary, "status": "pending", "created_at": created}

    def list(self, status: str = "pending") -> list[dict]:
        with connection() as db:
            rows = db.execute(
                "SELECT id,action_type,summary,payload_json,status,created_at,decided_at,result,"
                "agent,anmerkung,runde FROM confirmations WHERE status=? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def offene_revisionen(self, agent: str) -> list[dict]:
        """Was dieser Mitarbeiter überarbeiten soll — kommt in seinen Prompt."""
        with connection() as db:
            rows = db.execute(
                "SELECT id,action_type,summary,payload_json,anmerkung,runde FROM confirmations "
                "WHERE agent=? AND status='revision' ORDER BY created_at", (agent,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def revision(self, action_id: str, anmerkung: str) -> dict:
        """Zurück an den Mitarbeiter, statt auszuführen. Die Anmerkung ist Pflicht —
        ohne sie weiß er nicht, was zu ändern ist, und liefert dasselbe noch einmal."""
        if not str(anmerkung).strip():
            raise ValueError("Für eine Revision wird eine Anmerkung benötigt.")
        with connection() as db:
            row = db.execute("SELECT id, status, agent FROM confirmations WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError("Unbekannte Bestätigung.")
            if row["status"] != "pending":
                raise ValueError("Diese Aktion wurde bereits entschieden oder ausgeführt.")
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE confirmations SET status='revision', anmerkung=?, decided_at=? WHERE id=?",
                (str(anmerkung)[:2000], now, action_id),
            )
        audit("confirmation_revision", action_id, {"anmerkung": anmerkung}, True)
        return {"id": action_id, "status": "revision", "agent": row["agent"]}

    def decide(self, action_id: str, approve: bool, executor: Executor) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        rejected = False
        with connection() as db:
            row = db.execute("SELECT * FROM confirmations WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise ValueError("Unbekannte Bestätigung.")
            if row["status"] != "pending":
                raise ValueError("Diese Aktion wurde bereits entschieden.")
            if not approve:
                db.execute("UPDATE confirmations SET status='rejected',decided_at=?,result=? WHERE id=?", (now, "Vom Nutzer abgelehnt", action_id))
                action_type, rejected = row["action_type"], True
            else:
                payload = json.loads(row["payload_json"])
                updated = db.execute("UPDATE confirmations SET status='executing',decided_at=? WHERE id=? AND status='pending'", (now, action_id))
                if updated.rowcount != 1:
                    raise ValueError("Diese Aktion wird bereits ausgeführt.")
                action_type = row["action_type"]
        if rejected:
            audit("confirmation_rejected", action_id, {"action_type": action_type}, True)
            return {"id": action_id, "status": "rejected", "result": "Vom Nutzer abgelehnt"}
        try:
            result = executor(action_type, payload)
        except Exception as exc:
            with connection() as db:
                db.execute("UPDATE confirmations SET status='failed',result=? WHERE id=?", (str(exc), action_id))
            audit("confirmation_execution", action_id, {"action_type": action_type, "error": str(exc)}, False)
            raise
        with connection() as db:
            db.execute("UPDATE confirmations SET status='approved',decided_at=?,result=? WHERE id=?", (now, result, action_id))
        audit("confirmation_execution", action_id, {"action_type": action_type}, True)
        return {"id": action_id, "status": "approved", "result": result}
