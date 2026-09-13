from __future__ import annotations

from fastapi import APIRouter

from web.app import shopping

router = APIRouter()


@router.post("/api/shopping")
def compare(payload: dict):
    return shopping.compare(str(payload.get("category", "")), str(payload.get("brand", "gstar")),
                            str(payload.get("size", "XXL")))
