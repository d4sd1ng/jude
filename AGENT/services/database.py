from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DATA_DIR = Path(os.getenv("JUDE_DATA_DIR", "/media/d4sd1ng/AI-Data/Jude/data"))
DB_PATH = DATA_DIR / "jude.db"


def initialize_database(path: Path | None = None) -> None:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS candles (
            source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
            open_time INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
            low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (source, symbol, interval, open_time)
        );
        CREATE TABLE IF NOT EXISTS confirmations (
            id TEXT PRIMARY KEY, action_type TEXT NOT NULL, summary TEXT NOT NULL,
            payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, decided_at TEXT, result TEXT
        );
        CREATE TABLE IF NOT EXISTS meal_plans (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, days INTEGER NOT NULL,
            people INTEGER NOT NULL, plan_json TEXT NOT NULL, pdf_path TEXT
        );
        CREATE TABLE IF NOT EXISTS calendar_events (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trading_cards (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL, status TEXT NOT NULL,
            kill_zone TEXT NOT NULL, created_at TEXT NOT NULL,
            card_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            job_key TEXT PRIMARY KEY, ran_at TEXT NOT NULL, result TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fact_checks (
            id TEXT PRIMARY KEY, source_url TEXT NOT NULL, verdict TEXT NOT NULL,
            created_at TEXT NOT NULL, report_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            message TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, read_at TEXT
        );
        CREATE TABLE IF NOT EXISTS model_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0, reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            service_tier TEXT NOT NULL DEFAULT 'standard', tariff TEXT NOT NULL DEFAULT 'standard',
            cost_multiplier REAL NOT NULL DEFAULT 1.0, request_id TEXT,
            cost_usd REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS route_decisions (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, task_type TEXT NOT NULL,
            complexity INTEGER NOT NULL, selected_model TEXT NOT NULL, final_model TEXT,
            success INTEGER NOT NULL DEFAULT 0, escalated INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0, prompt_hash TEXT NOT NULL,
            attempts_json TEXT NOT NULL DEFAULT '[]', user_feedback INTEGER,
            feedback_at TEXT, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT,
            details_json TEXT NOT NULL DEFAULT '{}', success INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
            model TEXT, training_allowed INTEGER NOT NULL DEFAULT 1,
            exclusion_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS memory_items (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            kind TEXT NOT NULL, content TEXT NOT NULL, normalized TEXT NOT NULL,
            status TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            UNIQUE(normalized, kind)
        );
        CREATE TABLE IF NOT EXISTS memory_blocks (
            fingerprint TEXT PRIMARY KEY, created_at TEXT NOT NULL
        );
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(model_usage)")}
        migrations = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "service_tier": "TEXT NOT NULL DEFAULT 'standard'",
            "tariff": "TEXT NOT NULL DEFAULT 'standard'",
            "cost_multiplier": "REAL NOT NULL DEFAULT 1.0",
            "request_id": "TEXT",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                db.execute(f"ALTER TABLE model_usage ADD COLUMN {name} {declaration}")


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = path or DB_PATH
    initialize_database(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def audit(action: str, target: str | None, details: dict, success: bool, actor: str = "jude") -> None:
    from datetime import datetime, timezone
    with connection() as db:
        db.execute(
            "INSERT INTO audit_log(created_at,actor,action,target,details_json,success) VALUES(?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, action, target, json.dumps(details, ensure_ascii=False), int(success)),
        )
