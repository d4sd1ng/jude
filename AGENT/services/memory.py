from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from services.database import connection


class MemoryService:
    """Lokales, nachvollziehbares Langzeitgedächtnis mit Trainingsausschlüssen."""

    SENSITIVE = re.compile(
        r"\b(passwort|password|api[- ]?key|token|secret|login|pin|iban|kreditkarte|private key)\b",
        re.IGNORECASE,
    )
    DO_NOT_STORE = re.compile(
        r"\b(merk(?:e)? dir (?:das|dies)? nicht|nicht speichern|nicht merken|off the record|privat behandeln)\b",
        re.IGNORECASE,
    )
    EXPLICIT_REMEMBER = re.compile(r"^\s*(?:jude[, ]+)?merk(?:e)? dir(?: bitte)?[,:]?\s+(.+)$", re.IGNORECASE)
    EXPLICIT_FORGET = re.compile(r"^\s*(?:jude[, ]+)?vergiss(?: bitte)?[,:]?\s+(.+)$", re.IGNORECASE)
    STABLE_FACT = re.compile(
        r"^\s*(ich (?:bin|habe|wohne|arbeite|heiße|bevorzuge|möchte|nutze|will)\b|"
        r"mein(?:e|er|en|em|es)?\b|für mich\b)",
        re.IGNORECASE,
    )
    # Sehr verlässliche Identitäts-/Dauerfakten – rechtfertigen automatische Freigabe.
    STRONG_FACT = re.compile(
        r"^\s*ich (?:heiße|bin|wohne(?: in)?|arbeite(?: als| bei)?|komme aus)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        import os
        self.auto_approve_threshold = self._env_float("JUDE_MEMORY_AUTOAPPROVE", 0.8)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        import os
        try:
            return float(os.getenv(name, "").strip() or default)
        except ValueError:
            return default

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.findall(r"[\wäöüß]+", value.casefold(), flags=re.UNICODE))

    @classmethod
    def _fingerprint(cls, value: str) -> str:
        return hashlib.sha256(cls._normalize(value).encode()).hexdigest()

    def directive(self, text: str) -> str | None:
        """Verarbeitet explizite Speicher-/Vergissbefehle ohne Modellinterpretation."""
        if self.DO_NOT_STORE.search(text):
            self.exclude_last_turn("Nutzerbefehl: nicht merken")
            return "Verstanden. Der vorherige Gesprächsbeitrag ist vom Gedächtnis und Training ausgeschlossen."
        match = self.EXPLICIT_FORGET.match(text)
        if match:
            count = self.forget(match.group(1))
            return f"Vergessen und für erneutes automatisches Merken gesperrt: {count} Eintrag/Einträge."
        match = self.EXPLICIT_REMEMBER.match(text)
        if match:
            content = match.group(1).strip()
            if self.SENSITIVE.search(content):
                return "Sensible Zugangsdaten speichere ich nicht im Gedächtnis. Sie gehören ausschließlich in die geschützte .env-Datei."
            self.remember(content, kind="explicit", status="active", source="user", confidence=1.0)
            return "Ist dauerhaft und lokal gespeichert."
        return None

    def remember(self, content: str, *, kind: str, status: str, source: str, confidence: float) -> dict:
        normalized = self._normalize(content)
        if not normalized or self.SENSITIVE.search(content):
            raise ValueError("Leerer oder sensibler Gedächtniseintrag wird nicht gespeichert.")
        fingerprint = self._fingerprint(content)
        now = self._now()
        with connection() as db:
            if db.execute("SELECT 1 FROM memory_blocks WHERE fingerprint=?", (fingerprint,)).fetchone():
                return {"status": "blocked", "content": content}
            existing = db.execute(
                "SELECT id,status,occurrences FROM memory_items WHERE normalized=? AND kind=?",
                (normalized, kind),
            ).fetchone()
            if existing:
                occurrences = int(existing["occurrences"]) + 1
                promoted = "active" if existing["status"] == "active" or occurrences >= 2 else status
                db.execute(
                    "UPDATE memory_items SET updated_at=?,status=?,confidence=?,occurrences=? WHERE id=?",
                    (now, promoted, max(confidence, 0.9 if promoted == "active" else confidence), occurrences, existing["id"]),
                )
                return {"id": existing["id"], "status": promoted, "content": content, "occurrences": occurrences}
            item_id = uuid.uuid4().hex[:16]
            db.execute(
                "INSERT INTO memory_items(id,created_at,updated_at,kind,content,normalized,status,source,confidence,occurrences) "
                "VALUES(?,?,?,?,?,?,?,?,?,1)",
                (item_id, now, now, kind, content, normalized, status, source, confidence),
            )
        return {"id": item_id, "status": status, "content": content, "occurrences": 1}

    def capture_candidates(self, text: str) -> list[dict]:
        if self.DO_NOT_STORE.search(text) or self.SENSITIVE.search(text):
            return []
        results = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if len(sentence) < 8 or not self.STABLE_FACT.search(sentence):
                continue
            # Starke Identitätsfakten bekommen hohe Konfidenz und werden bei
            # Überschreiten der Schwelle automatisch bestätigt (kein Klick nötig).
            confidence = 0.85 if self.STRONG_FACT.search(sentence) else 0.65
            status = "active" if confidence >= self.auto_approve_threshold else "candidate"
            results.append(self.remember(sentence, kind="personal", status=status,
                                         source="automatic", confidence=confidence))
        return results

    def seed_profile(self, facts: list[str]) -> list[dict]:
        """Legt bekannte Profilfakten (Name, Vorlieben) als bestätigte Erinnerungen an."""
        stored = []
        for fact in facts:
            fact = fact.strip()
            if not fact:
                continue
            try:
                stored.append(self.remember(fact, kind="explicit", status="active",
                                            source="profil", confidence=1.0))
            except ValueError:
                continue
        return stored

    def record_turn(self, user_text: str, assistant_text: str, model: str | None, agent: str = "jude") -> None:
        excluded = bool(self.DO_NOT_STORE.search(user_text) or self.SENSITIVE.search(user_text))
        if excluded:
            return
        with connection() as db:
            db.execute(
                "INSERT INTO conversation_turns(id,created_at,user_text,assistant_text,model,training_allowed,exclusion_reason,agent) "
                "VALUES(?,?,?,?,?,1,'',?)",
                (uuid.uuid4().hex[:16], self._now(), user_text, assistant_text, model, agent),
            )

    def recent_turns(self, limit: int = 6, agent: str = "jude") -> list[dict]:
        """Letzte abgeschlossene Gesprächsrunden dieses Agenten, älteste zuerst –
        zum Wiederherstellen der Chat-Historie nach einem Neustart des Prozesses,
        der bisher jedes Mal ersatzlos vergaß, worüber gerade gesprochen wurde."""
        with connection() as db:
            rows = db.execute(
                "SELECT user_text,assistant_text FROM conversation_turns WHERE agent=? "
                "ORDER BY created_at DESC LIMIT ?", (agent, max(1, min(limit, 50))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def exclude_last_turn(self, reason: str) -> None:
        with connection() as db:
            row = db.execute("SELECT id,user_text FROM conversation_turns ORDER BY created_at DESC LIMIT 1").fetchone()
            if not row:
                return
            db.execute("UPDATE conversation_turns SET training_allowed=0,exclusion_reason=? WHERE id=?",
                       (reason, row["id"]))
            normalized = self._normalize(row["user_text"])
            db.execute("DELETE FROM memory_items WHERE normalized=?", (normalized,))
            db.execute("INSERT OR IGNORE INTO memory_blocks(fingerprint,created_at) VALUES(?,?)",
                       (self._fingerprint(row["user_text"]), self._now()))

    def forget(self, query: str) -> int:
        normalized = self._normalize(query)
        if not normalized:
            return 0
        with connection() as db:
            rows = db.execute(
                "SELECT id,content FROM memory_items WHERE normalized LIKE ? OR content LIKE ?",
                (f"%{normalized}%", f"%{query}%"),
            ).fetchall()
            for row in rows:
                db.execute("INSERT OR IGNORE INTO memory_blocks(fingerprint,created_at) VALUES(?,?)",
                           (self._fingerprint(row["content"]), self._now()))
                db.execute("DELETE FROM memory_items WHERE id=?", (row["id"],))
        return len(rows)

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM memory_items"
        params: list[object] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with connection() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def set_status(self, item_id: str, status: str) -> dict:
        if status not in {"candidate", "active"}:
            raise ValueError("Status muss candidate oder active sein.")
        with connection() as db:
            db.execute("UPDATE memory_items SET status=?,updated_at=? WHERE id=?", (status, self._now(), item_id))
            row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError("Gedächtniseintrag nicht gefunden.")
        return dict(row)

    def delete_id(self, item_id: str) -> dict:
        with connection() as db:
            row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError("Gedächtniseintrag nicht gefunden.")
            db.execute("INSERT OR IGNORE INTO memory_blocks(fingerprint,created_at) VALUES(?,?)",
                       (self._fingerprint(row["content"]), self._now()))
            db.execute("DELETE FROM memory_items WHERE id=?", (item_id,))
        return {"id": item_id, "status": "forgotten"}

    def context(self, query: str, limit: int = 8) -> str:
        tokens = {token for token in self._normalize(query).split() if len(token) > 2}
        items = self.list(limit=200)
        ranked = []
        for item in items:
            item_tokens = set(item["normalized"].split())
            score = len(tokens & item_tokens) + (2 if item["status"] == "active" else 0)
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        lines = []
        for _, item in ranked[:limit]:
            label = "bestätigt" if item["status"] == "active" else "unbestätigter Kandidat"
            lines.append(f"- [{label}] {item['content']}")
        recalled = self.recall(query, limit=3)
        if recalled:
            lines.append("Frühere Gespräche zum Thema:")
            for turn in recalled:
                lines.append(f"  · Du: {turn['user_text'][:160]} → Jude: {turn['assistant_text'][:200]}")
        return "\n".join(lines)

    def recall(self, query: str, limit: int = 3) -> list[dict]:
        """Sucht thematisch passende frühere Gesprächsbeiträge (episodisches Gedächtnis)."""
        tokens = {token for token in self._normalize(query).split() if len(token) >= 3}
        if not tokens:
            return []
        with connection() as db:
            rows = db.execute(
                "SELECT user_text,assistant_text,created_at,model FROM conversation_turns "
                "WHERE training_allowed=1 ORDER BY created_at DESC LIMIT 400"
            ).fetchall()
        ranked = []
        for row in rows:
            text_tokens = set(self._normalize(f"{row['user_text']} {row['assistant_text']}").split())
            score = len(tokens & text_tokens)
            if score >= 2:
                ranked.append((score, dict(row)))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["created_at"]), reverse=True)
        return [turn for _, turn in ranked[:limit]]

    def training_stats(self) -> dict:
        with connection() as db:
            included = db.execute("SELECT COUNT(*) AS n FROM conversation_turns WHERE training_allowed=1").fetchone()["n"]
            excluded = db.execute("SELECT COUNT(*) AS n FROM conversation_turns WHERE training_allowed=0").fetchone()["n"]
            candidates = db.execute("SELECT COUNT(*) AS n FROM memory_items WHERE status='candidate'").fetchone()["n"]
            active = db.execute("SELECT COUNT(*) AS n FROM memory_items WHERE status='active'").fetchone()["n"]
        return {"training_turns": included, "excluded_turns": excluded,
                "memory_candidates": candidates, "active_memories": active}
