from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone

import requests

from services.database import connection

MARKETS = {
    "BTC/EUR": {"provider": "binance", "symbol": "BTCEUR", "caveat": "Binance Spot"},
    "BTC/USD": {"provider": "binance", "symbol": "BTCUSDT", "caveat": "Binance BTC/USDT als USD-Näherung"},
    "XAU/USD": {"provider": "yahoo", "symbol": "GC=F", "caveat": "Yahoo Gold-Futures, kein XAU-Spotpreis"},
    "XAU/EUR": {"provider": "yahoo_derived", "symbol": "GC=F/EURUSD=X", "caveat": "Gold-Futures durch EUR/USD, abgeleiteter Proxy"},
}
YAHOO_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d"}


class MarketService:
    def fetch(self, market: str, interval: str = "1h", limit: int = 500) -> dict:
        if market not in MARKETS:
            raise ValueError(f"Unbekannter Markt: {market}")
        spec = MARKETS[market]
        if spec["provider"] == "binance":
            candles = self._fetch_binance(spec["symbol"], interval, limit)
            source = "binance"
        else:
            candles = self._fetch_yahoo(market, interval, limit)
            source = "yahoo"
        self._validate_candles(candles)
        self._store(source, market, interval, candles, spec["caveat"])
        return {"market": market, "interval": interval, "source": source, "caveat": spec["caveat"],
                "count": len(candles), "candles": candles, "updated_at": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def _validate_candles(candles: list[dict]) -> None:
        for candle in candles:
            values = [candle.get(key) for key in ("open", "high", "low", "close", "volume")]
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
                raise ValueError("Datenquelle lieferte eine unvollständige oder nicht endliche OHLCV-Kerze.")
            if candle["low"] > min(candle["open"], candle["close"]) or candle["high"] < max(candle["open"], candle["close"]):
                raise ValueError("Datenquelle lieferte eine ungültige OHLC-Kerze.")
            if candle["volume"] < 0:
                raise ValueError("Datenquelle lieferte negatives Volumen.")

    @staticmethod
    def _fetch_binance(symbol: str, interval: str, limit: int) -> list[dict]:
        if interval not in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}:
            raise ValueError("Nicht unterstütztes Binance-Intervall.")
        response = requests.get("https://api.binance.com/api/v3/klines",
                                params={"symbol": symbol, "interval": interval, "limit": max(1, min(limit, 1000))}, timeout=20)
        response.raise_for_status()
        return [{"time": int(row[0] // 1000), "open": float(row[1]), "high": float(row[2]),
                 "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])} for row in response.json()]

    @staticmethod
    def _fetch_yahoo(market: str, interval: str, limit: int) -> list[dict]:
        import yfinance as yf
        yf_interval = YAHOO_INTERVALS.get(interval)
        if yf_interval is None:
            raise ValueError("Yahoo unterstützt dieses Intervall nicht.")
        period = "7d" if yf_interval in {"1m", "5m", "15m"} else "60d" if yf_interval == "1h" else "5y"
        tickers = ["GC=F"] if market == "XAU/USD" else ["GC=F", "EURUSD=X"]
        data = yf.download(tickers, period=period, interval=yf_interval, auto_adjust=False,
                           progress=False, threads=False, timeout=20, group_by="ticker")
        if data.empty:
            raise RuntimeError("Yahoo lieferte keine Gold-Daten.")
        rows: list[dict] = []
        for timestamp, row in data.tail(max(limit * 5, limit)).iterrows():
            if market == "XAU/USD":
                values = row["GC=F"] if getattr(data.columns, "nlevels", 1) > 1 else row
                open_price, high_price, low_price, close_price, volume = (
                    values.get("Open"), values.get("High"), values.get("Low"), values.get("Close"), values.get("Volume", 0))
            else:
                gold, fx = row["GC=F"], row["EURUSD=X"]
                if any(value != value for value in [gold.get("Open"), gold.get("High"), gold.get("Low"), gold.get("Close"), fx.get("Open"), fx.get("High"), fx.get("Low"), fx.get("Close")]):
                    continue
                open_price = gold["Open"] / fx["Open"]
                high_price = gold["High"] / fx["Low"]
                low_price = gold["Low"] / fx["High"]
                close_price = gold["Close"] / fx["Close"]
                volume = gold.get("Volume", 0)
            if any(value != value for value in [open_price, high_price, low_price, close_price]):
                continue
            rows.append({"time": int(timestamp.timestamp()), "open": float(open_price), "high": float(high_price),
                         "low": float(low_price), "close": float(close_price), "volume": float(volume or 0)})
        if interval == "4h":
            rows = MarketService._aggregate_four_hour(rows)
        return rows[-limit:]

    @staticmethod
    def _aggregate_four_hour(rows: list[dict]) -> list[dict]:
        groups: dict[int, list[dict]] = {}
        for row in rows:
            bucket = row["time"] - row["time"] % 14400
            groups.setdefault(bucket, []).append(row)
        return [{"time": key, "open": values[0]["open"], "high": max(v["high"] for v in values),
                 "low": min(v["low"] for v in values), "close": values[-1]["close"],
                 "volume": sum(v["volume"] for v in values)} for key, values in sorted(groups.items())]

    @staticmethod
    def _store(source: str, market: str, interval: str, candles: list[dict], caveat: str) -> None:
        fetched = datetime.now(timezone.utc).isoformat()
        with connection() as db:
            db.executemany("""
                INSERT INTO candles(source,symbol,interval,open_time,open,high,low,close,volume,fetched_at,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,symbol,interval,open_time) DO UPDATE SET
                open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                volume=excluded.volume,fetched_at=excluded.fetched_at,metadata_json=excluded.metadata_json
            """, [(source, market, interval, c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"], fetched,
                    json.dumps({"caveat": caveat}, ensure_ascii=False)) for c in candles])

    def history(self, market: str, interval: str = "1h", limit: int = 500) -> dict:
        with connection() as db:
            rows = db.execute("""SELECT source,open_time AS time,open,high,low,close,volume,fetched_at,metadata_json
                FROM candles WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?""",
                              (market, interval, max(1, min(limit, 5000)))).fetchall()
        candles = [dict(row) for row in reversed(rows)]
        caveat = json.loads(candles[-1].pop("metadata_json"))["caveat"] if candles else MARKETS.get(market, {}).get("caveat", "")
        for candle in candles[:-1]:
            candle.pop("metadata_json", None)
        return {"market": market, "interval": interval, "caveat": caveat, "candles": candles,
                "status": "cached" if candles else "empty"}

    def csv_export(self, market: str, interval: str = "1h") -> str:
        rows = self.history(market, interval, 5000)["candles"]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["time", "open", "high", "low", "close", "volume", "source", "fetched_at"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
