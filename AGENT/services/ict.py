from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.database import connection
from services.ict_training import ICTTrainingService
from services.notifications import NotificationService

ICT_PROMPT = Path("/media/d4sd1ng/AI-Data/Projects/ICT_SNIPER/ict_trading_bot_systemprompt_1.txt")


class MT5MCPClient:
    """Kurzlebiger stdio-MCP-Client; jede Sitzung ist lesend, sofern kein Order-Tool aufgerufen wird."""

    def __init__(self, command: str | None = None):
        value = command or os.getenv(
            "MT5_MCP_COMMAND",
            "/home/d4sd1ng/trading/venv/bin/python /home/d4sd1ng/trading/mt5_mcp.py",
        )
        parts = shlex.split(value)
        self.command, self.args = parts[0], parts[1:]

    async def _call_async(self, name: str, arguments: dict) -> object:
        return (await self._call_many_async([(name, arguments)]))[0]

    async def _call_many_async(self, calls: list[tuple[str, dict]]) -> list[object]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.command, args=self.args)
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                results = [await session.call_tool(name, arguments) for name, arguments in calls]
        parsed = []
        for result in results:
            if getattr(result, "isError", False):
                raise RuntimeError("MT5-MCP meldete einen Fehler")
            text = "".join(getattr(block, "text", "") for block in result.content)
            data = json.loads(text)
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"]))
            parsed.append(data)
        return parsed

    def call(self, name: str, arguments: dict | None = None) -> object:
        return asyncio.run(self._call_async(name, arguments or {}))

    def call_many(self, calls: list[tuple[str, dict]]) -> list[object]:
        return asyncio.run(self._call_many_async(calls))


