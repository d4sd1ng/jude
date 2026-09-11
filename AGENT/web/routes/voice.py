from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import voice as voice_service

router = APIRouter()


@router.get("/api/voice")
def voice_status():
    return voice_service.status()


@router.get("/api/voice/events")
def voice_events(since: int = 0):
    return voice_service.events(since)


@router.post("/api/voice/start")
def voice_start():
    # Knopf im GUI = bewusste Aktivierung, kein zusaetzliches Wachwort noetig.
    return voice_service.start(wachwort_pflicht=False)


@router.post("/api/voice/stop")
async def voice_stop():
    return await asyncio.to_thread(voice_service.stop)


@router.post("/api/voice/phrases")
def voice_phrases(payload: dict):
    return voice_service.set_phrases(str(payload.get("wake_word", "")), str(payload.get("sleep_word", "")),
                             str(payload.get("greeting", "")), str(payload.get("farewell", "")))


@router.post("/api/voice/skip")
def voice_skip():
    return voice_service.skip()


@router.post("/api/voice/skip-all")
def voice_skip_all():
    return voice_service.skip_all()
