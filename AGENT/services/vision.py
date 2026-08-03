"""Bilder verstehen (Vision): beschreibt und beantwortet Fragen zu gegebenen Bildern.

Lokal-first: nutzt ein multimodales Ollama-Modell (privat, kostenlos). Ist kein
lokales Vision-Modell vorhanden, kann optional OpenAI als Cloud-Fallback dienen.
"""

from __future__ import annotations

import base64
import os

import requests


class VisionService:
    def __init__(self, ollama_url: str | None = None, timeout: int = 180):
        self.ollama_url = (ollama_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.local_model = os.getenv("JUDE_VISION_MODEL", "qwen2.5vl:7b")
        self.cloud_model = os.getenv("JUDE_VISION_CLOUD_MODEL", "gpt-5.6-terra")
        self.timeout = timeout

    # ---------------------------------------------------------------- lokal

    def _local_available(self) -> bool:
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=4)
            response.raise_for_status()
            names = {m["name"] for m in response.json().get("models", [])}
            return self.local_model in names or f"{self.local_model}:latest" in names
        except requests.RequestException:
            return False

    def _describe_local(self, image_b64: str, question: str) -> str:
        response = requests.post(
            f"{self.ollama_url}/api/chat",
            json={"model": self.local_model, "stream": False,
                  "messages": [{"role": "user", "content": question, "images": [image_b64]}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json().get("message", {}).get("content", "")).strip()

    # ---------------------------------------------------------------- cloud

    def _describe_cloud(self, image_b64: str, question: str) -> str:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Kein lokales Vision-Modell und kein OPENAI_API_KEY für den Fallback.")
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.cloud_model, "input": [{"role": "user", "content": [
                {"type": "input_text", "text": question},
                {"type": "input_image", "image_url": f"data:image/png;base64,{image_b64}"},
            ]}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        parts = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                parts.extend(p.get("text", "") for p in item.get("content", [])
                             if p.get("type") in {"output_text", "text"})
        return "".join(parts).strip() or str(data.get("output_text", "")).strip()

    # ------------------------------------------------------------------ API

    def describe(self, image: bytes, question: str = "Beschreibe dieses Bild genau auf Deutsch.") -> dict:
        if not image:
            raise ValueError("Es wurde kein Bild übergeben.")
        question = (question or "Beschreibe dieses Bild genau auf Deutsch.").strip()
        image_b64 = base64.b64encode(image).decode()
        if self._local_available():
            return {"answer": self._describe_local(image_b64, question),
                    "model": self.local_model, "source": "lokal"}
        return {"answer": self._describe_cloud(image_b64, question),
                "model": self.cloud_model, "source": "OpenAI"}

    def describe_path(self, path: str, question: str = "Beschreibe dieses Bild genau auf Deutsch.") -> dict:
        from services.filesystem import resolve_path
        target = resolve_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Bild nicht gefunden: {path}")
        return self.describe(target.read_bytes(), question)
