from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import agent, ict

router = APIRouter()


@router.post("/api/ict/train/{symbol}")
async def ict_train(symbol: str):
    return await asyncio.to_thread(ict.train_live, symbol.upper())


@router.get("/api/ict/status")
def ict_status(): return ict.stack_status()


@router.get("/api/ict/cards")
def ict_cards(): return ict.cards()


@router.post("/api/ict/analyse/{symbol}")
async def ict_analyse(symbol: str): return await asyncio.to_thread(ict.analyse_live, agent.router, symbol.upper())
