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

    @staticmethod
    def _sma(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    @staticmethod
    def _pct(values: list[float], bars_back: int) -> float:
        if len(values) < 2:
            return 0.0
        base = values[-1 - min(bars_back, len(values) - 1)]
        return (values[-1] - base) / base * 100 if base else 0.0

    def _analyze(self, name: str, label: str, unit: str) -> dict | None:
        try:
            data = self.market.fetch(name, interval="1h", limit=200)
        except Exception:
            return None
        candles = data.get("candles", [])
        if len(candles) < 24:
            return None
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        last = closes[-1]

        change_24h = self._pct(closes, 24)
        change_7d = self._pct(closes, 168)
        window = candles[-24:]
        day_high = max(float(c["high"]) for c in window)
        day_low = min(float(c["low"]) for c in window)
        span = day_high - day_low
        range_pos = (last - day_low) / span * 100 if span else 50.0

        sma20, sma50 = self._sma(closes, 20), self._sma(closes, 50)
        if sma20 and sma50 and last > sma20 > sma50:
            trend = "aufwärts"
        elif sma20 and sma50 and last < sma20 < sma50:
            trend = "abwärts"
        else:
            trend = "seitwärts"
        momentum = (last - sma20) / sma20 * 100 if sma20 else 0.0

        lookback = min(72, len(candles))
        resistance = max(highs[-lookback:])
        support = min(lows[-lookback:])
        # Volatilität als mittlere Stundenspanne der letzten 24h in Prozent.
        volatility = sum((float(c["high"]) - float(c["low"])) / float(c["close"]) for c in window) / len(window) * 100

        return {
            "label": label, "unit": unit, "price": round(last, 2),
            "change_24h": round(change_24h, 2), "change_7d": round(change_7d, 2),
            "direction": "gestiegen" if change_24h >= 0 else "gefallen",
            "day_high": round(day_high, 2), "day_low": round(day_low, 2),
            "range_position_pct": round(range_pos, 0),
            "trend": trend, "momentum_pct": round(momentum, 2),
            "support": round(support, 2), "resistance": round(resistance, 2),
            "volatility_pct": round(volatility, 2), "source": data.get("source"),
        }

    def markets(self) -> list[dict]:
        result = []
        for name, label, unit in (("XAU/USD", "Gold", "Dollar"), ("BTC/USD", "Bitcoin", "Dollar")):
            line = self._analyze(name, label, unit)
            if line:
                result.append(line)
        return result

    @staticmethod
    def _zone(range_pos: float) -> str:
        if range_pos >= 66:
            return "im oberen Bereich der Tagesspanne"
        if range_pos <= 33:
            return "im unteren Bereich der Tagesspanne"
        return "in der Mitte der Tagesspanne"

    def _market_sentence(self, m: dict) -> str:
        price_fmt = f"{m['price']:,.0f}".replace(",", ".")
        support_fmt = f"{m['support']:,.0f}".replace(",", ".")
        resist_fmt = f"{m['resistance']:,.0f}".replace(",", ".")
        return (
            f"{m['label']} steht bei {price_fmt} {m['unit']}, {m['direction']} um "
            f"{abs(m['change_24h']):.1f} Prozent auf Tagessicht und {m['change_7d']:+.1f} Prozent auf Wochensicht. "
            f"Der kurzfristige Trend ist {m['trend']}, {self._zone(m['range_position_pct'])}. "
            f"Unterstützung bei {support_fmt}, Widerstand bei {resist_fmt}."
        )

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
        for m in data["markets"]:
            parts.append(self._market_sentence(m))
        for label, items in data["headlines"].items():
            if items:
                parts.append(f"{label}: {items[0]}.")
        if not parts:
            return "Für das Briefing liegen gerade keine Daten vor."
        return "Kurzbriefing. " + " ".join(parts)
