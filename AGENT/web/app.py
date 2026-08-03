from __future__ import annotations

import asyncio
import base64
import hmac
import os
import threading
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from main import build_application
from services.actions import ActionExecutor
from services.calendar import CalendarService
from services.coding import CodingService
from services.confirmations import ConfirmationQueue
from services.database import connection
from services.fact_checker import FactCheckerService
from services.home_assistant import HomeAssistantService
from services.ict import ICTService
from services.mail import MailService
from services.market import MarketService
from services.meals import MealPlanService
from services.memory import MemoryService
from services.news import CryptoNewsService
from services.notifications import NotificationService
from services.ocr import OCRService
from services.radar import RadarService
from services.scraper import ScraperService
from services.shopping import ShoppingService
from speech.controller import VoiceController

STATIC = Path(__file__).parent / "static"
agent, _creator = build_application()
confirmations, executor = ConfirmationQueue(), ActionExecutor()
market, news, radar = MarketService(), CryptoNewsService(), RadarService()
ha, mail, ocr = HomeAssistantService(), MailService(), OCRService()
shopping, meals = ShoppingService(), MealPlanService(agent.router)
coding = CodingService()
calendar = CalendarService()
notifications = NotificationService()
memory = MemoryService()
ict, scraper, facts = ICTService(), ScraperService(), FactCheckerService(agent.router)
chat_lock = asyncio.Lock()
agent_lock = threading.Lock()
voice = VoiceController(agent, agent_lock)


def _local(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", None}


def require_auth(request: Request) -> None:
    user, password = os.getenv("JUDE_GUI_USER", ""), os.getenv("JUDE_GUI_PASSWORD", "")
    if _local(request.client.host if request.client else None) and not user and not password:
        return
    if not user or not password:
        raise HTTPException(403, "Remote-Zugriff ist ohne GUI-Zugangsdaten gesperrt.")
    header = request.headers.get("authorization", "")
    try:
        scheme, value = header.split(" ", 1)
        supplied_user, supplied_password = base64.b64decode(value).decode().split(":", 1)
    except Exception:
        raise HTTPException(401, "Anmeldung erforderlich", headers={"WWW-Authenticate": "Basic"})
    if scheme.lower() != "basic" or not hmac.compare_digest(supplied_user, user) or not hmac.compare_digest(supplied_password, password):
        raise HTTPException(401, "Ungültige Anmeldung", headers={"WWW-Authenticate": "Basic"})


async def _scheduler() -> None:
    while True:
        await asyncio.to_thread(ict.run_due, agent.router)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_scheduler()) if ict.scheduler_config()["enabled"] else None
    if os.getenv("JUDE_VOICE", "").strip().lower() in {"1", "true", "an", "on"}:
        voice.start()
    yield
    await asyncio.to_thread(voice.stop)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Jude", version="1.0", lifespan=lifespan, dependencies=[Depends(require_auth)])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(Exception)
async def error_handler(_: Request, exc: Exception):
    return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"router": agent.router.status(), "mail": mail.account_status(), "ict": ict.stack_status(probe=False),
            "home_assistant_configured": bool(ha.url and ha.token), "news_configured": bool(os.getenv("NEWS_API_KEY")),
            "home_actions": ha.action_status(), "fake_checker": "ready", "scraper": "public_http_only",
            "memory": memory.training_stats()}


@app.get("/api/memory")
def memory_list(status: str | None = None):
    return {"items": memory.list(status=status), "stats": memory.training_stats()}


@app.post("/api/memory")
def memory_add(payload: dict):
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(400, "Inhalt fehlt")
    return memory.remember(content, kind="explicit", status="active", source="gui", confidence=1.0)


@app.post("/api/memory/{item_id}/approve")
def memory_approve(item_id: str):
    return memory.set_status(item_id, "active")


@app.delete("/api/memory/{item_id}")
def memory_delete(item_id: str):
    return memory.delete_id(item_id)


def _chat_sync(text: str) -> dict:
    with agent_lock:
        answer_text = agent.process_input(text)
        return {"answer": answer_text, "model": agent.last_model, "route_id": agent.last_route_id}


@app.post("/api/chat")
async def chat(payload: dict):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Text fehlt")
    async with chat_lock:
        return await asyncio.to_thread(_chat_sync, text)


@app.get("/api/voice")
def voice_status():
    return voice.status()


@app.get("/api/voice/events")
def voice_events(since: int = 0):
    return voice.events(since)


@app.post("/api/voice/start")
def voice_start():
    return voice.start()


@app.post("/api/voice/stop")
async def voice_stop():
    return await asyncio.to_thread(voice.stop)


@app.post("/api/routing/{route_id}/feedback")
def routing_feedback(route_id: str, payload: dict):
    return agent.router.feedback(route_id, int(payload.get("value", 0)))


@app.get("/api/market/{market_name:path}")
def market_data(market_name: str, interval: str = "1h", limit: int = 300, refresh: bool = True):
    return market.fetch(market_name, interval, limit) if refresh else market.history(market_name, interval, limit)


