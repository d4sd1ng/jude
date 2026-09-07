from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

from fastapi import APIRouter

from web.app import agent, ha, health, ict, mail, memory, system_monitor

router = APIRouter()


@router.post("/api/system/restart")
def system_restart():
    """Startet einen neuen Server-Prozess und beendet diesen hier danach -
    von Hand ueber die GUI statt ueber ein Terminal, das Tino sonst jedes
    Mal selbst offen haben musste, um main.py neu zu starten.

    Der neue Prozess repliziert exakt die eigenen Start-Argumente (sys.argv)
    und laeuft komplett losgeloest (start_new_session) weiter, auch wenn
    dieser Prozess gleich endet. Die Antwort geht zuerst raus, das eigentliche
    Beenden passiert in einem Hintergrund-Thread mit kurzer Verzoegerung -
    sonst kaeme die HTTP-Antwort nie beim Browser an."""
    def _neu_starten() -> None:
        import time
        time.sleep(0.5)
        subprocess.Popen([sys.executable] + sys.argv, cwd=os.getcwd(),
                         start_new_session=True)
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_neu_starten, daemon=True).start()
    return {"neustart": True, "hinweis": "Server startet neu - die Seite laedt in ein paar Sekunden von selbst neu."}


@router.get("/api/status")
def status():
    return {"router": agent.router.status(),
            "mail": mail.account_status(), "ict": ict.stack_status(probe=False),
            "home_assistant_configured": bool(ha.url and ha.token), "news_configured": bool(os.getenv("NEWS_API_KEY")),
            "home_actions": ha.action_status(), "fake_checker": "ready", "scraper": "public_http_only",
            "memory": memory.training_stats()}


@router.get("/api/system")
def system_snapshot():
    return system_monitor.snapshot()


@router.get("/api/health")
def health_snapshot():
    return health.snapshot()


@router.get("/api/backups")
def backups_list():
    return {"backups": agent.backup.list()}


@router.post("/api/backups")
async def backups_run():
    return await asyncio.to_thread(agent.backup.run)
