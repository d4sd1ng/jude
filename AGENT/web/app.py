from __future__ import annotations

import asyncio
import base64
import hmac
import os
import threading
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from main import build_application
from core.paths import IMAGES_DIR
from services.actions import ActionExecutor
from services.briefing import BriefingService
from services.calendar import CalendarService
from services.coding import CodingService
from services.confirmations import ConfirmationQueue
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
    # Bewusst OHNE chat_lock: Mitarbeiter-Läufe (delegate_to_agent) bauen ihr
    # eigenes Agent-Objekt mit eigenem Gesprächsverlauf – kein geteilter
    # Zustand mit Tinos Chat. Mit chat_lock fror der Chat für die gesamte
    # Dauer jedes fälligen Mitarbeiter-Laufs ein; bei 13 stündlich/häufiger
    # fälligen Mitarbeitern hätte das den Chat regelmäßig lahmgelegt.
    while True:
        with suppress(Exception):
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


# --- Domain routes ------------------------------------------------------
# Split into web/routes/*.py (one module per domain, ~94 routes total) so no
# single file has to hold every endpoint in the app - see jolly-napping-tulip.md
# Part 3, Stage A. Imported here, at the bottom, deliberately: each route
# module does `from web.app import <singleton>` at its own top level, which
# needs every singleton above (and `app` itself) already defined. Python
# returns this already-partially-executed module from sys.modules for that
# import, so the order here is load-bearing, not incidental.
from web.routes import (  # noqa: E402
    agents,
    auftraege,
    austausch,
    calendar as calendar_routes,
    chat,
    coding as coding_routes,
    confirmations as confirmations_routes,
    documents,
    ict as ict_routes,
    images as images_routes,
    lights,
    mail as mail_routes,
    market as market_routes,
    meals as meals_routes,
    memory as memory_routes,
    news as news_routes,
    notifications as notifications_routes,
    ocr as ocr_routes,
    radar as radar_routes,
    reviews as reviews_routes,
    router_providers,
    scrape,
    shopping as shopping_routes,
    status as status_routes,
    tasks,
    vision as vision_routes,
    voice as voice_routes,
    weather as weather_routes,
)

for _module in (
    agents, auftraege, austausch, calendar_routes, chat, coding_routes,
    confirmations_routes, documents, ict_routes, images_routes, lights,
    mail_routes, market_routes, meals_routes, memory_routes, news_routes,
    notifications_routes, ocr_routes, radar_routes, reviews_routes,
    router_providers, scrape, shopping_routes, status_routes, tasks,
    vision_routes, voice_routes, weather_routes,
):
    app.include_router(_module.router)
