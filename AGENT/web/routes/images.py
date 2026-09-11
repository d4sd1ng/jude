from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from web.app import blender, images

router = APIRouter()


@router.get("/api/images")
def images_list():
    return {"items": images.list_recent()}


@router.post("/api/images/generate")
async def images_generate(payload: dict):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "Prompt fehlt")
    return await asyncio.to_thread(images.generate, prompt, str(payload.get("size", "1024x1024")))


@router.post("/api/images/edit")
async def images_edit(prompt: str = Form(...), file: UploadFile = File(...),
                      mask: UploadFile | None = File(None), size: str = Form("1024x1024")):
    image_bytes = await file.read()
    mask_bytes = await mask.read() if mask is not None else None
    return await asyncio.to_thread(images.edit, image_bytes, prompt, file.filename or "bild.png", mask_bytes, size)


@router.post("/api/images/render3d")
async def images_render3d(payload: dict):
    script = str(payload.get("blender_python", "")).strip()
    if not script:
        raise HTTPException(400, "Blender-Skript fehlt")
    return await asyncio.to_thread(blender.render, script, str(payload.get("title", "szene")),
                                   int(payload.get("width", 1024)), int(payload.get("height", 1024)),
                                   str(payload.get("engine", "BLENDER_EEVEE_NEXT")))
