from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from web.app import ocr as ocr_service

router = APIRouter()


@router.post("/api/ocr")
async def run_ocr(file: UploadFile = File(...), language: str = Form("deu+eng")):
    return ocr_service.extract(await file.read(), file.filename or "upload", language)
