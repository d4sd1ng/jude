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


@pytest.fixture(autouse=True)
def no_live_scheduler_dispatch():
    """Verhindert, dass ReviewQueue.revision()/ConfirmationQueue.revision()
    waehrend Tests echte Hintergrundarbeit ueber SchedulerService.dispatch_now
    ausloesen (11.09.2026).

    tests/test_completeness.py importiert ``from web.app import app`` -
    dieser Import ruft ``main.build_application()`` auf, die dieselbe
    Produktions-Verdrahtung durchlaeuft wie main.py und dabei
    ``SchedulerService.registrieren()`` aufruft. Das passiert EINMALIG beim
    Modul-Import (Pytest sammelt/importiert alle Testdateien vor dem ersten
    Testlauf), startet dabei echte Daemon-Worker-Threads und registriert
    diese Instanz als globalen ``_aktiv``-Singleton fuer den Rest des
    gesamten Pytest-Prozesses - unabhaengig davon, ob TestClient(app) als
    Context-Manager verwendet wird oder nicht (das steuert nur FastAPIs
    Lifespan/den asyncio-Tick-Task, nicht diese Registrierung).

    Jeder spaetere Test, der revision() aufruft (z.B. tests/test_review.py),
    findet darum ueber SchedulerService.aktuelle() eine ECHTE Instanz und
    reiht einen echten Hintergrundlauf ein. Der isolated_database-Fixture
    oben gibt zwar jedem Test eine eigene DB_PATH - das ist aber ein
    geteiltes Modul-Attribut, kein Thread-lokaler Wert: feuert der
    Hintergrund-Worker mitten in einem SPAETEREN, unabhaengigen Test, sieht
    er dessen 'isolierten' Pfad und races dort gegen den Migrationscheck in
    services/database.py.connection() (unsicheres "pruefen dann ALTER TABLE"
    ohne Sperre) - beobachtet als sporadisches "duplicate column name",
    Rate ca. 1-in-3 bis 1-in-5 vor diesem Fixture, 2-in-10 selbst nach dem
    Umstellen der drei TestClient(app)-Konstruktionen auf 'with'-Bloecke.

    Vor UND nach jedem Test auf None setzen: vor dem Test, falls web.app noch
    nicht importiert wurde (Reihenfolge nicht garantiert) und der Import erst
    WAEHREND dieses Tests passiert; nach dem Test, falls genau dieser Test
    der Import-Ausloeser war.
    """
    from services.scheduler import SchedulerService
    SchedulerService._aktiv = None
    yield
    SchedulerService._aktiv = None
