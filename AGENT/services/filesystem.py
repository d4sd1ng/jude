from __future__ import annotations

import os
import tempfile
from pathlib import Path

AI_DATA_ROOT = Path(os.getenv("AI_DATA_ROOT", "/media/d4sd1ng/AI-Data")).resolve()
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
