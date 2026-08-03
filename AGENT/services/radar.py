from __future__ import annotations

from datetime import datetime, timezone

import requests


class RadarService:
    LATITUDE = 50.8075
    LONGITUDE = 8.7708
    ADDRESS = "Berliner Straße, 35039 Marburg, Germany"

    def frames(self) -> dict:
        response = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=15)
        response.raise_for_status()
        data = response.json()
        frames = [{"time": frame["time"], "iso_time": datetime.fromtimestamp(frame["time"], timezone.utc).isoformat(),
                   "path": frame["path"]} for frame in data.get("radar", {}).get("past", [])]
        return {"host": data.get("host"), "generated": data.get("generated"), "frames": frames,
                "latitude": self.LATITUDE, "longitude": self.LONGITUDE, "address": self.ADDRESS,
                "max_zoom": 7, "source": "RainViewer", "status": "live" if frames else "empty"}
