from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import agent

router = APIRouter()


@router.get("/api/documents")
def documents_list():
    return {"documents": agent.documents.list_documents()}


@router.post("/api/documents/ingest")
async def documents_ingest(payload: dict):
    return await asyncio.to_thread(agent.documents.ingest, str(payload.get("path", "")))


@router.post("/api/documents/search")
async def documents_search(payload: dict):
    return await asyncio.to_thread(agent.documents.search, str(payload.get("query", "")),
                                   int(payload.get("top_k", 4)))


@router.delete("/api/documents")
def documents_forget(payload: dict):
    return agent.documents.forget_document(str(payload.get("path", "")))
