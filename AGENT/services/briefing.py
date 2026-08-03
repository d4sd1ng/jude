"""Kurzbriefing für Sprach- und GUI-Ausgabe: Märkte und Schlagzeilen.

Märkte (Gold, Bitcoin) kommen aus dem MarketService. Schlagzeilen zu Tech
(Musk, Nvidia/Jensen Huang, AMD) und Kriegslage stammen aus Google-News-RSS
(kostenlos, ohne Schlüssel); liegt ein NewsAPI-Schlüssel vor, wird dieser
bevorzugt. Ergebnisse werden kurz gecacht, damit wiederholtes Aufwachen die
Quellen nicht überlastet.
"""

from __future__ import annotations

import os
import time
from urllib.parse import quote_plus

import requests
from defusedxml import ElementTree

from services.market import MarketService

DEFAULT_TOPICS = {
    "Tech / Elon Musk": "Elon Musk OR Tesla OR SpaceX",
    "Nvidia / Jensen Huang": "Nvidia Jensen Huang",
    "AMD": "AMD Ryzen OR AMD Radeon OR Lisa Su",
    "Kriegslage": "Krieg OR Ukraine OR Nahost OR Gaza",
}


class BriefingService:
    CACHE_SECONDS = 300

    def __init__(self, market: MarketService | None = None):
        self.market = market or MarketService()
        self._cache: dict | None = None
        self._cached_at = 0.0

    # --------------------------------------------------------------- Märkte

    def _market_line(self, name: str, label: str, unit: str) -> dict | None:
        try:
            data = self.market.fetch(name, interval="1h", limit=30)
            candles = data.get("candles", [])
            if not candles:
                return None
            last = float(candles[-1]["close"])
            past = float(candles[max(0, len(candles) - 25)]["close"])
            change = (last - past) / past * 100 if past else 0.0
            return {"label": label, "price": round(last, 2), "unit": unit,
                    "change_pct": round(change, 2), "direction": "gestiegen" if change >= 0 else "gefallen"}
        except Exception:
            return None

    def markets(self) -> list[dict]:
        result = []
        for name, label, unit in (("XAU/USD", "Gold", "Dollar"), ("BTC/USD", "Bitcoin", "Dollar")):
            line = self._market_line(name, label, unit)
            if line:
                result.append(line)
        return result

    # ---------------------------------------------------------- Schlagzeilen

    @staticmethod
    def _google_news(query: str, limit: int) -> list[str]:
        url = (f"https://news.google.com/rss/search?q={quote_plus(query)}"
               "&hl=de&gl=DE&ceid=DE:de")
        response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 Jude"})
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        titles = []
        for item in root.iter("item"):
            title = item.findtext("title")
            if title:
                titles.append(title.split(" - ")[0].strip())
            if len(titles) >= limit:
                break
        return titles

    @staticmethod
    def _newsapi(query: str, limit: int) -> list[str]:
        key = os.getenv("NEWS_API_KEY")
        if not key:
            return []
        response = requests.get("https://newsapi.org/v2/everything", headers={"X-Api-Key": key},
                                params={"q": query, "language": "de", "sortBy": "publishedAt",
                                        "pageSize": max(1, min(limit, 10))}, timeout=15)
        response.raise_for_status()
        return [a["title"] for a in response.json().get("articles", []) if a.get("title")][:limit]

    def _topic_headlines(self, query: str, limit: int) -> list[str]:
        try:
            headlines = self._newsapi(query, limit)
            if headlines:
                return headlines
        except Exception:
            pass
        try:
            return self._google_news(query, limit)
        except Exception:
            return []

    def headlines(self, per_topic: int = 1) -> dict[str, list[str]]:
        topics = DEFAULT_TOPICS
        return {label: self._topic_headlines(query, per_topic) for label, query in topics.items()}

    # ---------------------------------------------------------------- Brief

    def data(self) -> dict:
        now = time.monotonic()
        if self._cache is not None and now - self._cached_at < self.CACHE_SECONDS:
            return self._cache
        self._cache = {"markets": self.markets(), "headlines": self.headlines()}
        self._cached_at = now
        return self._cache

    def spoken_brief(self) -> str:
        data = self.data()
        parts: list[str] = []
        market_bits = []
        for m in data["markets"]:
            market_bits.append(f"{m['label']} bei {m['price']:.0f} {m['unit']}, "
                               f"{m['direction']} um {abs(m['change_pct']):.1f} Prozent")
        if market_bits:
            parts.append("Märkte: " + "; ".join(market_bits) + ".")
        for label, items in data["headlines"].items():
            if items:
                parts.append(f"{label}: {items[0]}.")
        if not parts:
            return "Für das Briefing liegen gerade keine Daten vor."
        return "Kurzbriefing. " + " ".join(parts)
