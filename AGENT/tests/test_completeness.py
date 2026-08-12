from __future__ import annotations

import json
import subprocess
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from core.model_router import ModelRouter
from core.tool_registry import ToolRegistry
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from services.calendar import CalendarService
from services.confirmations import ConfirmationQueue
from services.database import connection
from services.fact_checker import FactCheckerService
from services.home_assistant import HomeAssistantService
from services.notifications import NotificationService
from services.ocr import OCRService
from services.radar import RadarService
from services.shopping import ShoppingService
from tools import load_all_tools
from web.app import app


class SequenceRouter:
    def __init__(self, contents):
        self.contents = iter(contents)

    def call_with_fallback(self, *_args, **_kwargs):
        return {"content": next(self.contents)}


def response(data):
    result = Mock()
    result.json.return_value = data
    result.raise_for_status.return_value = None
    return result


def test_all_required_agent_tools_are_registered():
    registry = ToolRegistry()
    load_all_tools(registry, router=ModelRouter(), confirmations=ConfirmationQueue())
    required = {
        "market_fetch", "market_history", "crypto_news", "crypto_news_brief", "rain_radar",
        "light_switch", "home_action_status", "home_action_run", "mail_search", "mail_read", "mail_draft", "mail_archive",
        "request_mail_send", "request_mail_delete", "request_calendar_event", "calendar_list",
        "shopping_compare", "meal_plan", "ocr_file", "scrape_public_url", "fact_check_url",
        "coding_repositories", "coding_status", "coding_read", "coding_write", "coding_diff",
        "coding_branch", "coding_commit", "coding_push", "coding_create_pr", "coding_test",
        "ict_status", "ict_analyse_live", "request_confirmation",
        "ict_training_status", "memory_list", "memory_stats",
    }
    assert required <= set(registry.tools)


def test_confirmation_executor_may_use_database_without_lock():
    queue = ConfirmationQueue()
    pending = queue.request("calendar_create", "Test", {"title": "Test"})

    def nested(_kind, _payload):
        with connection() as db:
            db.execute("INSERT INTO audit_log(created_at,actor,action,target,details_json,success) VALUES(?,?,?,?,?,?)",
                       ("now", "test", "nested", None, "{}", 1))
        return "ok"

    result = queue.decide(pending["id"], True, nested)
    assert result["status"] == "approved"
    assert queue.list() == []


def test_notification_lifecycle():
    created = NotificationService.create("test", "Titel", "Text", {"x": 1})
    assert NotificationService.list()[0]["payload"] == {"x": 1}
    NotificationService.mark_read(created["id"])
    assert NotificationService.list() == []


def test_fact_checker_requires_two_qualified_domains():
    class FakeScraper:
        def extract(self, url):
            if url == "https://source.test/post":
                return {"url": url, "title": "Post", "author": "A", "published_at": "2026-01-01", "text": "Behauptung " * 30}
            return {"url": url, "title": url, "published_at": "2026-01-02", "text": "Direkter Beleg " * 30}

        def search(self, *_):
            return [{"url": "https://one.test/a"}, {"url": "https://two.test/b"}]

    router = SequenceRouter([
        json.dumps({"claims": [{"claim": "X ist geschehen", "search_query": "X"}]}),
        json.dumps({"explanation": "Zwei Belege", "uncertainty": "gering", "assessments": [
            {"evidence_id": "E1", "serious": True, "independent": True, "stance": "supports", "reason": "Primärquelle"},
            {"evidence_id": "E2", "serious": True, "independent": True, "stance": "supports", "reason": "Redaktion"},
        ]}),
    ])
    report = FactCheckerService(router, FakeScraper()).check("https://source.test/post")
    assert report["verdict"] == "confirmed"
    assert len(report["claims"][0]["supporting_urls"]) == 2


def test_fact_checker_repairs_minor_model_json_errors():
    assert FactCheckerService._json("Antwort: {'ok': true, 'items': [1,2,],}") == {"ok": True, "items": [1, 2]}


def test_home_assistant_state_and_switch(monkeypatch):
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://ha.test")
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "token")
    service = HomeAssistantService()
    with patch("services.home_assistant.requests.get", return_value=response([
        {"entity_id": "light.wohnzimmer", "state": "on", "attributes": {"friendly_name": "Wohnzimmer"}},
        {"entity_id": "sensor.other", "state": "x", "attributes": {}},
    ])):
        assert service.states()[0]["entity_id"] == "light.wohnzimmer"
    with patch("services.home_assistant.requests.post", return_value=response([])) as post:
        assert service.switch_light("flur", "off") == "flur off"
        assert post.call_args.args[0].endswith("/api/services/light/turn_off")


