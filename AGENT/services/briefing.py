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

TECH_TOPICS = {
    "Elon Musk": "Elon Musk OR Tesla OR SpaceX OR xAI",
    "Nvidia / Jensen Huang": "Nvidia Jensen Huang",
    "AMD": "AMD Ryzen OR AMD Radeon OR Lisa Su",
}
WAR_TOPICS = {
    "Kriegslage": "Krieg OR Ukraine OR Nahost OR Gaza OR Taiwan",
}
DEFAULT_TOPICS = {**TECH_TOPICS, **WAR_TOPICS}


class BriefingService:
    CACHE_SECONDS = 300

    SYMBOL_LABELS = {"XAUUSD": "Gold", "BTCUSD": "Bitcoin"}
    STATUS_LABELS = {"SETUP_FOUND": "Setup gefunden", "WAITING_FOR_SETUP": "wartet auf Setup",
                     "TRADE_BLOCKED": "Trade blockiert"}

    def __init__(self, market: MarketService | None = None, ict=None, router=None):
        self.market = market or MarketService()
        self.ict = ict
        self.router = router
        self.ict_live = os.getenv("JUDE_BRIEFING_ICT_LIVE", "").strip().lower() in {"1", "true", "an", "on", "ja"}
        self._cache: dict | None = None
        self._cached_at = 0.0

    # ------------------------------------------------------------ ICT/SMC

    @staticmethod
    def _short(value, limit: int = 160) -> str:
        text = value if isinstance(value, str) else str(value)
        return text[:limit].strip()

    def _latest_card(self, symbol: str) -> dict | None:
        if not self.ict:
            return None
        try:
            if self.ict_live and self.router is not None:
                return self.ict.analyse_live(self.router, symbol)
            for card in self.ict.cards(limit=50):
                if card.get("symbol") == symbol:
                    return card
        except Exception:
            return None
        return None

    def ict_readouts(self) -> list[dict]:
        readouts = []
        for symbol, label in self.SYMBOL_LABELS.items():
            card = self._latest_card(symbol)
            if not card:
                continue
            readouts.append({
                "label": label, "symbol": symbol,
                "status": card.get("status"),
                "status_label": self.STATUS_LABELS.get(card.get("status"), card.get("status") or "unbekannt"),
                "direction": card.get("direction"),
                "h4_bias": self._short(card.get("h4_bias", "")),
                "h1_context": self._short(card.get("h1_context", "")),
                "m1_entry": self._short(card.get("m1_entry", "")),
                "confluence": self._short(card.get("confluence", "")),
                "kill_zone": card.get("kill_zone"),
                "rr": card.get("rr"), "entry": card.get("entry"),
                "created_at": card.get("created_at"),
            })
        return readouts

    def _ict_sentence(self, r: dict) -> str:
        bits = [f"ICT-Analyse {r['label']}: H4-Bias {r['h4_bias'] or 'unklar'}, {r['status_label']}"]
        if r.get("kill_zone") and r["kill_zone"] != "manuell":
            bits.append(f"Kill Zone {r['kill_zone']}")
        if r["status"] == "SETUP_FOUND" and r.get("direction"):
            bits.append(f"{r['direction']} mit Chance-Risiko-Verhältnis {r.get('rr', '-')}")
        elif r.get("confluence"):
            bits.append(f"Konfluenz: {r['confluence']}")
        return ". ".join(bits) + "."

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

    def _topic_headlines(self, query: str, limit: int) -> list[str]:
        # Google-News-RSS ist kostenlos und unlimitiert – richtig für den
        # 10-Minuten-Ticker. Die frühere newsapi.org-Stufe lief mit dem
        # newsdata.io-Schlüssel ins Leere (401 bei jedem Poll) und ist raus.
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
        self._cache = {"markets": self.markets(), "ict": self.ict_readouts(), "headlines": self.headlines()}
        self._cached_at = now
        return self._cache

    def segments(self, full: bool = True) -> list[dict]:
        """Briefing in überspringbare Themenblöcke.

        ``full=True`` liefert XAU & BTC, Tech und Kriegslage (einmal täglich);
        ``full=False`` nur das Markt-Update zu XAU & BTC für erneutes Aufwachen.
        """
        data = self.data()
        segments: list[dict] = []

        # Schreibtisch zuerst: offene Abnahmen und überfällige Aufträge –
        # bewusst außerhalb des data()-Caches, die Zahl muss frisch sein.
        try:
            from services.review import ReviewQueue
            from services.auftraege import Auftragsbuch
            from services.confirmations import ConfirmationQueue
            zus = ReviewQueue().zusammenfassung()
            offen = int(zus.get("offen") or 0)
            ueber = len(Auftragsbuch().ueberfaellig())
            # Wartende Bestätigungen gehören ebenfalls auf den Schreibtisch –
            # die Schleuse hielt sonst stumm Schreibvorgänge fest (16.08.).
            best = len(ConfirmationQueue().list("pending"))
            if offen or ueber or best:
                teile = []
                if offen:
                    teile.append(f"{offen} Vorlage{'n' if offen != 1 else ''} zur Abnahme")
                if ueber:
                    teile.append(f"{ueber} überfällige Aufträge" if ueber != 1
                                 else "1 überfälliger Auftrag")
                if best:
                    teile.append(f"{best} Freigabe{'n' if best != 1 else ''} in der Schleuse")
                segments.insert(0, {"topic": "Schreibtisch",
                                    "text": "Auf deinem Schreibtisch: " + " und ".join(teile) + "."})
        except Exception:
            pass

        market_parts = [self._market_sentence(m) for m in data.get("markets", [])]
        market_parts += [self._ict_sentence(r) for r in data.get("ict", [])]
        if market_parts:
            segments.append({"topic": "XAU & BTC", "text": " ".join(market_parts)})

        if not full:
            return segments

        headlines = data.get("headlines", {})
        tech = [f"{label}: {items[0]}" for label, items in headlines.items()
                if label in TECH_TOPICS and items]
        if tech:
            segments.append({"topic": "Tech", "text": "Tech-News. " + " ".join(f"{t}." for t in tech)})

        war = [items[0] for label, items in headlines.items() if label in WAR_TOPICS and items]
        if war:
            segments.append({"topic": "Kriegslage", "text": "Kriegslage. " + " ".join(f"{w}." for w in war)})

        return segments

    def spoken_brief(self) -> str:
        segments = self.segments()
        if not segments:
            return "Für das Briefing liegen gerade keine Daten vor."
        return "Kurzbriefing. " + " ".join(s["text"] for s in segments)
