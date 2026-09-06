from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter

from web.app import agent, ha, health, ict, mail, memory, system_monitor

router = APIRouter()


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