def test_home_assistant_runs_only_configured_action_profiles(monkeypatch):
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://ha.test")
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "token")
    monkeypatch.setenv("HA_ALEXA_ACTIONS_JSON", json.dumps({
        "guten_morgen": {"domain": "script", "service": "turn_on", "entity_id": "script.guten_morgen"},
    }))
    service = HomeAssistantService()
    with patch("services.home_assistant.requests.post", return_value=response([])) as post:
        assert service.run_profile("alexa", "guten_morgen") == "alexa/guten_morgen ausgeführt"
        assert post.call_args.args[0] == "http://ha.test/api/services/script/turn_on"
    try:
        service.run_profile("alexa", "nicht_freigegeben")
    except ValueError as exc:
        assert "Nicht freigegebene" in str(exc)
    else:
        raise AssertionError("Nicht konfigurierte Aktion wurde ausgeführt")


def test_radar_parsing():
    with patch("services.radar.requests.get", return_value=response({"host": "https://tile.test", "generated": 3,
            "radar": {"past": [{"time": 100, "path": "/x"}]}})):
        data = RadarService().frames()
    assert data["status"] == "live" and data["address"].startswith("Berliner")


def test_shopping_separates_brands_and_fixes_special_sizes():
    gstar = [{"brand": "G-Star", "title": "G-Star Shirt", "url": "https://g-star.com/de_de/x", "snippet": "",
              "prices": ["39,95 €"], "price_eur": 39.95, "size": "XL", "availability": "InStock"}]
    with patch.object(ShoppingService, "_gstar_products", return_value=gstar):
        data = ShoppingService().compare("t-shirt", brand="gstar", size="XL")
    assert data["brand"] == "G-Star" and data["size"] == "XL" and data["count"] == 1
    nike = [{"brand": "Nike", "title": "Nike Air Max 90", "url": "https://nike.com/de/t/x", "snippet": "",
             "prices": ["149,99 €"], "price_eur": 149.99, "size": "44", "availability": "laut Größenfilter"}]
    with patch.object(ShoppingService, "_nike_products", return_value=nike):
        data = ShoppingService().compare("schuhe", brand="gstar", size="XXL")  # wird auf Nike/44 gezwungen
    assert data["brand"] == "Nike" and data["size"] == "44"
    with patch.object(ShoppingService, "_gstar_products", return_value=gstar):
        data = ShoppingService().compare("jeans", brand="nike", size="L")     # wird auf G-Star/W33 L34 gezwungen
    assert data["brand"] == "G-Star" and data["size"] == "W33 L34"


def test_real_ocr_german_and_english():
    image = Image.new("RGB", (900, 160), "white")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 38)
    ImageDraw.Draw(image).text((20, 45), "Hallo Marburg English 2026", fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    result = OCRService().extract(buffer.getvalue(), "sample.png", "deu+eng")
    assert "Marburg" in result["text"] and "English" in result["text"]


def test_calendar_creates_valid_ics():
    path = Path(CalendarService().create_confirmed("Arzt", "2026-08-01T10:00:00+02:00", "2026-08-01T11:00:00+02:00"))
    try:
        text = path.read_text(encoding="utf-8")
        assert "BEGIN:VEVENT" in text and "SUMMARY:Arzt" in text and "DTSTART:20260801T080000Z" in text
    finally:
        path.unlink(missing_ok=True)


def test_coding_service_real_git_repository():
    from services.coding import CodingService
    from core.paths import TEST_REPOS_DIR
    root = TEST_REPOS_DIR / uuid.uuid4().hex
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    try:
        service = CodingService()
        written = service.write(str(root / "hello.txt"), "ok")
        assert Path(written).read_text() == "ok"
        assert "hello.txt" in service.status(str(root))
        assert service.create_branch(str(root), "codex/audit") == "codex/audit"
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


def test_remote_gui_requires_auth_and_status_is_fast(monkeypatch):
    monkeypatch.delenv("JUDE_GUI_USER", raising=False)
    monkeypatch.delenv("JUDE_GUI_PASSWORD", raising=False)
    client = TestClient(app)
    assert client.get("/api/status").status_code == 403
    monkeypatch.setenv("JUDE_GUI_USER", "jude")
    monkeypatch.setenv("JUDE_GUI_PASSWORD", "secret")
    response = client.get("/api/status", auth=("jude", "secret"))
    assert response.status_code == 200
    assert response.json()["ict"]["connection"]["ready"] is None
    page = client.get("/", auth=("jude", "secret"))
    assert page.status_code == 200 and 'id="mealResult"' in page.text and 'id="shopBrand"' in page.text


def test_gui_downloads_recorded_meal_pdf(monkeypatch):
    monkeypatch.setenv("JUDE_GUI_USER", "jude")
    monkeypatch.setenv("JUDE_GUI_PASSWORD", "secret")
    plan_id = uuid.uuid4().hex[:12]
    from core.paths import MEALS_DIR
    target = MEALS_DIR / f"essensplan_{plan_id}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    try:
        with connection() as db:
            db.execute("INSERT INTO meal_plans(id,created_at,days,people,plan_json,pdf_path) VALUES(?,?,?,?,?,?)",
                       (plan_id, "now", 7, 2, "{}", str(target)))
        response = TestClient(app).get(f"/api/meals/{plan_id}/pdf", auth=("jude", "secret"))
        assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    finally:
        target.unlink(missing_ok=True)
