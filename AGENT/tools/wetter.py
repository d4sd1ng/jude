from __future__ import annotations

import requests

from core.tool_registry import Tool, ToolRegistry


def get_current_weather(location: str, unit: str = "celsius") -> dict:
    words = location.replace(",", " ").split()
    queries = [location] + [" ".join(words[:index]) for index in range(len(words) - 1, 0, -1)]
    results = []
    for query in dict.fromkeys(queries):
        geocode = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={
            "name": query, "count": 1, "language": "de", "format": "json",
        }, timeout=15)
        geocode.raise_for_status()
        results = geocode.json().get("results") or []
        if results:
            break
    if not results:
        raise ValueError(f"Ort nicht gefunden: {location}")
    place = results[0]
    temperature_unit = "fahrenheit" if unit == "fahrenheit" else "celsius"
    forecast = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m",
        "temperature_unit": temperature_unit, "timezone": "auto",
    }, timeout=15)
    forecast.raise_for_status()
    return {"location": f"{place.get('name')}, {place.get('country')}", "coordinates": [place["latitude"], place["longitude"]],
            "current": forecast.json().get("current", {}), "units": forecast.json().get("current_units", {}),
            "source": "Open-Meteo", "license": "CC BY 4.0"}


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="get_current_weather", description="Ruft aktuelle Wetterdaten für einen Ort über Open-Meteo ab.",
        func=get_current_weather,
        param_schema={"type": "object", "properties": {
            "location": {"type": "string"}, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        }, "required": ["location"]},
    ))
