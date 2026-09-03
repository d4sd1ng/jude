"""Systemzustand und Fehlerüberblick.

Fasst zusammen, ob die tragenden Teile laufen: Ollama + Modelle, Speicherplatz,
Datenbank, Wake-Word-Modell, Mikrofon, Cloud-Budget – und listet die letzten
Fehler (aus Audit-Log, fehlgeschlagenen Bestätigungen und Aufgaben-Fehlern).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import requests

from core.paths import AI_DATA_ROOT, MODELS_DIR
from services.database import connection


class HealthService:
    def __init__(self, router=None, voice=None):
        self.router = router
        self.voice = voice
        self.ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

    def _ollama(self) -> dict:
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=3)
            response.raise_for_status()
            return {"reachable": True, "models": sorted(m["name"] for m in response.json().get("models", []))}
        except requests.RequestException as exc:
            return {"reachable": False, "error": str(exc)[:120]}

    @staticmethod
    def _disk() -> dict:
        usage = shutil.disk_usage(AI_DATA_ROOT)
        return {"free_gb": round(usage.free / 1e9, 1), "total_gb": round(usage.total / 1e9, 1),
                "used_percent": round(usage.used / usage.total * 100, 1)}

    @staticmethod
    def _database() -> dict:
        try:
            with connection() as db:
                turns = db.execute("SELECT COUNT(*) AS n FROM conversation_turns").fetchone()["n"]
                notes = db.execute("SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL").fetchone()["n"]
            return {"ok": True, "conversation_turns": turns, "unread_notifications": notes}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:120]}

    @staticmethod
    def _microphone() -> dict:
        try:
            result = subprocess.run(["pactl", "list", "short", "sources"],
                                    capture_output=True, text=True, timeout=4)
            sources = [line for line in result.stdout.splitlines() if line and ".monitor" not in line]
            return {"available": bool(sources), "count": len(sources)}
        except Exception:
            return {"available": None, "note": "nicht prüfbar"}

    def _wakeword(self) -> dict:
        model = MODELS_DIR / "wakeword" / "jude_angetreten.onnx"
        return {"trained_model_present": model.is_file(),
                "size_bytes": model.stat().st_size if model.is_file() else 0}

    @staticmethod
    def _recent_errors(limit: int = 10) -> list[dict]:
        errors = []
        with connection() as db:
            for row in db.execute("SELECT created_at,action,target FROM audit_log WHERE success=0 "
                                  "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
                errors.append({"when": row["created_at"], "source": "audit", "what": f"{row['action']}: {row['target']}"[:200]})
            for row in db.execute("SELECT created_at,title,message FROM notifications WHERE kind='scheduled_error' "
                                  "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
                errors.append({"when": row["created_at"], "source": "scheduler", "what": f"{row['title']}: {row['message']}"[:200]})
        errors.sort(key=lambda e: e["when"], reverse=True)
        return errors[:limit]

    def snapshot(self) -> dict:
        ollama = self._ollama()
        disk = self._disk()
        database = self._database()
        errors = self._recent_errors()
        budget = {}
        if self.router is not None:
            try:
                status = self.router.status()
                budget = {"used_usd": status.get("budget_used_usd"), "limit_usd": status.get("budget_limit_usd")}
            except Exception:
                budget = {}
        checks = {
            "ollama": ollama,
            "models_installed": len(ollama.get("models", [])),
            "disk": disk,
            "database": database,
            "microphone": self._microphone(),
            "wakeword": self._wakeword(),
            "voice": self.voice.status() if self.voice is not None else {"running": False},
            "budget": budget,
            "recent_errors": errors,
        }
        # Kernkomponenten bestimmen den Status; einzelne alte Fehler sind nur Hinweise.
        healthy = ollama.get("reachable") and database.get("ok") and disk["free_gb"] > 2
        checks["status"] = "ok" if healthy else "degraded"
        return checks
