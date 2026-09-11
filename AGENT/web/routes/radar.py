from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response

from web.app import dwd_radar, radar

router = APIRouter()


@router.get("/api/radar")
def get_radar():
    """DWD-RV zuerst (30 min zurück, 90 min voraus); RainViewer nur als
    Notnagel, wenn der DWD nicht liefert."""
    location = {"latitude": radar.LATITUDE, "longitude": radar.LONGITUDE,
                "zip": radar.ZIP, "city": radar.CITY, "zoom": radar.ZOOM}
    try:
        return {**dwd_radar.frames(), **location}
    except Exception as exc:
        logging.getLogger(__name__).warning("DWD-Radar nicht verfügbar: %s", exc)
        return radar.frames()


@router.get("/api/radar/frame/{key}.png")
def get_radar_frame(key: str):
    try:
        image = dwd_radar.png(key)
    except KeyError:
        raise HTTPException(404, "Radar-Frame nicht gefunden")
    return Response(content=image, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})
