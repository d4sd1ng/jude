from __future__ import annotations

import os
import tempfile
from pathlib import Path

from core.paths import AI_DATA_ROOT  # noqa: F401  (zentrale Wurzel, auch für Importe von hier)
BLOCKED_PARTS = {"$RECYCLE.BIN", "System Volume Information", ".Trash-1000"}


def resolve_path(path: str | Path, *, for_write: bool = False) -> Path:
    target = Path(path).expanduser().resolve()
    if any(part in BLOCKED_PARTS for part in target.parts):
        raise PermissionError("System- und Papierkorbpfade sind gesperrt.")
    if for_write and target != AI_DATA_ROOT and AI_DATA_ROOT not in target.parents:
        raise PermissionError("Direktes Schreiben ist nur unter AI-Data erlaubt.")
    return target


def read_text(path: str, max_characters: int = 50000) -> str:
    target = resolve_path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    return target.read_text(encoding="utf-8", errors="replace")[:max(1, min(max_characters, 200000))]


def list_dir(path: str, max_entries: int = 300) -> dict:
    """Listet den Inhalt eines Verzeichnisses (nur lesend, System-/Papierkorb gesperrt)."""
    target = resolve_path(path)
    if not target.is_dir():
        raise NotADirectoryError(f"Kein Verzeichnis: {target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if any(part in BLOCKED_PARTS for part in child.parts):
            continue
        try:
            is_dir = child.is_dir()
            size = child.stat().st_size if child.is_file() else None
        except OSError:
            continue
        entries.append({"name": child.name, "type": "dir" if is_dir else "file", "size": size})
        if len(entries) >= max(1, min(max_entries, 2000)):
            break
    return {"path": str(target), "count": len(entries), "entries": entries}


def write_text(path: str, content: str) -> str:
    target = resolve_path(path, for_write=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def write_external_after_confirmation(path: str, content: str) -> str:
    """Nur durch den zentralen Bestätigungs-Executor aufrufen."""
    target = resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)
