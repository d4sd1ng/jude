from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import agent, news

router = APIRouter()


@router.get("/api/news")
def get_news(): return news.fetch()


@router.get("/api/news/brief")
async def get_news_brief():
    articles = await asyncio.to_thread(news.fetch)
    response = await asyncio.to_thread(
        agent.router.call_with_fallback,
        [{"role": "user", "content": news.journalist_prompt(articles)}],
    )
    return {"brief": response.get("content", ""), "model": response.get("_model"), "news": articles}
