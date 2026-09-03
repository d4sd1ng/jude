from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from services import database


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    from core.paths import TEST_DATA_DIR
    root = TEST_DATA_DIR
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"test-{uuid.uuid4().hex}.db"
    monkeypatch.setattr(database, "DB_PATH", target)
    yield target
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(target) + suffix)
        if path.exists():
            path.unlink()
