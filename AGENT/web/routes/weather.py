from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import briefing, weather

router = APIRouter()


@router.get("/api/weather")
async def weather_current():
    return await asyncio.to_thread(weather.current)


@router.get("/api/briefing")
async def briefing_current():
    return await asyncio.to_thread(briefing.data)
