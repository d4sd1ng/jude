"""Automatische Sicherungen der Jude-Daten.

Sichert die SQLite-Datenbank (konsistent über die Online-Backup-API),
die Konfiguration und die JSON-Zustandsdateien in ein Zeitstempel-ZIP unter
``Jude/backups`` und rotiert alte Sicherungen. Geheimnisse (.env) bleiben außen
vor, damit die (auf NTFS lesbaren) ZIPs keine Schlüssel enthalten.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from core.paths import AI_DATA_ROOT, DATA_DIR, JUDE_DIR
from services.database import DB_PATH

BACKUP_DIR = JUDE_DIR / "backups"


class BackupService:
    def __init__(self, keep: int = 14):
        self.keep = keep

    @staticmethod
    def _consistent_db_copy(target: Path) -> None:
        source = sqlite3.connect(DB_PATH)
        try:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()

    def _extra_files(self) -> list[Path]:
        candidates = [
            AI_DATA_ROOT / "Projects" / "Jude" / "AGENT" / "config" / "models.yaml",
            DATA_DIR / "sub_agents.json",
            DATA_DIR / "scheduled_tasks.json",
            DATA_DIR / "voice_briefing.json",
        ]
        return [p for p in candidates if p.is_file()]

    def run(self) -> dict:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive = BACKUP_DIR / f"jude-backup-{stamp}.zip"
        with tempfile.TemporaryDirectory() as tmp:
            db_copy = Path(tmp) / "jude.db"
            self._consistent_db_copy(db_copy)
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(db_copy, "jude.db")
                for path in self._extra_files():
                    zf.write(path, path.name)
        self._rotate()
        return {"archive": str(archive), "size_bytes": archive.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "kept": len(self.list())}

    def _rotate(self) -> None:
        backups = sorted(BACKUP_DIR.glob("jude-backup-*.zip"), reverse=True)
        for old in backups[self.keep:]:
            old.unlink(missing_ok=True)

    def list(self) -> list[dict]:
        if not BACKUP_DIR.is_dir():
            return []
        items = []
        for path in sorted(BACKUP_DIR.glob("jude-backup-*.zip"), reverse=True):
            stat = path.stat()
            items.append({"archive": path.name, "size_bytes": stat.st_size,
                          "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        return items

    def restore_info(self) -> dict:
        """Sicherheitshinweis statt automatischer Wiederherstellung."""
        return {"note": "Zum Wiederherstellen Jude stoppen, das ZIP entpacken und jude.db nach "
                        f"{DB_PATH} kopieren. Sicherungen liegen unter {BACKUP_DIR}."}
