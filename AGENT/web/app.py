from __future__ import annotations

import asyncio
import base64
import logging
import hmac
import json
import os
import threading
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from main import build_application
from core.paths import IMAGES_DIR
from services.actions import ActionExecutor
from services.briefing import BriefingService
from services.calendar import CalendarService
from services.coding import CodingService
from services.confirmations import ConfirmationQueue
from services.database import connection
from services.fact_checker import FactCheckerService
from services.health import HealthService
from services.home_assistant import HomeAssistantService
from services.ict import ICTService
from services.mail import MailService
from services.market import MarketService
from services.meals import MealPlanService
from services.notion import NotionRecipeService
from services.memory import MemoryService
from services.news import CryptoNewsService
from services.notifications import NotificationService
from services.ocr import OCRService
from services.dwd_radar import DWDRadarService
from services.radar import RadarService
from services.images import ImageService
from services.render3d import BlenderService
from services.review import ReviewQueue
from services.scraper import ScraperService
from services.shopping import ShoppingService
from services.system_monitor import SystemMonitorService
from services.vision import VisionService
from services.weather import WeatherService
from speech.controller import VoiceController

STATIC = Path(__file__).parent / "static"
agent, _creator = build_application()
team = agent.team
confirmations, executor = ConfirmationQueue(), ActionExecutor(team=team)
reviews = ReviewQueue()
market, news, radar = MarketService(), CryptoNewsService(), RadarService()
dwd_radar = DWDRadarService()
ha, mail, ocr = HomeAssistantService(), MailService(), OCRService()
shopping, meals = ShoppingService(), MealPlanService(agent.router)
recipes = NotionRecipeService()
coding = CodingService()
calendar = CalendarService()
notifications = NotificationService()
memory = MemoryService()
ict, scraper, facts = ICTService(), ScraperService(), FactCheckerService(agent.router)
chat_lock = asyncio.Lock()
agent_lock = threading.Lock()
system_monitor = SystemMonitorService()
weather = WeatherService()
health = HealthService(router=agent.router, voice=None)
briefing = BriefingService(market, ict=ict, router=agent.router)
voice = VoiceController(agent, agent_lock, briefing=briefing)
scheduler = agent.scheduler
scheduler.briefing = briefing
scheduler.voice = voice
health.voice = voice
images = ImageService()
blender = BlenderService()
vision = VisionService()


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


async def _task_scheduler() -> None:
    while True:
        with suppress(Exception):
            async with chat_lock:
                await asyncio.to_thread(scheduler.tick)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    team.laufende_bereinigen()
    dwd_radar.warmup()
    task = asyncio.create_task(_scheduler()) if ict.scheduler_config()["enabled"] else None
    task_loop = asyncio.create_task(_task_scheduler())
    if os.getenv("JUDE_VOICE", "").strip().lower() in {"1", "true", "an", "on"}:
        voice.start()
    yield
    await asyncio.to_thread(voice.stop)
    for job in (task, task_loop):
        if job:
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job


def _allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1", "[::1]"}
    hosts.add(os.getenv("JUDE_HOST", "127.0.0.1"))
    hosts.update(h.strip() for h in os.getenv("JUDE_ALLOWED_HOSTS", "").split(",") if h.strip())
    return hosts


def _host_of(value: str) -> str:
    value = value.split("://", 1)[-1].split("/", 1)[0]
    if value.startswith("["):  # IPv6 literal
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


app = FastAPI(title="Jude", version="1.0", lifespan=lifespan, dependencies=[Depends(require_auth)])


