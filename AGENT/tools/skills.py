from __future__ import annotations

import json

from core.tool_registry import Tool, ToolRegistry
from services.filesystem import list_dir
from services.remote import SSHService
from services.calendar import CalendarService
from services.coding import CodingService
from services.confirmations import ConfirmationQueue
from services.home_assistant import HomeAssistantService
from services.ict import ICTService
from services.mail import MailService
from services.market import MarketService
from services.memory import MemoryService
from services.news import CryptoNewsService
from services.ocr import OCRService
from services.radar import RadarService
from services.scraper import ScraperService
from services.shopping import ShoppingService


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    result = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def register(registry: ToolRegistry) -> None:
    market, news, radar = MarketService(), CryptoNewsService(), RadarService()
    ha, mail, shopping, coding, ict = HomeAssistantService(), MailService(), ShoppingService(), CodingService(), ICTService()
    ocr, scraper, calendar, memory = OCRService(), ScraperService(), CalendarService(), MemoryService()
    ssh = SSHService()
    tools = [
        Tool("market_fetch", "Aktuelle OHLCV-Daten abrufen und speichern.", market.fetch,
             _schema({"market": {"type": "string", "enum": ["BTC/EUR", "BTC/USD", "XAU/EUR", "XAU/USD"]}, "interval": {"type": "string"}, "limit": {"type": "integer"}}, ["market"])),
        Tool("market_history", "Gespeicherte OHLCV-Historie lesen.", market.history,
             _schema({"market": {"type": "string"}, "interval": {"type": "string"}, "limit": {"type": "integer"}}, ["market"])),
        Tool("crypto_news", "Aktuelle Crypto-News über NewsAPI abrufen.", news.fetch,
             _schema({"language": {"type": "string"}, "hours": {"type": "integer"}, "limit": {"type": "integer"}}), untrusted=True),
        Tool("rain_radar", "Regenradar-Frames für Berliner Straße in Marburg abrufen.", radar.frames, _schema({})),
        Tool("light_switch", "Home-Assistant-Licht in Wohnzimmer, Schlafzimmer oder Flur schalten.", ha.switch_light,
             _schema({"room": {"type": "string", "enum": ["wohnzimmer", "schlafzimmer", "flur"]}, "state": {"type": "string", "enum": ["on", "off"]}}, ["room", "state"]), confirm_action="home_switch"),
        Tool("home_action_status", "Konfigurierte Alexa- und Growcontroller-Aktionen auflisten.", ha.action_status, _schema({})),
        Tool("home_action_run", "Ausschließlich eine konfigurierte Home-Assistant-Allowlist-Aktion ausführen.", ha.run_profile,
             _schema({"group": {"type": "string", "enum": ["alexa", "grow"]}, "action": {"type": "string"}}, ["group", "action"]), confirm_action="home_action"),
        Tool("mail_search", "Konfiguriertes Postfach durchsuchen.", mail.search,
             _schema({"account": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}, "folder": {"type": "string"}}, ["account"])),
        Tool("mail_read", "E-Mail lesen.", mail.read,
             _schema({"account": {"type": "string"}, "message_id": {"type": "string"}, "folder": {"type": "string"}}, ["account", "message_id"]), untrusted=True),
        Tool("mail_draft", "E-Mail-Entwurf erstellen, aber nicht senden.", mail.create_draft,
             _schema({"account": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["account", "to", "subject", "body"])),
        Tool("mail_archive", "E-Mail archivieren.", mail.archive,
             _schema({"account": {"type": "string"}, "message_id": {"type": "string"}, "folder": {"type": "string"}}, ["account", "message_id"]), confirm_action="mail_archive"),
        Tool("shopping_compare", "G-Star/Nike-Herrenbekleidung vergleichen; keine Bestellung.", shopping.compare,
             _schema({"category": {"type": "string"}, "limit": {"type": "integer"}}, ["category"])),
        Tool("coding_repositories", "Git-Repositories unter AI-Data inventarisieren.", coding.repositories, _schema({})),
        Tool("coding_status", "Git-Status eines AI-Data-Repositories lesen.", coding.status, _schema({"repo": {"type": "string"}}, ["repo"])),
        Tool("coding_test", "Vorhandene Tests eines AI-Data-Repositories ausführen.", coding.test, _schema({"repo": {"type": "string"}}, ["repo"])),
        Tool("coding_read", "Textdatei innerhalb oder außerhalb AI-Data read-only lesen.", coding.read,
             _schema({"path": {"type": "string"}}, ["path"]), untrusted=True),
        Tool("list_directory", "Verzeichnisinhalt auflisten (nur lesend; System-/Papierkorbpfade gesperrt).", list_dir,
             _schema({"path": {"type": "string"}, "max_entries": {"type": "integer"}}, ["path"])),
        Tool("coding_write", "Textdatei ausschließlich unter AI-Data atomar schreiben.", coding.write,
             _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), confirm_action="code_write"),
        Tool("coding_diff", "Git-Diff eines AI-Data-Repositories lesen.", coding.diff, _schema({"repo": {"type": "string"}}, ["repo"])),
        Tool("coding_branch", "codex/-Branch in einem AI-Data-Repository erstellen.", coding.create_branch,
             _schema({"repo": {"type": "string"}, "branch": {"type": "string"}}, ["repo", "branch"]), confirm_action="code_branch"),
        Tool("coding_commit", "Explizite Pfade committen.", coding.commit,
             _schema({"repo": {"type": "string"}, "message": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}, ["repo", "message", "paths"]), confirm_action="code_commit"),
        Tool("coding_push", "Branch zu origin pushen.", coding.push,
             _schema({"repo": {"type": "string"}, "branch": {"type": "string"}}, ["repo", "branch"]), confirm_action="code_push"),
        Tool("coding_clone", "Git-Repository nach AI-Data/Projects klonen (nach Bestätigung).", coding.clone,
             _schema({"url": {"type": "string"}, "name": {"type": "string"}}, ["url"]), confirm_action="code_clone"),
        Tool("coding_pull", "Aktuellen Stand eines AI-Data-Repositories per fast-forward ziehen (nach Bestätigung).", coding.pull,
             _schema({"repo": {"type": "string"}}, ["repo"]), confirm_action="code_pull"),
        Tool("list_ssh_hosts", "Freigegebene SSH-Hosts auflisten (aus JUDE_SSH_HOSTS bzw. ~/.ssh/config).",
             ssh.allowed_hosts, _schema({})),
        Tool("ssh_run", "Befehl auf einem freigegebenen SSH-Host ausführen (schlüsselbasiert, nach Bestätigung).", ssh.run,
             _schema({"host": {"type": "string"}, "command": {"type": "string"}}, ["host", "command"]), confirm_action="ssh_command"),
        Tool("scp_transfer", "Datei per SCP zu/von einem freigegebenen Host übertragen (nach Bestätigung). direction: upload|download.", ssh.transfer,
             _schema({"host": {"type": "string"}, "direction": {"type": "string", "enum": ["upload", "download"]},
                      "remote_path": {"type": "string"}, "local_path": {"type": "string"}},
                     ["host", "direction", "remote_path", "local_path"]), confirm_action="scp_transfer"),
        Tool("coding_create_pr", "GitHub-Pull-Request erstellen.", coding.create_pr,
             _schema({"repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "draft": {"type": "boolean"}}, ["repo", "title", "body"]), confirm_action="code_pr"),
        Tool("ocr_file", "Deutsch/englischen Text aus Bild oder PDF lesen.", ocr.extract_path,
             _schema({"path": {"type": "string"}, "language": {"type": "string", "enum": ["deu", "eng", "deu+eng"]}}, ["path"]), untrusted=True),
        Tool("scrape_public_url", "Öffentliche URL sicher extrahieren; keine Logins oder Paywalls.", scraper.extract,
             _schema({"url": {"type": "string"}}, ["url"]), untrusted=True),
        Tool("calendar_list", "Lokal gespeicherte Termine auflisten.", calendar.list, _schema({})),
        Tool("ict_status", "Status des lokalen MT5-Demo-Stacks und ICT-Schedulers prüfen.", ict.stack_status, _schema({})),
        Tool("memory_list", "Lokale bestätigte Erinnerungen und Kandidaten auflisten.", memory.list,
             _schema({"status": {"type": "string", "enum": ["candidate", "active"]}, "limit": {"type": "integer"}})),
        Tool("memory_stats", "Statistik des lokalen Gedächtnisses und der freigegebenen Trainingsgespräche.",
             memory.training_stats, _schema({})),
        Tool("recall_conversations", "Frühere Gespräche zu einem Thema wiederfinden (episodisches Gedächtnis).",
             memory.recall, _schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"])),
    ]
    for tool in tools:
        registry.register(tool)


def register_context(registry: ToolRegistry, router=None, confirmations: ConfirmationQueue | None = None, **_) -> None:
    if router is not None:
        from services.fact_checker import FactCheckerService
        from services.meals import MealPlanService
        meals, ict, news, facts = MealPlanService(router), ICTService(), CryptoNewsService(), FactCheckerService(router)
        registry.register(Tool("meal_plan", "Günstigen Low-Carb-Plan mit PDF und Einkaufsliste erstellen.", meals.generate,
                               _schema({"days": {"type": "integer"}, "people": {"type": "integer"}})))
        registry.register(Tool("ict_analyse_live", "H4/H1/M1 gemeinsam über den lokalen MT5-Demo-Stack analysieren und Trading Card speichern.",
                               lambda symbol: ict.analyse_live(router, symbol), _schema({"symbol": {"type": "string", "enum": ["XAUUSD", "BTCUSD"]}}, ["symbol"])))
        registry.register(Tool("ict_training_status", "Walk-forward-Trainingsstand für XAUUSD und BTCUSD lesen.",
                               ict.training.status, _schema({})))
        registry.register(Tool("crypto_news_brief", "Aktuelle Crypto-News abrufen und journalistisch mit Quellen einordnen.",
                               lambda: router.call_with_fallback([{"role": "user", "content": news.journalist_prompt(news.fetch())}]).get("content", ""), _schema({})))
        registry.register(Tool("fact_check_url", "Bericht, Post oder Video per URL anhand unabhängiger Quellen prüfen.", facts.check,
                               _schema({"url": {"type": "string"}}, ["url"])))
    if confirmations is not None:
        # Der Registry die Bestätigungs-Warteschlange geben, damit sicherheitsrelevante
        # Tool-Aufrufe des Agenten automatisch zur Nutzerfreigabe vorgemerkt werden.
        registry.set_confirmations(confirmations)
        registry.register(Tool("request_confirmation", "Risikoreiche Aktion verbindlich zur Nutzerbestätigung vormerken.",
                               lambda action_type, summary, payload: json.dumps(confirmations.request(action_type, summary, payload), ensure_ascii=False),
                               _schema({"action_type": {"type": "string", "enum": ["mail_send", "mail_delete", "git_merge", "file_delete", "external_write", "calendar_create"]},
                                        "summary": {"type": "string"}, "payload": {"type": "object"}}, ["action_type", "summary", "payload"])))
        registry.register(Tool("request_mail_send", "E-Mail zur ausdrücklichen Bestätigung vormerken.",
                               lambda account, to, subject, body: confirmations.request("mail_send", f"E-Mail über {account} an {to}: {subject}", {"account": account, "to": to, "subject": subject, "body": body}),
                               _schema({"account": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["account", "to", "subject", "body"])))
        registry.register(Tool("request_mail_delete", "E-Mail-Löschung zur ausdrücklichen Bestätigung vormerken.",
                               lambda account, message_id, folder="INBOX": confirmations.request("mail_delete", f"E-Mail {message_id} aus {account}/{folder} löschen", {"account": account, "message_id": message_id, "folder": folder}),
                               _schema({"account": {"type": "string"}, "message_id": {"type": "string"}, "folder": {"type": "string"}}, ["account", "message_id"])))
        registry.register(Tool("request_calendar_event", "Termin als ICS zur ausdrücklichen Bestätigung vormerken.",
                               lambda title, starts_at, ends_at, description="", location="": confirmations.request("calendar_create", f"Termin erstellen: {title} am {starts_at}", {"title": title, "starts_at": starts_at, "ends_at": ends_at, "description": description, "location": location}),
                               _schema({"title": {"type": "string"}, "starts_at": {"type": "string"}, "ends_at": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, ["title", "starts_at", "ends_at"])))
        # Privilegierter Freibrief: normalerweise gesperrte Aktionen nach ausdrücklicher Nutzerfreigabe.
        registry.register(Tool("request_command",
                               "Systembefehl (auch normalerweise gesperrte Aktionen) zur ausdrücklichen Bestätigung vormerken. "
                               "Der genaue Befehl wird dir vor der Ausführung zur Freigabe angezeigt.",
                               lambda command, reason="": confirmations.request("shell_command", f"Befehl ausführen: {command}" + (f" — {reason}" if reason else ""), {"command": command}),
                               _schema({"command": {"type": "string"}, "reason": {"type": "string"}}, ["command"])))
        registry.register(Tool("request_external_write",
                               "Datei außerhalb von AI-Data schreiben – zur ausdrücklichen Bestätigung vormerken.",
                               lambda path, content: confirmations.request("external_write", f"Datei außerhalb AI-Data schreiben: {path}", {"path": path, "content": content}),
                               _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])))


def register_documents(registry: ToolRegistry, documents) -> None:
    """Werkzeuge für lokales Dokumentwissen (RAG)."""
    registry.register(Tool("ingest_document", "Ein Dokument (Text/PDF) unter AI-Data ins Wissen einlesen.",
                           documents.ingest, _schema({"path": {"type": "string"}}, ["path"])))
    registry.register(Tool("search_documents", "Eingelesene Dokumente semantisch durchsuchen und passende Stellen erhalten.",
                           documents.search, _schema({"query": {"type": "string"}, "top_k": {"type": "integer"}}, ["query"]),
                           untrusted=True))
    registry.register(Tool("list_documents", "Eingelesene Dokumente auflisten.", documents.list_documents, _schema({})))
    registry.register(Tool("forget_document", "Ein Dokument aus dem Wissen entfernen.",
                           documents.forget_document, _schema({"path": {"type": "string"}}, ["path"])))


def register_backup(registry: ToolRegistry, backup) -> None:
    registry.register(Tool("run_backup", "Sofort eine Sicherung von Datenbank und Konfiguration erstellen.",
                           backup.run, _schema({})))
    registry.register(Tool("list_backups", "Vorhandene Sicherungen auflisten.", backup.list, _schema({})))


def register_scheduler(registry: ToolRegistry, scheduler) -> None:
    """Werkzeuge für zeitgesteuerte, proaktive Aufgaben."""
    registry.register(Tool("list_scheduled_tasks", "Geplante (zeitgesteuerte) Aufgaben auflisten.",
                           scheduler.list, _schema({})))
    registry.register(Tool("create_scheduled_task",
                           "Zeitgesteuerte Aufgabe anlegen, damit Jude proaktiv handelt. Entweder täglich um 'at' (HH:MM) "
                           "oder alle 'every_minutes'. action_type: 'prompt' (Anfrage an dich selbst ausführen), "
                           "'briefing' (Kurzbriefing) oder 'tool' (Werkzeug aufrufen).",
                           lambda name, action_type, at=None, every_minutes=None, prompt=None, tool=None, tool_args=None, speak=True:
                               scheduler.create(name, action_type, at=at, every_minutes=every_minutes,
                                                prompt=prompt, tool=tool, tool_args=tool_args, speak=speak),
                           _schema({"name": {"type": "string"}, "action_type": {"type": "string", "enum": ["prompt", "briefing", "tool"]},
                                    "at": {"type": "string"}, "every_minutes": {"type": "integer"},
                                    "prompt": {"type": "string"}, "tool": {"type": "string"},
                                    "tool_args": {"type": "object"}, "speak": {"type": "boolean"}},
                                   ["name", "action_type"])))
    registry.register(Tool("delete_scheduled_task", "Geplante Aufgabe entfernen.",
                           scheduler.delete, _schema({"task_id": {"type": "string"}}, ["task_id"])))


def register_team(registry: ToolRegistry, team) -> None:
    """Werkzeuge zum Verwalten und Einsetzen benannter Sub-Agenten ("Mitarbeiter")."""
    registry.register(Tool("list_sub_agents", "Vorhandene Sub-Agenten (Mitarbeiter) mit Rolle und Skills auflisten.",
                           team.list, _schema({})))
    registry.register(Tool("list_available_skills", "Verfügbare Skills/Werkzeuge auflisten, die ein Sub-Agent bekommen kann.",
                           team.available_skills, _schema({})))
    registry.register(Tool("delegate_to_agent", "Einem benannten Sub-Agenten eine Aufgabe übergeben und dessen Ergebnis erhalten.",
                           lambda name, task: team.run(name, task),
                           _schema({"name": {"type": "string"}, "task": {"type": "string"}}, ["name", "task"])))
    registry.register(Tool("create_sub_agent",
                           "Lege einen benannten Sub-Agenten (Mitarbeiter) an, wenn ein abgegrenzter, wiederkehrender "
                           "Aufgabenbereich davon profitiert (z.B. 'ServerAdmin' für SSH-Wartung, 'Rechercheur' fürs Web). "
                           "Gib Name, Rolle und die passende Skill-Liste an; danach kannst du ihm mit delegate_to_agent "
                           "Aufgaben übergeben. Anlegen erfolgt nach ausdrücklicher Bestätigung.",
                           lambda name, role, skills, model=None: team.create(name, role, skills, model),
                           _schema({"name": {"type": "string"}, "role": {"type": "string"},
                                    "skills": {"type": "array", "items": {"type": "string"}},
                                    "model": {"type": "string"}}, ["name", "role", "skills"]),
                           confirm_action="create_agent"))
