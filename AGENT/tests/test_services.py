from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from services.database import connection
from services.filesystem import resolve_path
from services.ict import ICTService
from services.ict_training import FEATURE_NAMES, features_from_frames
from services.market import MarketService
from services.memory import MemoryService
from services.scraper import ScraperService


def test_filesystem_write_scope():
    from core.paths import AI_DATA_ROOT
    assert str(resolve_path(AI_DATA_ROOT / "test.txt", for_write=True)).endswith("test.txt")
    with pytest.raises(PermissionError):
        resolve_path("/tmp/not-allowed.txt", for_write=True)
    with pytest.raises(PermissionError):
        resolve_path(AI_DATA_ROOT / ".Trash-1000" / "item")


def test_kill_zones_and_cross_midnight():
    service = ICTService(client=Mock())
    ny = ZoneInfo("America/New_York")
    assert [z["name"] for z in service.due_zones(datetime(2026, 7, 23, 21, 0, tzinfo=ny))] == ["Asian Range"]
    assert [z["name"] for z in service.due_zones(datetime(2026, 7, 24, 0, 30, tzinfo=ny))] == []
    assert {z["name"] for z in service.due_zones(datetime(2026, 7, 23, 10, 30, tzinfo=ny))} == {"New York Open", "London Close"}


def test_ny_marburg_dst_difference_is_not_hardcoded():
    winter = ICTService.scheduler_config(datetime(2026, 1, 15, tzinfo=ZoneInfo("UTC")))
    transition = ICTService.scheduler_config(datetime(2026, 3, 15, tzinfo=ZoneInfo("UTC")))
    w_start = datetime.fromisoformat(next(x for x in winter["local_windows"] if x["name"] == "New York Open")["marburg_start"])
    w_ny = datetime.fromisoformat(next(x for x in winter["local_windows"] if x["name"] == "New York Open")["new_york_start"])
    t_start = datetime.fromisoformat(next(x for x in transition["local_windows"] if x["name"] == "New York Open")["marburg_start"])
    t_ny = datetime.fromisoformat(next(x for x in transition["local_windows"] if x["name"] == "New York Open")["new_york_start"])
    assert (w_start.utcoffset() - w_ny.utcoffset()).total_seconds() == 6 * 3600
    assert (t_start.utcoffset() - t_ny.utcoffset()).total_seconds() == 5 * 3600


def test_market_binance_mapping():
    response = Mock()
    response.json.return_value = [[1000, "1", "2", "0.5", "1.5", "7"]]
    response.raise_for_status.return_value = None
    with patch("services.market.requests.get", return_value=response) as get:
        rows = MarketService._fetch_binance("BTCEUR", "1h", 20)
    assert rows == [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 7.0}]
    assert get.call_args.kwargs["params"]["limit"] == 20


def test_market_rejects_invalid_ohlc():
    with pytest.raises(ValueError):
        MarketService._validate_candles([{"time": 1, "open": 10.0, "high": 9.0, "low": 8.0,
                                          "close": 9.0, "volume": 1.0}])


def test_scraper_blocks_local_network(monkeypatch):
    monkeypatch.setattr("services.scraper.socket.getaddrinfo", lambda *_: [(None, None, None, None, ("127.0.0.1", 80))])
    with pytest.raises(PermissionError):
        ScraperService._validate_url("http://example.test/")


def test_memory_promotes_repeated_candidate_and_honors_forget():
    service = MemoryService()
    content = "Ich bevorzuge Testfarbe Violett 8f6c1a"
    first = service.remember(content, kind="personal", status="candidate", source="test", confidence=0.65)
    second = service.remember(content, kind="personal", status="candidate", source="test", confidence=0.65)
    assert first["status"] == "candidate" and second["status"] == "active"
    assert service.delete_id(second["id"])["status"] == "forgotten"
    assert service.remember(content, kind="personal", status="candidate", source="test", confidence=0.65)["status"] == "blocked"
    with connection() as db:
        db.execute("DELETE FROM memory_blocks WHERE fingerprint=?", (service._fingerprint(content),))


def test_ict_feature_vector_combines_h4_h1_m1():
    def candles(count: int, step_seconds: int):
        base = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))
        return [{"time": (base + timedelta(seconds=i * step_seconds)).isoformat(),
                 "open": 100 + i * 0.1, "high": 101 + i * 0.1, "low": 99 + i * 0.1,
                 "close": 100.4 + i * 0.1} for i in range(count)]
    values = features_from_frames(candles(30, 14_400), candles(40, 3_600), candles(60, 60))
    assert values.shape == (len(FEATURE_NAMES),)
    assert values[-1] == 1.0
