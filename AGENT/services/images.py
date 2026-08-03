"""Bilder erzeugen und bearbeiten über die OpenAI-Images-API (gpt-image-1).

Erzeugte und bearbeitete Bilder werden lokal unter ``Jude/images`` abgelegt, mit
einer Metadaten-JSON pro Bild (Prompt, Größe, Quelle, Zeit). So bleibt die
Herkunft nachvollziehbar, auch wenn die Generierung in der Cloud passiert.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone

import requests

from core.paths import IMAGES_DIR

_API_BASE = "https://api.openai.com/v1/images"
_ALLOWED_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}


class ImageService:
    def __init__(self, model: str | None = None, timeout: int = 180):
        self.model = model or os.getenv("JUDE_IMAGE_MODEL", "gpt-image-1")
        self.timeout = timeout

    # --------------------------------------------------------------- intern

    @staticmethod
    def _require_key() -> str:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Bildfunktionen benötigen OPENAI_API_KEY in AGENT/.env.")
        return key

    @staticmethod
    def _norm_size(size: str | None) -> str:
        size = (size or "1024x1024").strip()
        if size not in _ALLOWED_SIZES:
            raise ValueError(f"Ungültige Größe. Erlaubt: {', '.join(sorted(_ALLOWED_SIZES))}.")
        return size

    def _save(self, b64: str, prompt: str, size: str, kind: str, source_name: str | None = None) -> dict:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}_{kind}_{uuid.uuid4().hex[:8]}.png"
        path = IMAGES_DIR / name
        path.write_bytes(base64.b64decode(b64))
        meta = {"file": name, "kind": kind, "prompt": prompt, "size": size, "model": self.model,
                "source": "OpenAI", "based_on": source_name,
                "created_at": datetime.now(timezone.utc).isoformat()}
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), **meta}

    @staticmethod
    def _first_b64(data: dict) -> str:
        items = data.get("data") or []
        if not items or "b64_json" not in items[0]:
            raise RuntimeError("Bild-API lieferte keine Bilddaten.")
        return items[0]["b64_json"]

    # ---------------------------------------------------------------- API

    def generate(self, prompt: str, size: str | None = None) -> dict:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Für die Bilderzeugung wird ein Prompt benötigt.")
        size = self._norm_size(size)
        response = requests.post(
            f"{_API_BASE}/generations",
            headers={"Authorization": f"Bearer {self._require_key()}"},
            json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._save(self._first_b64(response.json()), prompt, size, "generiert")

    def edit(self, image: bytes, prompt: str, filename: str = "bild.png",
             mask: bytes | None = None, size: str | None = None) -> dict:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Für die Bildbearbeitung wird eine Anweisung benötigt.")
        if not image:
            raise ValueError("Es wurde kein Bild übergeben.")
        size = self._norm_size(size)
        files = [("image", (filename, image, "image/png"))]
        if mask:
            files.append(("mask", ("mask.png", mask, "image/png")))
        response = requests.post(
            f"{_API_BASE}/edits",
            headers={"Authorization": f"Bearer {self._require_key()}"},
            data={"model": self.model, "prompt": prompt, "size": size, "n": 1},
            files=files,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._save(self._first_b64(response.json()), prompt, size, "bearbeitet", source_name=filename)

    def list_recent(self, limit: int = 24) -> list[dict]:
        if not IMAGES_DIR.is_dir():
            return []
        metas = []
        for meta_file in sorted(IMAGES_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                metas.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except Exception:
                continue
        return metas
