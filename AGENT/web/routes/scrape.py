from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import facts, scraper

router = APIRouter()


@router.post("/api/scrape")
async def scrape(payload: dict): return await asyncio.to_thread(scraper.extract, str(payload.get("url", "")))


@router.post("/api/fact-check")
async def fact_check(payload: dict): return await asyncio.to_thread(facts.check, str(payload.get("url", "")))
