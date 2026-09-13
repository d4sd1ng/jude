"""Dokumentwissen (RAG): Texte/PDFs lokal einlesen, einbetten und durchsuchen.

Dokumente werden in Abschnitte zerlegt, mit einem lokalen Ollama-Embedding-Modell
vektorisiert und in SQLite gespeichert. Suchen betten die Frage ein und liefern
die per Kosinus-Ähnlichkeit passendsten Abschnitte samt Quelle. Vollständig lokal
und privat; kein externer Vektordienst.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import numpy as np
import requests

from services.database import connection
from services.filesystem import resolve_path


class DocumentService:
    def __init__(self, ollama_url: str | None = None):
        self.ollama_url = (ollama_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = os.getenv("JUDE_EMBED_MODEL", "nomic-embed-text")
        self._ensure_table()

    @staticmethod
    def _ensure_table() -> None:
        with connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS rag_chunks("
                       "id TEXT PRIMARY KEY, path TEXT, chunk_index INTEGER, text TEXT, "
                       "embedding BLOB, created_at TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rag_path ON rag_chunks(path)")

    # ------------------------------------------------------------ Embedding

    def _embed(self, text: str) -> np.ndarray:
        # War 60s: dieser Aufruf geht direkt an lokales Ollama, ohne Fallback-
        # Kette oder Circuit-Breaker – blockierte search_documents (und damit
        # den ganzen Agentenlauf) bis zu einer Minute ohne Ausweich-Option,
        # wenn die lokale GPU nicht rechtzeitig antwortete (gemessen 03.09.2026,
        # Teil derselben Haenger-Kette wie der Chat-Fallback).
        response = requests.post(f"{self.ollama_url}/api/embeddings",
                                 json={"model": self.model, "prompt": text}, timeout=20)
        response.raise_for_status()
        vector = np.asarray(response.json().get("embedding", []), dtype=np.float32)
        if vector.size == 0:
            raise RuntimeError("Embedding-Modell lieferte keinen Vektor (Modell installiert?).")
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    @staticmethod
    def _read_text(path) -> str:
        target = resolve_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Datei fehlt: {target}")
        if target.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            return "\n".join((page.extract_text() or "") for page in PdfReader(str(target)).pages)
        return target.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _chunk(text: str, size: int = 220, overlap: int = 40) -> list[str]:
        """Zerlegt den Text in Abschnitte. ``size`` zaehlt WOERTER.

        Vorher standen hier 900 Woerter – rund 6.000 Zeichen. Das Einbettungs-
        modell (nomic-embed-text) bricht darueber mit
        "the input length exceeds the context length" ab, jedes Einlesen
        scheiterte also. 220 Woerter (~1.500 Zeichen) liegen sicher darunter
        und liefern zudem praezisere Treffer als lange Bloecke.
        """
        words = re.split(r"\s+", text.strip())
        chunks, step = [], max(1, size - overlap)
        for start in range(0, len(words), step):
            chunk = " ".join(words[start:start + size]).strip()
            if len(chunk) >= 40:
                chunks.append(chunk)
            if start + size >= len(words):
                break
        return chunks

    # ---------------------------------------------------------------- API

    def ingest(self, path: str) -> dict:
        target = resolve_path(path)
        text = self._read_text(path)
        chunks = self._chunk(text)
        if not chunks:
            raise ValueError("Dokument enthält keinen verwertbaren Text.")
        now = datetime.now(timezone.utc).isoformat()
        with connection() as db:
            db.execute("DELETE FROM rag_chunks WHERE path=?", (str(target),))
            for index, chunk in enumerate(chunks):
                vector = self._embed(chunk)
                db.execute("INSERT INTO rag_chunks(id,path,chunk_index,text,embedding,created_at) VALUES(?,?,?,?,?,?)",
                           (uuid.uuid4().hex[:16], str(target), index, chunk, vector.tobytes(), now))
        return {"path": str(target), "chunks": len(chunks), "ingested_at": now}

    def search(self, query: str, top_k: int = 4) -> dict:
        query_vec = self._embed(query)
        with connection() as db:
            rows = db.execute("SELECT path,chunk_index,text,embedding FROM rag_chunks").fetchall()
        if not rows:
            return {"query": query, "results": [], "note": "Noch keine Dokumente eingelesen."}
        scored = []
        for row in rows:
            vector = np.frombuffer(row["embedding"], dtype=np.float32)
            if vector.shape != query_vec.shape:
                continue
            scored.append((float(np.dot(query_vec, vector)), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = [{"path": row["path"], "chunk": row["chunk_index"],
                    "score": round(score, 3), "text": row["text"][:1200]}
                   for score, row in scored[:max(1, min(top_k, 10))]]
        return {"query": query, "results": results}

    def list_documents(self) -> list[dict]:
        with connection() as db:
            rows = db.execute("SELECT path, COUNT(*) AS chunks, MAX(created_at) AS ingested_at "
                              "FROM rag_chunks GROUP BY path ORDER BY ingested_at DESC").fetchall()
        return [dict(row) for row in rows]

    def forget_document(self, path: str) -> dict:
        target = resolve_path(path)
        with connection() as db:
            removed = db.execute("DELETE FROM rag_chunks WHERE path=?", (str(target),))
        return {"path": str(target), "removed_chunks": removed.rowcount}
