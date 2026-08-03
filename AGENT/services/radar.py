from __future__ import annotations

import os
from datetime import datetime, timezone

import requests


class RadarService:
    # Standort 35039 Marburg (Berliner Straße); per Umgebungsvariable anpassbar.
    LATITUDE = float(os.getenv("JUDE_RADAR_LAT", "50.8075"))
    LONGITUDE = float(os.getenv("JUDE_RADAR_LON", "8.7708"))
    ZIP = os.getenv("JUDE_RADAR_ZIP", "35039")
    CITY = os.getenv("JUDE_RADAR_CITY", "Marburg")
    ADDRESS = os.getenv("JUDE_RADAR_ADDRESS", f"Berliner Straße, {ZIP} {CITY}, Germany")
    ZOOM = int(os.getenv("JUDE_RADAR_ZOOM", "9"))

    def frames(self) -> dict:
        response = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=15)
        response.raise_for_status()
        data = response.json()
        frames = [{"time": frame["time"], "iso_time": datetime.fromtimestamp(frame["time"], timezone.utc).isoformat(),
                   "path": frame["path"]} for frame in data.get("radar", {}).get("past", [])]
        return {"host": data.get("host"), "generated": data.get("generated"), "frames": frames,
                "latitude": self.LATITUDE, "longitude": self.LONGITUDE, "address": self.ADDRESS,
                "zip": self.ZIP, "city": self.CITY, "zoom": self.ZOOM,
                "max_zoom": 12, "source": "RainViewer", "status": "live" if frames else "empty"}
