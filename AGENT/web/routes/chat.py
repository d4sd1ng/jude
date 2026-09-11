from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from web.app import agent, agent_lock, chat_lock, voice

router = APIRouter()


def _chat_sync(text: str) -> dict:
    with agent_lock:
        answer_text = agent.process_input(text)
        voice.speak_answer(answer_text)
        return {"answer": answer_text, "model": agent.last_model, "route_id": agent.last_route_id}


@router.post("/api/chat")
async def chat(payload: dict):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Text fehlt")
    async with chat_lock:
        return await asyncio.to_thread(_chat_sync, text)


@router.get("/api/chat/model")
def chat_model_get():
    return {"model": agent.force_model or ""}


@router.post("/api/chat/model")
def chat_model_set(payload: dict):
    """Modell für den Haupt-Chat fest vorgeben, statt der automatischen Wahl –
    z.B. wenn Tino vorher schon weiß, dass das Standardmodell die Aufgabe
    nicht schafft. Leer/„auto" schaltet zurück auf die normale Routenwahl."""
    model = str(payload.get("model", "")).strip()
    if model and model not in agent.router.models:
        raise HTTPException(400, f"Unbekanntes Modell: {model}")
    with agent_lock:
        agent.force_model = model or None
    return {"model": agent.force_model or ""}
