from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from web.app import market

router = APIRouter()


@router.get("/api/market/{market_name:path}")
def market_data(market_name: str, interval: str = "1h", limit: int = 300, refresh: bool = True):
    return market.fetch(market_name, interval, limit) if refresh else market.history(market_name, interval, limit)


@router.get("/api/market/{market_name:path}/csv", response_class=PlainTextResponse)
def market_csv(market_name: str, interval: str = "1h"):
    return PlainTextResponse(market.csv_export(market_name, interval), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{market_name.replace("/", "-")}_{interval}.csv"'})