@app.middleware("http")
async def _origin_guard(request: Request, call_next):
    """Schützt vor CSRF/DNS-Rebinding: fremde Herkunft und unbekannte Host-Header
    werden bei zustandsändernden Anfragen abgewiesen."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        allowed = _allowed_hosts()
        host = _host_of(request.headers.get("host", ""))
        if host and host not in allowed:
            return JSONResponse({"error": "Unerlaubter Host-Header."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and _host_of(origin) not in allowed:
            return JSONResponse({"error": "Anfrage von fremder Herkunft blockiert."}, status_code=403)
    response = await call_next(request)
    # GUI-Dateien nie hart cachen, damit Änderungen sofort sichtbar sind.
    if request.url.path in {"/", ""} or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.middleware("http")
async def _mount_auth(request: Request, call_next):
    """Schliesst die Luecke, die ``dependencies`` der FastAPI-App offenlaesst.

    ``app.mount()`` haengt eine eigenstaendige Starlette-App ein; deren
    Anfragen laufen am Router der Hauptanwendung vorbei und damit auch an
    ``Depends(require_auth)``. Gemessen am 03.09.2026 gegen die laufende
    Instanz: ``/`` und ``/api/status`` antworteten mit 401, ``/static/app.js``
    dagegen mit 200 – die komplette Oberflaeche samt jedem darin genannten
    API-Pfad war ohne Anmeldung lesbar. Seit start.sh an 0.0.0.0 bindet, gilt
    das fuer jeden im Netz. Middleware laeuft vor dem Routing und deckt die
    Mounts deshalb mit ab.
    """
    if request.url.path.startswith(("/static", "/images")):
        try:
            require_auth(request)
        except HTTPException as fehler:
            return JSONResponse({"error": fehler.detail}, status_code=fehler.status_code,
                                headers=dict(fehler.headers or {}))
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")


@app.exception_handler(Exception)
async def error_handler(_: Request, exc: Exception):
    return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"router": agent.router.status(),
            "mail": mail.account_status(), "ict": ict.stack_status(probe=False),
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


@app.post("/api/austausch/upload")
async def austausch_upload(file: UploadFile = File(...)):
    """Dokument von Tino entgegennehmen: landet in austausch/an-team."""
    from tools.austausch import AUSTAUSCH_DIR, _dateiname
    name = _dateiname(file.filename or "dokument")
    ziel = AUSTAUSCH_DIR / "an-team" / name
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(await file.read())
    return {"name": name, "pfad": str(ziel)}


@app.get("/api/austausch/datei")
def austausch_datei(pfad: str):
    """Datei aus dem Austausch für die Vorschau ausliefern – NUR von dort.

    Die Abnahme-Vorschau rendert Bilder und Landingpages direkt; ohne den
    harten Pfad-Zaun wäre das ein Lese-Zugang auf die ganze Platte."""
    from pathlib import Path
    from tools.austausch import AUSTAUSCH_DIR
    basis = AUSTAUSCH_DIR.resolve()
    roh = pfad.strip()
    ziel = (Path(roh) if roh.startswith("/") else basis.parent / roh).resolve()
    if not (basis in ziel.parents) or not ziel.is_file():
        raise HTTPException(403, "Nur Dateien im Austausch-Ordner.")
    return FileResponse(ziel)


@app.get("/api/auftraege")
def auftraege_api(status: str = "alle"):
    """Auftragsbuch für den Schreibtisch-Tab."""
    from services.auftraege import Auftragsbuch
    return Auftragsbuch().liste(status=status, limit=100)


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
    # Knopf im GUI = bewusste Aktivierung, kein zusaetzliches Wachwort noetig.
    return voice.start(wachwort_pflicht=False)


@app.post("/api/voice/stop")
async def voice_stop():
    return await asyncio.to_thread(voice.stop)


@app.post("/api/voice/phrases")
def voice_phrases(payload: dict):
    return voice.set_phrases(str(payload.get("wake_word", "")), str(payload.get("sleep_word", "")),
                             str(payload.get("greeting", "")), str(payload.get("farewell", "")))


@app.post("/api/voice/skip")
def voice_skip():
    return voice.skip()


@app.post("/api/voice/skip-all")
def voice_skip_all():
    return voice.skip_all()


@app.get("/api/system")
def system_snapshot():
    return system_monitor.snapshot()


@app.get("/api/weather")
async def weather_current():
    return await asyncio.to_thread(weather.current)


@app.get("/api/briefing")
async def briefing_current():
    return await asyncio.to_thread(briefing.data)


@app.get("/api/health")
def health_snapshot():
    return health.snapshot()


@app.get("/api/documents")
def documents_list():
    return {"documents": agent.documents.list_documents()}


@app.post("/api/documents/ingest")
async def documents_ingest(payload: dict):
    return await asyncio.to_thread(agent.documents.ingest, str(payload.get("path", "")))


@app.post("/api/documents/search")
async def documents_search(payload: dict):
    return await asyncio.to_thread(agent.documents.search, str(payload.get("query", "")),
                                   int(payload.get("top_k", 4)))


@app.delete("/api/documents")
def documents_forget(payload: dict):
    return agent.documents.forget_document(str(payload.get("path", "")))


@app.get("/api/backups")
def backups_list():
    return {"backups": agent.backup.list()}


@app.post("/api/backups")
async def backups_run():
    return await asyncio.to_thread(agent.backup.run)


@app.get("/api/tasks")
def tasks_list():
    return {"tasks": scheduler.list()}


@app.post("/api/tasks")
def tasks_create(payload: dict):
    return scheduler.create(str(payload.get("name", "")), str(payload.get("action_type", "prompt")),
                            at=payload.get("at"), every_minutes=payload.get("every_minutes"),
                            prompt=payload.get("prompt"), tool=payload.get("tool"),
                            tool_args=payload.get("tool_args"), speak=bool(payload.get("speak", True)))


@app.delete("/api/tasks/{task_id}")
def tasks_delete(task_id: str):
    return scheduler.delete(task_id)


@app.get("/api/agents")
def agents_list():
    return {"agents": team.list(), "skills": team.available_skills()}


@app.post("/api/agents")
def agents_create(payload: dict):
    return team.create(str(payload.get("name", "")), str(payload.get("role", "")),
                       list(payload.get("skills", [])), payload.get("model"))


@app.delete("/api/agents/{name}")
def agents_delete(name: str):
    return team.delete(name)


@app.post("/api/agents/{name}/run")
async def agents_run(name: str, payload: dict):
    return await asyncio.to_thread(team.run, name, str(payload.get("task", "")))


@app.get("/api/agents/aktiv")
def agents_aktiv():
    """Wer gerade arbeitet, wer nicht, und wann zuletzt – für die Live-Anzeige
    im Schreibtisch."""
    return team.status_uebersicht()


@app.get("/api/images")
def images_list():
    return {"items": images.list_recent()}


@app.post("/api/images/generate")
async def images_generate(payload: dict):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "Prompt fehlt")
    return await asyncio.to_thread(images.generate, prompt, str(payload.get("size", "1024x1024")))


@app.post("/api/images/edit")
async def images_edit(prompt: str = Form(...), file: UploadFile = File(...),
                      mask: UploadFile | None = File(None), size: str = Form("1024x1024")):
    image_bytes = await file.read()
    mask_bytes = await mask.read() if mask is not None else None
    return await asyncio.to_thread(images.edit, image_bytes, prompt, file.filename or "bild.png", mask_bytes, size)


@app.post("/api/images/render3d")
async def images_render3d(payload: dict):
    script = str(payload.get("blender_python", "")).strip()
    if not script:
        raise HTTPException(400, "Blender-Skript fehlt")
    return await asyncio.to_thread(blender.render, script, str(payload.get("title", "szene")),
                                   int(payload.get("width", 1024)), int(payload.get("height", 1024)),
                                   str(payload.get("engine", "BLENDER_EEVEE_NEXT")))


@app.post("/api/vision")
async def vision_describe(file: UploadFile = File(...), question: str = Form("Beschreibe dieses Bild genau auf Deutsch.")):
    image_bytes = await file.read()
    return await asyncio.to_thread(vision.describe, image_bytes, question)


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
def get_radar():
    """DWD-RV zuerst (30 min zurück, 90 min voraus); RainViewer nur als
    Notnagel, wenn der DWD nicht liefert."""
    location = {"latitude": radar.LATITUDE, "longitude": radar.LONGITUDE,
                "zip": radar.ZIP, "city": radar.CITY, "zoom": radar.ZOOM}
    try:
        return {**dwd_radar.frames(), **location}
    except Exception as exc:
        logging.getLogger(__name__).warning("DWD-Radar nicht verfügbar: %s", exc)
        return radar.frames()


@app.get("/api/radar/frame/{key}.png")
def get_radar_frame(key: str):
    try:
        image = dwd_radar.png(key)
    except KeyError:
        raise HTTPException(404, "Radar-Frame nicht gefunden")
    return Response(content=image, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})


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


@app.post("/api/notifications/read_all")
def notification_read_all(): return notifications.mark_all_read()


@app.post("/api/ict/train/{symbol}")
async def ict_train(symbol: str):
    return await asyncio.to_thread(ict.train_live, symbol.upper())


@app.post("/api/shopping")
def compare(payload: dict):
    return shopping.compare(str(payload.get("category", "")), str(payload.get("brand", "gstar")),
                            str(payload.get("size", "XXL")))


@app.get("/api/grow")
def grow_metrics():
    try:
        return ha.grow_sensors()
    except Exception:
        return {"configured": False, "sensors": []}


@app.get("/api/recipes/today")
def recipe_today():
    """Rezept des Tages aus Notion. Ist Notion nicht eingerichtet, meldet der
    Endpunkt das, statt zu werfen – das Cockpit zeigt dann einen Hinweis."""
    if not recipes.available():
        return {"configured": False, "recipe": None}
    try:
        return {"configured": True, "recipe": recipes.today()}
    except Exception as exc:
        return {"configured": True, "recipe": None, "error": str(exc)[:200]}


@app.get("/api/meals/current")
def meal_current():
    """Der Tag des zuletzt erzeugten Plans, der auf heute fällt. Läuft der Plan
    aus, bleibt kein Tag übrig und das Cockpit zeigt einen Hinweis."""
    with connection() as db:
        row = db.execute("SELECT created_at, plan_json FROM meal_plans ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        return {"day": None}
    plan = json.loads(row["plan_json"])
    days = plan.get("days", [])
    created = datetime.fromisoformat(row["created_at"])
    index = (datetime.now(timezone.utc) - created).days
    return {"day": days[index] if 0 <= index < len(days) else None}


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
    from core.paths import MEALS_DIR
    allowed = MEALS_DIR.resolve()
    if allowed not in path.parents or not path.is_file():
        raise HTTPException(404, "PDF nicht gefunden")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/calendar/{event_id}/ics")
def calendar_ics(event_id: str):
    if not event_id.isalnum():
        raise HTTPException(400, "Ungültige Termin-ID")
    from core.paths import CALENDAR_DIR
    matches = list(CALENDAR_DIR.glob(f"*_{event_id[:8]}.ics"))
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
def confirmation_decide(action_id: str, decision: str, payload: dict | None = None):
    if decision not in {"approve", "reject", "revision"}:
        raise HTTPException(400, "Entscheidung muss approve, reject oder revision sein")
    if decision == "revision":
        anmerkung = str((payload or {}).get("anmerkung", "")).strip()
        if not anmerkung:
            raise HTTPException(400, "Für eine Revision wird eine Anmerkung benötigt")
        try:
            return confirmations.revision(action_id, anmerkung)
        except KeyError:
            raise HTTPException(404, "Unbekannte Bestätigung")
    return confirmations.decide(action_id, decision == "approve", executor)


# --- Abnahme ----------------------------------------------------------------
# Nicht zu verwechseln mit den Bestaetigungen darueber: die halten eine Aktion
# an, bis Tino zustimmt. Hier legt ein Mitarbeiter etwas Fertiges vor und
# arbeitet sofort weiter; Tino nimmt ab oder schickt es mit einer Anmerkung
# zurueck.

@app.get("/api/reviews")
def review_list(status: str = "offen", limit: int = 50, art: str | None = None):
    return {"vorlagen": reviews.liste(status, limit, art),
            "zusammenfassung": reviews.zusammenfassung(),
            "offen_nach_art": reviews.offen_nach_art()}


@app.get("/api/reviews/{review_id}")
def review_show(review_id: str):
    try:
        return reviews.zeigen(review_id)
    except KeyError:
        raise HTTPException(404, "Unbekannte Vorlage")


@app.post("/api/reviews/{review_id}/{entscheidung}")
def review_decide(review_id: str, entscheidung: str, payload: dict | None = None):
    if entscheidung not in {"abnehmen", "revision"}:
        raise HTTPException(400, "Entscheidung muss abnehmen oder revision sein")
    anmerkung = str((payload or {}).get("anmerkung", "")).strip()
    if entscheidung == "revision" and not anmerkung:
        raise HTTPException(400, "Für eine Revision wird eine Anmerkung benötigt")
    try:
        if entscheidung == "abnehmen":
            ergebnis = reviews.abnehmen(review_id, anmerkung)
            # Bisher hielt das Team nur fest, was schiefging – nie, was gut war.
            # Tino soll dafuer nichts extra tippen muessen: eine Abnahme ohne
            # jede Revision ist selbst schon das Signal – automatisch erkannt
            # aus dem Revisionsverlauf, nicht aus einem getippten Lob.
            if int(ergebnis.get("runde") or 1) <= 1:
                with suppress(Exception):
                    team.lob_merken(
                        ergebnis["agent"],
                        f"„{ergebnis.get('titel', '')}“ ({ergebnis.get('art', '')}) "
                        "im ersten Anlauf ohne Revision abgenommen.",
                        quelle="automatisch")
            if anmerkung:
                with suppress(Exception):
                    team.lob_merken(ergebnis["agent"], anmerkung, quelle="Tino")
            return ergebnis
        ergebnis = reviews.revision(review_id, anmerkung)
    except KeyError:
        raise HTTPException(404, "Unbekannte Vorlage")
    # Tinos Anmerkung wiegt schwerer als jede andere: sie wird zur Lehre, damit
    # derselbe Einwand nicht bei jeder Vorlage aufs Neue kommt.
    with suppress(Exception):
        team.lehre_merken(ergebnis["agent"], anmerkung, quelle="Tino")
    return ergebnis