@app.get("/api/market/{market_name:path}/csv", response_class=PlainTextResponse)
def market_csv(market_name: str, interval: str = "1h"):
    return PlainTextResponse(market.csv_export(market_name, interval), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{market_name.replace("/", "-")}_{interval}.csv"'})


@app.get("/api/news")
def get_news(): return news.fetch()


@app.get("/api/news/brief")
async def get_news_brief():
    articles = await asyncio.to_thread(news.fetch)
    response = await asyncio.to_thread(
        agent.router.call_with_fallback,
        [{"role": "user", "content": news.journalist_prompt(articles)}],
    )
    return {"brief": response.get("content", ""), "model": response.get("_model"), "news": articles}


@app.get("/api/radar")
def get_radar(): return radar.frames()


@app.get("/api/lights")
def lights(): return {"entities": ha.entities(), "states": ha.states() if ha.url and ha.token else []}


@app.post("/api/lights/{room}/{state}")
def switch_light(room: str, state: str): return {"result": ha.switch_light(room, state)}


@app.get("/api/home-actions")
def home_actions(): return ha.action_status()


@app.post("/api/home-actions/{group}/{action}")
def home_action(group: str, action: str): return {"result": ha.run_profile(group, action)}


@app.post("/api/ocr")
async def run_ocr(file: UploadFile = File(...), language: str = Form("deu+eng")):
    return ocr.extract(await file.read(), file.filename or "upload", language)


@app.get("/api/mail/status")
def mail_status(): return mail.account_status()


@app.get("/api/mail/{account}/search")
def mail_search(account: str, q: str = "ALL", limit: int = 30): return mail.search(account, q, limit)


@app.get("/api/mail/{account}/{message_id}")
def mail_read(account: str, message_id: str, folder: str = "INBOX"): return mail.read(account, message_id, folder)


@app.post("/api/mail/draft")
def mail_draft(payload: dict): return {"result": mail.create_draft(str(payload["account"]), str(payload["to"]), str(payload["subject"]), str(payload["body"]))}


@app.post("/api/mail/archive")
def mail_archive(payload: dict): return {"result": mail.archive(str(payload["account"]), str(payload["message_id"]), str(payload.get("folder", "INBOX")))}


@app.get("/api/coding/repositories")
def coding_repositories(): return coding.repositories()


@app.post("/api/coding/status")
def coding_status(payload: dict): return {"status": coding.status(str(payload["repo"]))}


@app.post("/api/coding/test")
async def coding_test(payload: dict): return await asyncio.to_thread(coding.test, str(payload["repo"]))


@app.post("/api/coding/read")
def coding_read(payload: dict): return {"content": coding.read(str(payload["path"]))}


@app.post("/api/coding/write")
def coding_write(payload: dict): return {"path": coding.write(str(payload["path"]), str(payload["content"]))}


@app.post("/api/coding/diff")
def coding_diff(payload: dict): return {"diff": coding.diff(str(payload["repo"]))}


@app.post("/api/coding/branch")
def coding_branch(payload: dict): return {"branch": coding.create_branch(str(payload["repo"]), str(payload["branch"]))}


@app.post("/api/coding/commit")
def coding_commit(payload: dict): return {"commit": coding.commit(str(payload["repo"]), str(payload["message"]), list(payload["paths"]))}


@app.post("/api/coding/push")
def coding_push(payload: dict): return {"remote": coding.push(str(payload["repo"]), str(payload["branch"]))}


@app.post("/api/coding/pr")
def coding_pr(payload: dict): return {"url": coding.create_pr(str(payload["repo"]), str(payload["title"]), str(payload["body"]), bool(payload.get("draft", True)))}


@app.get("/api/calendar")
def calendar_list(): return calendar.list()


@app.get("/api/notifications")
def notification_list(unread_only: bool = True): return notifications.list(unread_only)


@app.post("/api/notifications/{notification_id}/read")
def notification_read(notification_id: str): return notifications.mark_read(notification_id)


@app.post("/api/ict/train/{symbol}")
async def ict_train(symbol: str):
    return await asyncio.to_thread(ict.train_live, symbol.upper())


@app.post("/api/shopping")
def compare(payload: dict): return shopping.compare(str(payload.get("category", "")))


@app.post("/api/meals")
async def meal(payload: dict):
    return await asyncio.to_thread(meals.generate, int(payload.get("days", 7)), int(payload.get("people", 2)))


@app.get("/api/meals/{plan_id}/pdf")
def meal_pdf(plan_id: str):
    with connection() as db:
        row = db.execute("SELECT pdf_path FROM meal_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Essensplan nicht gefunden")
    path = Path(row["pdf_path"]).resolve()
    allowed = Path("/media/d4sd1ng/AI-Data/Essensplan").resolve()
    if allowed not in path.parents or not path.is_file():
        raise HTTPException(404, "PDF nicht gefunden")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/calendar/{event_id}/ics")
def calendar_ics(event_id: str):
    if not event_id.isalnum():
        raise HTTPException(400, "Ungültige Termin-ID")
    matches = list(Path("/media/d4sd1ng/AI-Data/Kalender").glob(f"*_{event_id[:8]}.ics"))
    if len(matches) != 1:
        raise HTTPException(404, "Kalenderdatei nicht gefunden")
    return FileResponse(matches[0], media_type="text/calendar", filename=matches[0].name)


@app.get("/api/ict/status")
def ict_status(): return ict.stack_status()


@app.get("/api/ict/cards")
def ict_cards(): return ict.cards()


@app.post("/api/ict/analyse/{symbol}")
async def ict_analyse(symbol: str): return await asyncio.to_thread(ict.analyse_live, agent.router, symbol.upper())


@app.post("/api/scrape")
async def scrape(payload: dict): return await asyncio.to_thread(scraper.extract, str(payload.get("url", "")))


@app.post("/api/fact-check")
async def fact_check(payload: dict): return await asyncio.to_thread(facts.check, str(payload.get("url", "")))


@app.get("/api/confirmations")
def confirmation_list(): return confirmations.list()


@app.post("/api/confirmations")
def confirmation_request(payload: dict):
    return confirmations.request(str(payload["action_type"]), str(payload["summary"]), dict(payload["payload"]))


@app.post("/api/confirmations/{action_id}/{decision}")
def confirmation_decide(action_id: str, decision: str):
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Entscheidung muss approve oder reject sein")
    return confirmations.decide(action_id, decision == "approve", executor)
