from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import numpy as np
import pytest

from core.model_router import ModelRouter
from core.tool_registry import Tool, ToolRegistry
from services.backup import BackupService
from services.database import connection
from services.documents import DocumentService
from services.health import HealthService
from services.memory import MemoryService
from services.remote import SSHService
from services.scheduler import SchedulerService
from services.team import SubAgentService


# --------------------------------------------------------------- Scheduler

def test_scheduler_due_logic():
    s = SchedulerService()
    now = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    daily_past = {"enabled": True, "schedule": {"type": "daily", "at": "08:00"}, "last_run": None}
    daily_future = {"enabled": True, "schedule": {"type": "daily", "at": "10:00"}, "last_run": None}
    assert s._is_due(daily_past, now) is True
    assert s._is_due(daily_future, now) is False
    already = {"enabled": True, "schedule": {"type": "daily", "at": "08:00"},
               "last_run": now.isoformat()}
    assert s._is_due(already, now) is False
    interval_new = {"enabled": True, "schedule": {"type": "interval", "every_minutes": 5}, "last_run": None}
    assert s._is_due(interval_new, now) is True
    interval_recent = {"enabled": True, "schedule": {"type": "interval", "every_minutes": 5},
                       "last_run": (now - timedelta(minutes=2)).isoformat()}
    assert s._is_due(interval_recent, now) is False
    assert s._is_due({**interval_new, "enabled": False}, now) is False


def test_scheduler_crud_and_tick(tmp_path):
    agent = Mock()
    agent.tools.execute.return_value = "erledigt"
    s = SchedulerService(agent=agent)
    s.path = tmp_path / "tasks.json"
    task = s.create("Check", "tool", every_minutes=1, tool="noop")
    assert task["id"] in {t["id"] for t in s.list()}
    fired = s.tick(datetime.now(timezone.utc).astimezone())
    assert fired and fired[0]["ok"] is True
    agent.tools.execute.assert_called_with("noop", {})
    s.delete(task["id"])
    assert s.list() == []
    with pytest.raises(ValueError):
        s.create("x", "prompt")  # weder at noch every_minutes


# ----------------------------------------------------------------- Backup

def test_backup_creates_archive_and_rotates(tmp_path, monkeypatch):
    import services.backup as backup_mod
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", tmp_path)
    with connection() as db:  # sorgt für Tabellen in der isolierten DB
        db.execute("INSERT INTO notifications(id,kind,title,message,payload_json,created_at) VALUES('a','k','t','m','{}','now')")
    service = BackupService(keep=2)
    service.__dict__  # noqa: silence
    import zipfile
    result = service.run()
    assert zipfile.is_zipfile(result["archive"])
    with zipfile.ZipFile(result["archive"]) as zf:
        assert "jude.db" in zf.namelist()
    assert len(service.list()) == 1


# ------------------------------------------------------- episodisches Memory

def test_memory_recall_finds_past_turns():
    m = MemoryService()
    m.record_turn("Wie erreiche ich den Homeserver per SSH?",
                  "Der Homeserver pi hört auf 192.168.178.10.", "local_qwen_coder")
    hits = m.recall("nochmal die homeserver ssh adresse bitte")
    assert hits and "192.168.178.10" in hits[0]["assistant_text"]
    assert "Frühere Gespräche" in m.context("homeserver ssh")


# --------------------------------------------------------------- Dokumente RAG

def test_document_chunking():
    text = " ".join(f"wort{i}" for i in range(2500))
    chunks = DocumentService._chunk(text, size=900, overlap=150)
    assert len(chunks) >= 3
    assert all(len(c) >= 40 for c in chunks)


def test_document_ingest_and_search(tmp_path, monkeypatch):
    from core.paths import AI_DATA_ROOT
    doc = AI_DATA_ROOT / "Jude" / "test-data" / "rag_unit.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("Das Wake-Wort ist Jude angetreten. Der Server heißt pi.", encoding="utf-8")

    vectors = {"pi server": np.array([1.0, 0.0], np.float32), "andere": np.array([0.0, 1.0], np.float32)}

    def fake_embed(self, text):
        return vectors["pi server"] if "pi" in text.lower() or "server" in text.lower() else vectors["andere"]

    monkeypatch.setattr(DocumentService, "_embed", fake_embed)
    d = DocumentService()
    assert d.ingest(str(doc))["chunks"] >= 1
    result = d.search("wie heißt der server pi")
    assert result["results"] and result["results"][0]["score"] > 0.5
    d.forget_document(str(doc))
    doc.unlink(missing_ok=True)


# ------------------------------------------------------------------- Health

def test_health_status_ok_and_keys():
    with patch.object(HealthService, "_ollama", return_value={"reachable": True, "models": ["qwen3:8b"]}):
        snap = HealthService().snapshot()
    assert snap["status"] == "ok"
    for key in ("ollama", "disk", "database", "microphone", "wakeword", "recent_errors"):
        assert key in snap


def test_health_degraded_when_ollama_down():
    with patch.object(HealthService, "_ollama", return_value={"reachable": False, "error": "x"}):
        assert HealthService().snapshot()["status"] == "degraded"


# ------------------------------------------------------- Routing (Handlung)

def test_router_actionable_detection():
    r = ModelRouter()
    assert r._is_actionable("Führe auf pi 'df -h' aus")
    assert r._is_actionable("Klone das Repository")
    assert r._is_actionable("Erstelle einen Sub-Agenten")
    assert not r._is_actionable("Wie geht es dir?")
    assert not r._is_actionable("Erzähl mir einen Witz")


def test_router_routes_actions_to_tool_model():
    # Handlungsanfragen brauchen ein Tools-Modell, Plaudern bleibt beim
    # unzensierten Standardmodell. Erste Tools-Wahl ist seit 02.09.2026 die
    # freie Cloud-Stufe (siehe test_core), nicht mehr das lokale qwen.
    with patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}, clear=False):
        r = ModelRouter()
        action = r.select_model("Klone das Repo und pushe es", needs_tools=True)
        chat = r.select_model("Wie geht es dir heute?", needs_tools=True)
    assert "tools" in action.tags
    assert action.name == "cloud_ollama_gptoss"
    assert chat.name == "local_dolphin"


# --------------------------------------------------------------- Sub-Agenten

def test_team_create_validate_delete(tmp_path):
    registry = ToolRegistry()
    registry.register(Tool("noop", "d", lambda: "ok", {"type": "object", "properties": {}}))
    team = SubAgentService(registry, Mock())
    team.path = tmp_path / "agents.json"
    spec = team.create("Helfer", "Macht Dinge.", ["noop"])
    assert spec["skills"] == ["noop"]
    assert team.get("helfer")["name"] == "Helfer"
    with pytest.raises(ValueError):
        team.create("Böse", "Rolle", ["existiert_nicht"])
    team.delete("Helfer")
    assert team.list() == []


# --------------------------------------------------------------------- SSH

def test_ssh_allowlist_blocks_unlisted(monkeypatch):
    monkeypatch.setenv("JUDE_SSH_HOSTS", "pi,vps")
    s = SSHService()
    assert s.allowed_hosts() == ["pi", "vps"]
    with pytest.raises(PermissionError):
        s.run("fremder-host", "rm -rf /")
