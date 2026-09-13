from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, UploadFile

from web.app import vision as vision_service

router = APIRouter()


@router.post("/api/vision")
async def vision_describe(file: UploadFile = File(...), question: str = Form("Beschreibe dieses Bild genau auf Deutsch.")):
    image_bytes = await file.read()
    return await asyncio.to_thread(vision_service.describe, image_bytes, question)
