"""Aktuelle Wetterwerte über Open-Meteo (kostenlos, ohne Schlüssel).

Nutzt die Koordinaten des Radar-Standorts und cached das Ergebnis, damit das
Cockpit-Polling keine Dauerlast auf der öffentlichen API erzeugt.
"""

from __future__ import annotations

import time

import requests

from services.radar import RadarService

_WEATHER_CODES = {
    0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt",
    45: "Nebel", 48: "Reifnebel", 51: "leichter Niesel", 53: "Niesel",
    55: "starker Niesel", 61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    66: "gefrierender Regen", 67: "starker gefrierender Regen", 71: "leichter Schnee",
    73: "Schnee", 75: "starker Schnee", 77: "Schneegriesel", 80: "leichte Schauer",
    81: "Schauer", 82: "heftige Schauer", 85: "Schneeschauer", 86: "starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel",
}


class WeatherService:
    CACHE_SECONDS = 600

    def __init__(self, latitude: float | None = None, longitude: float | None = None):
        self.latitude = latitude if latitude is not None else RadarService.LATITUDE
        self.longitude = longitude if longitude is not None else RadarService.LONGITUDE
        self._cached: dict | None = None
        self._cached_at = 0.0

    def current(self) -> dict:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.CACHE_SECONDS:
            return self._cached
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": self.latitude, "longitude": self.longitude,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                               "weather_code,wind_speed_10m",
                    "daily": "temperature_2m_min,temperature_2m_max",
                    "forecast_days": 1, "timezone": "Europe/Berlin"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        code = int(current.get("weather_code", -1))
        self._cached = {
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "condition": _WEATHER_CODES.get(code, "unbekannt"),
            "min": (daily.get("temperature_2m_min") or [None])[0],
            "max": (daily.get("temperature_2m_max") or [None])[0],
            "source": "Open-Meteo",
            "location": RadarService.ADDRESS,
        }
        self._cached_at = now
        return self._cached