class ICTService:
    ALLOWED_SYMBOLS = {"XAUUSD", "BTCUSD"}
    DEFAULT_KILL_ZONES = [
        {"name": "Asian Range", "start": "20:00", "end": "00:00"},
        {"name": "London Open", "start": "02:00", "end": "05:00"},
        {"name": "New York Open", "start": "08:30", "end": "11:00"},
        {"name": "London Close", "start": "10:00", "end": "12:00"},
    ]

    def __init__(self, client: MT5MCPClient | None = None):
        self.client = client or MT5MCPClient()
        self.training = ICTTrainingService()

    def prompt(self) -> str:
        return ICT_PROMPT.read_text(encoding="utf-8")

    def stack_status(self, probe: bool = True) -> dict:
        statuses = {}
        for service in ("mt5-terminal", "mt5-bridge"):
            process = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
            statuses[service] = process.stdout.strip() or process.stderr.strip()
        statuses["mt5-mcp"] = "on-demand stdio"
        if probe:
            try:
                self.client.call("get_account_info")
                connection_status = {"ready": True, "error": ""}
            except Exception as exc:
                connection_status = {"ready": False, "error": str(exc)}
        else:
            connection_status = {"ready": None, "error": "nicht geprüft; Detailstatus über /api/ict/status"}
        return {"services": statuses, "connection": connection_status, "mode": "DEMO only", "timeframes": ["H4", "H1", "M1"],
                "scheduler": self.scheduler_config(), "training": self.training.status()}

    @staticmethod
    def scheduler_config(reference: datetime | None = None) -> dict:
        """ICT_KILL_ZONES JSON: [{"name":"...","start":"HH:MM","end":"HH:MM"}]."""
        raw = os.getenv("ICT_KILL_ZONES", "").strip()
        zones = json.loads(raw) if raw else ICTService.DEFAULT_KILL_ZONES
        for zone in zones:
            if set(zone) < {"name", "start", "end"}:
                raise ValueError("Jede Kill Zone braucht name, start und end.")
            datetime.strptime(zone["start"], "%H:%M")
            datetime.strptime(zone["end"], "%H:%M")
        timezone_name = os.getenv("ICT_TIMEZONE", "America/New_York")
        local_timezone = os.getenv("ICT_LOCAL_TIMEZONE", "Europe/Berlin")
        today = (reference.astimezone(ZoneInfo(timezone_name)) if reference else datetime.now(ZoneInfo(timezone_name))).date()
        local_windows = []
        for zone in zones:
            start = datetime.combine(today, datetime.strptime(zone["start"], "%H:%M").time(), ZoneInfo(timezone_name))
            end = datetime.combine(today, datetime.strptime(zone["end"], "%H:%M").time(), ZoneInfo(timezone_name))
            if end <= start:
                end += timedelta(days=1)
            local_windows.append({"name": zone["name"], "new_york_start": start.isoformat(), "new_york_end": end.isoformat(),
                                  "marburg_start": start.astimezone(ZoneInfo(local_timezone)).isoformat(),
                                  "marburg_end": end.astimezone(ZoneInfo(local_timezone)).isoformat()})
        return {"enabled": os.getenv("ICT_SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
                "timezone": timezone_name, "local_timezone": local_timezone, "zones": zones, "local_windows": local_windows,
                "source_note": "ICT-Zeiten in New-York-Ortszeit; DST wird per IANA-Zeitzone umgerechnet."}

    def load_snapshot(self, symbol: str) -> dict:
        if symbol not in self.ALLOWED_SYMBOLS:
            raise ValueError("ICT/SMC unterstützt nur XAUUSD und BTCUSD.")
        values = self.client.call_many([
            ("get_account_info", {}), ("get_positions", {"symbol": symbol}),
            ("get_deals_history", {"days": 1}), ("get_symbol_tick", {"symbol": symbol}),
            ("get_ohlcv", {"symbol": symbol, "timeframe": "H4", "count": 100}),
            ("get_ohlcv", {"symbol": symbol, "timeframe": "H1", "count": 160}),
            ("get_ohlcv", {"symbol": symbol, "timeframe": "M1", "count": 200}),
        ])
        return {
            "symbol": symbol,
            "account": values[0], "positions": values[1], "deals_today": values[2], "tick": values[3],
            "h4": values[4], "h1": values[5], "m1": values[6],
        }

    def analyse(self, router, snapshot: dict, kill_zone: str = "manuell") -> dict:
        symbol = snapshot.get("symbol")
        if symbol not in self.ALLOWED_SYMBOLS:
            raise ValueError("ICT/SMC unterstützt nur XAUUSD und BTCUSD.")
        training_gate = self.training.score(symbol, snapshot["h4"], snapshot["h1"], snapshot["m1"])
        request = f"""{self.prompt()}
VERBINDLICHE ERGÄNZUNG:
- H4 bestimmt Bias und Ziel.
- H1 grenzt POI, Struktur und Liquiditätsbereich ein.
- M1 bestimmt ausschließlich den Entry-Trigger.
- H4, H1 und M1 sind als eine Einheit zu bewerten. Widerspruch oder fehlende Daten bedeuten Kein Trade.
- Nur analysieren, niemals order_send ausführen.
- Das historische Trainings-Gate ist zusätzlich verbindlich. Ohne ready=true und passed=true ist SETUP_FOUND verboten.
Antworte ausschließlich als JSON mit: status, symbol, direction, h4_bias, h1_context,
m1_entry, confluence, entry, sl, tp, rr, lot, blockers, reasoning, data_timestamp.
status ist nur SETUP_FOUND, WAITING_FOR_SETUP oder TRADE_BLOCKED.

Kill Zone: {kill_zone}
Historisches Trainings-Gate: {json.dumps(training_gate, ensure_ascii=False)}
Snapshot: {json.dumps(snapshot, ensure_ascii=False)}"""
        response = router.call_with_fallback([{"role": "user", "content": request}])
        text = str(response.get("content", ""))
        card = json.loads(text[text.find("{"):text.rfind("}") + 1])
        card.update({"id": uuid.uuid4().hex[:16], "symbol": symbol, "kill_zone": kill_zone,
                     "created_at": datetime.now(ZoneInfo("UTC")).isoformat(), "training_gate": training_gate})
        if card.get("status") == "SETUP_FOUND" and (not training_gate.get("ready") or not training_gate.get("passed")):
            blockers = list(card.get("blockers") or [])
            blockers.append(training_gate.get("reason") or "Walk-forward-Trainingsschwelle nicht erreicht.")
            card["blockers"] = blockers
            card["status"] = "TRADE_BLOCKED"
        self._validate_card(card)
        with connection() as db:
            db.execute("INSERT INTO trading_cards(id,symbol,status,kill_zone,created_at,card_json) VALUES(?,?,?,?,?,?)",
                       (card["id"], symbol, card["status"], kill_zone, card["created_at"], json.dumps(card, ensure_ascii=False)))
        if card["status"] == "SETUP_FOUND":
            NotificationService.create("ict_setup", f"ICT/SMC Setup {symbol}",
                                       f"{card.get('direction', '')} · {kill_zone} · RR {card.get('rr', '-')}", {"card_id": card["id"], "symbol": symbol})
        return card

    @staticmethod
    def _validate_card(card: dict) -> None:
        required = {"status", "h4_bias", "h1_context", "m1_entry", "confluence", "blockers"}
        missing = sorted(required - set(card))
        if missing:
            raise ValueError("Unvollständige Trading Card: " + ", ".join(missing))
        if card["status"] not in {"SETUP_FOUND", "WAITING_FOR_SETUP", "TRADE_BLOCKED"}:
            raise ValueError("Ungültiger Trading-Card-Status.")
        if card["status"] == "SETUP_FOUND":
            for key in ("entry", "sl", "tp", "rr", "lot", "direction"):
                if card.get(key) is None or card.get(key) == "":
                    raise ValueError(f"SETUP_FOUND ohne {key}.")
            rr = float(card["rr"])
            if not 1.8 <= rr <= 4.0:
                raise ValueError("SETUP_FOUND mit RR außerhalb 1,8 bis 4,0.")
            if card.get("blockers"):
                raise ValueError("SETUP_FOUND darf keine Blocker enthalten.")

    def analyse_live(self, router, symbol: str, kill_zone: str = "manuell") -> dict:
        return self.analyse(router, self.load_snapshot(symbol), kill_zone)

    def train_live(self, symbol: str, count: int = 120_000) -> dict:
        if symbol not in self.ALLOWED_SYMBOLS:
            raise ValueError("ICT/SMC unterstützt nur XAUUSD und BTCUSD.")
        rows = self.client.call("get_ohlcv", {"symbol": symbol, "timeframe": "M1", "count": count})
        return self.training.train(symbol, rows)

    def due_zones(self, moment: datetime | None = None) -> list[dict]:
        config = self.scheduler_config()
        if not config["enabled"]:
            return []
        now = moment.astimezone(ZoneInfo(config["timezone"])) if moment else datetime.now(ZoneInfo(config["timezone"]))
        current = now.strftime("%H:%M")
        active = []
        for zone in config["zones"]:
            start, end = zone["start"], zone["end"]
            inside = start <= current < end if start < end else current >= start or current < end
            if inside:
                active.append(zone)
        return active

    def run_due(self, router, moment: datetime | None = None) -> list[dict]:
        zones = self.due_zones(moment)
        if not zones:
            return []
        now = moment.astimezone(ZoneInfo(self.scheduler_config()["timezone"])) if moment else datetime.now(ZoneInfo(self.scheduler_config()["timezone"]))
        bucket = now.strftime("%Y%m%d%H%M")
        results = []
        zone_name = " + ".join(zone["name"] for zone in zones)
        for symbol in sorted(self.ALLOWED_SYMBOLS):
            job_key = f"{zone_name}:{symbol}:{bucket}"
            with connection() as db:
                if db.execute("SELECT 1 FROM scheduler_runs WHERE job_key=?", (job_key,)).fetchone():
                    continue
            try:
                card = self.analyse_live(router, symbol, zone_name)
                result = card["status"]
                results.append(card)
            except Exception as exc:
                result = f"error: {exc}"
            with connection() as db:
                db.execute("INSERT INTO scheduler_runs(job_key,ran_at,result) VALUES(?,?,?)",
                           (job_key, now.isoformat(), result))
        return results

    @staticmethod
    def cards(limit: int = 50) -> list[dict]:
        with connection() as db:
            rows = db.execute("SELECT card_json FROM trading_cards ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["card_json"]) for row in rows]
