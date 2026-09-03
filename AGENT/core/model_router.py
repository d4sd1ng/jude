"""Konfigurierbare Modellauswahl und Provider-Adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from contextlib import suppress
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from services.database import connection

logger = logging.getLogger(__name__)

# Provider, die gerade mit 429 um sich werfen, 15 Minuten überspringen –
# OpenAI stand einen ganzen Tag auf 429 und kostete in jeder Kette einen
# vergeblichen Versuch samt Wartezeit.
_PROVIDER_429: dict[str, dict] = {}
_SPERRDAUER_S = 900

# Muster für Fehler, die sich NIE von selbst lösen (leeres Guthaben, gesperrter
# Account) – anders als 429 (Rate-Limit, oft in Sekunden vorbei) lohnt sich
# hier kein zweiter Versuch. Gemessen 01.09.: ohne das hämmerte jeder einzelne
# Mitarbeiterlauf bis zu 10 Minuten lang alle drei Anthropic-Modelle frisch,
# weil ein 400 ("credit balance too low") die 429-Sperre nie auslöste.
# "requires a subscription" kam am 02.09. von Ollama Cloud (HTTP 402) fuer die
# grossen Modelle – ebenfalls nichts, was ein zweiter Versuch loest.
_DAUERHAFT_MUSTER = re.compile(
    r"credit balance|insufficient_quota|credit_balance_exhausted|billing"
    r"|requires a subscription", re.IGNORECASE)


def _ist_dauerhafter_fehler(exc: Exception) -> bool:
    return bool(_DAUERHAFT_MUSTER.search(str(exc)))


def _tagesende_ts() -> float:
    """Zeitstempel der nächsten lokalen Mitternacht."""
    jetzt = datetime.now().astimezone()
    mitternacht = (jetzt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return mitternacht.timestamp()


def _provider_429_gesperrt(provider: str) -> bool:
    eintrag = _PROVIDER_429.get(provider)
    return bool(eintrag and eintrag.get("bis", 0) > time.time() and eintrag.get("zaehler", 0) >= 2)


def _provider_429_merken(provider: str, war_429: bool, sofort: bool = False) -> None:
    if not war_429:
        _PROVIDER_429.pop(provider, None)
        return
    eintrag = _PROVIDER_429.setdefault(provider, {"zaehler": 0, "bis": 0, "tagessperre": False})
    eintrag["zaehler"] = 2 if sofort else eintrag["zaehler"] + 1
    if eintrag["zaehler"] < 2:
        return
    # Leeres Guthaben lädt sich nicht von selbst wieder auf: einmal geprüft
    # reicht für den Tag. Ein 429 (Rate-Limit) ist dagegen oft nach Minuten
    # vorbei und bleibt bei der kurzen Sperre.
    bis = _tagesende_ts() if sofort else time.time() + _SPERRDAUER_S
    if bis <= eintrag["bis"]:
        return                          # laufende Sperre nie verkürzen
    eintrag["bis"] = bis
    if sofort and not eintrag.get("tagessperre"):
        eintrag["tagessperre"] = True
        logger.warning(
            "Provider %s bis Mitternacht gesperrt (kein Guthaben/Quota). "
            "Nach dem Aufladen: Jude neu starten oder provider_sperre_aufheben(%r).",
            provider, provider)


def provider_gesperrt(provider: str) -> bool:
    """Ist dieser Provider gerade gesperrt (429 oder leeres Guthaben)?"""
    return _provider_429_gesperrt(provider)


def provider_sperre_aufheben(provider: str | None = None) -> None:
    """Sperre eines Providers (oder aller) manuell aufheben – z. B. nach dem Aufladen."""
    if provider is None:
        _PROVIDER_429.clear()
    else:
        _PROVIDER_429.pop(provider, None)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    model_name: str
    cost_input: float
    cost_cached_input: float
    cost_output: float
    latency: int
    max_tokens: int
    context_length: int
    tags: list[str]
    priority: int = 5
    weight: float = 1.0
    reasoning_effort: str | None = None
    think: bool | None = None
    # Wie lange Ollama das Modell nach dem Aufruf im VRAM behält. Ollamas
    # Standard sind 5 Minuten; danach kostet der nächste Aufruf das erneute
    # Laden. Gemessen 02.09.2026 für qwen3:8b: kalt 7,50 s Ladezeit (8,93 s
    # gesamt), warm 0,33 s (1,37 s gesamt). Zahl = Sekunden, -1 = dauerhaft
    # halten; als String nur mit Einheit ("60s"), sonst lehnt Ollama mit
    # "time: missing unit in duration" ab.
    keep_alive: str | int | None = None


class ComplexityEstimator:
    COMPLEX_KEYWORDS = (
        "analysiere", "vergleiche", "ausführlich", "programmiere", "debugge",
        "wissenschaftlich", "architect", "implement", "analyse", "compare",
    )

    @classmethod
    def estimate(cls, prompt: str) -> int:
        words = len(prompt.split())
        score = 1 + (words > 10) + (words > 40) + (words > 100) * 2
        score += min(3, sum(keyword in prompt.lower() for keyword in cls.COMPLEX_KEYWORDS))
        score += "?" in prompt
        return max(1, min(10, int(score)))


class ProviderAdapter(ABC):
    @abstractmethod
    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Rufe den Provider auf und liefere eine normalisierte Assistentenantwort."""


def _werkzeug_text(content: Any) -> str:
    """Textform eines Werkzeug-Ergebnisses.

    Bild-Anlagen (vorlage_ansehen) sind Blocklisten, die nur der Anthropic-
    Adapter als Bilder senden kann; alle anderen Anbieter bekommen den
    Textersatz, sonst landet Base64-Müll im Prompt."""
    if isinstance(content, dict) and "_bildbloecke" in content:
        return str(content.get("text")
                   or "[Bild-Anlage – dieses Modell kann sie nicht sehen]")
    return str(content or "")


class OllamaAdapter(ProviderAdapter):
    def __init__(self, base_url: str, timeout_seconds: int = 60, num_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.retries = num_retries

    def available_models(self) -> set[str]:
        response = requests.get(f"{self.base_url}/api/tags", timeout=3)
        response.raise_for_status()
        return {item["name"] for item in response.json().get("models", [])}

    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        messages = [{**m, "content": _werkzeug_text(m.get("content"))}
                    if isinstance(m.get("content"), dict) else m for m in messages]
        payload: dict[str, Any] = {
            "model": spec.model_name,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": spec.context_length, "num_predict": spec.max_tokens},
        }
        if tools:
            payload["tools"] = tools
        if spec.think is not None:
            payload["think"] = spec.think
        if spec.keep_alive is not None:
            payload["keep_alive"] = spec.keep_alive
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
                if response.status_code >= 400:
                    # Ollama begruendet Ablehnungen im Rumpf; ohne diesen Text war
                    # aus "400 Bad Request" nicht erkennbar, was am Verlauf stoert.
                    raise ValueError(f"HTTP {response.status_code}: {response.text[:400]}")
                data = response.json()
                message = data.get("message", {})
                message["usage"] = {
                    "input_tokens": data.get("prompt_eval_count", 0),
                    "output_tokens": data.get("eval_count", 0),
                }
                if isinstance(message.get("content"), str):
                    # Qwen3 & Co. liefern Denkblöcke im Klartext; die gehören nicht in die Antwort.
                    message["content"] = re.sub(r"<think>.*?</think>\s*", "", message["content"], flags=re.S)
                return message
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
        raise RuntimeError(f"Ollama-Aufruf fehlgeschlagen: {last_error}")


class OpenAIAdapter(ProviderAdapter):
    def __init__(self, base_url: str, timeout_seconds: int = 30, **_: Any):
        self.url = f"{base_url.rstrip('/')}/responses"
        self.timeout = timeout_seconds

    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        input_items: list[dict] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                input_items.append({"type": "function_call_output", "call_id": message.get("tool_call_id", ""),
                                    "output": _werkzeug_text(message.get("content", ""))})
                continue
            if message.get("content"):
                input_items.append({"role": role, "content": _werkzeug_text(message["content"])})
            if role == "assistant":
                for call in message.get("tool_calls", []):
                    function = call["function"]
                    arguments = function.get("arguments", {})
                    input_items.append({"type": "function_call", "call_id": call.get("id", ""),
                                        "name": function["name"],
                                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)})
        payload: dict[str, Any] = {"model": spec.model_name, "input": input_items,
                                   "max_output_tokens": spec.max_tokens, "store": False,
                                   "service_tier": os.getenv("OPENAI_SERVICE_TIER", "default")}
        if spec.reasoning_effort:
            payload["reasoning"] = {"effort": spec.reasoning_effort, "context": "current_turn"}
        if tools:
            payload["tools"] = [{"type": "function", "name": item["function"]["name"],
                                 "description": item["function"]["description"],
                                 "parameters": item["function"]["parameters"]} for item in tools]
        response = requests.post(
            self.url, json=payload,
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, timeout=self.timeout,
        )
        if not response.ok:
            # Der bare raise_for_status() zeigt nur "400 Bad Request for url: …" –
            # der eigentliche Grund (z. B. "insufficient_quota") steht im Body und
            # ging bisher verloren, wodurch die Dauerhaft-Fehler-Erkennung nie griff.
            raise requests.HTTPError(f"{response.status_code} {response.reason} für {self.url}: "
                                     f"{response.text[:400]}", response=response)
        data = response.json()
        text_parts, calls = [], []
        for item in data.get("output", []):
            if item.get("type") == "message":
                text_parts.extend(part.get("text", "") for part in item.get("content", [])
                                  if part.get("type") in {"output_text", "text"})
            elif item.get("type") == "function_call":
                calls.append({"id": item.get("call_id") or item.get("id", ""), "type": "function",
                              "function": {"name": item["name"], "arguments": item.get("arguments", "{}")}})
        result: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
        if calls:
            result["tool_calls"] = calls
        usage = data.get("usage", {})
        cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
        result["usage"] = {"input_tokens": usage.get("input_tokens", 0),
                           "uncached_input_tokens": max(0, usage.get("input_tokens", 0) - cached),
                           "cached_input_tokens": cached, "output_tokens": usage.get("output_tokens", 0),
                           "reasoning_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
                           "service_tier": data.get("service_tier", "standard"), "tariff": "standard"}
        return result


class OpenAICompatAdapter(ProviderAdapter):
    """Chat-Completions-Format für OpenAI-kompatible Anbieter (OpenRouter, Groq, DeepSeek …)."""

    def __init__(self, base_url: str, api_key_env: str, timeout_seconds: int = 60,
                 extra_headers: dict[str, str] | None = None, **_: Any):
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.api_key_env = api_key_env
        self.timeout = timeout_seconds
        self.extra_headers = extra_headers or {}

    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        # Tief kopieren: dict(message) waere nur eine flache Kopie – die
        # tool_calls darin sind dieselben Objekte wie im echten Gespraechsverlauf.
        # Das Umschreiben der Argumente in JSON-Strings traf damit das Original,
        # und jede nachfolgende Fallback-Stufe (Ollama, Anthropic) wies den so
        # veraenderten Verlauf mit HTTP 400 ab.
        outgoing: list[dict] = []
        for message in messages:
            if isinstance(message.get("content"), dict):
                message = {**message, "content": _werkzeug_text(message["content"])}
            item = copy.deepcopy(dict(message))
            for call in item.get("tool_calls", []) or []:
                arguments = call.get("function", {}).get("arguments")
                if not isinstance(arguments, str):
                    call["function"]["arguments"] = json.dumps(arguments or {})
                # Ollama laesst 'type' und 'id' weg, OpenAI-kompatible Anbieter
                # verlangen beides. Ohne das brach der Wechsel von einer lokalen
                # Stufe zurueck in die Cloud mit HTTP 400 ab.
                call.setdefault("type", "function")
                if not call.get("id"):
                    call["id"] = f"call_{uuid.uuid4().hex[:12]}"
            item.pop("usage", None)
            outgoing.append(item)
        payload: dict[str, Any] = {"model": spec.model_name, "messages": outgoing,
                                   "max_tokens": spec.max_tokens, "stream": False}
        if tools:
            payload["tools"] = tools
        response = requests.post(
            self.url, json=payload,
            headers={"Authorization": f"Bearer {os.environ[self.api_key_env]}", **self.extra_headers},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            # Ohne den Rumpf ist "400 Bad Request" nicht diagnostizierbar.
            raise ValueError(f"HTTP {response.status_code}: {response.text[:400]}")
        data = response.json()
        message = data.get("choices", [{}])[0].get("message", {})
        result: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("tool_calls"):
            # OpenAI-kompatible Anbieter liefern die Argumente als JSON-String.
            # So gespeichert bricht jeder spaetere Ollama-Aufruf mit
            # "Value looks like object, but can't find closing '}' symbol" ab.
            # Deshalb hier einmal zurueck in ein Objekt wandeln.
            calls = copy.deepcopy(message["tool_calls"])
            for call in calls:
                arguments = call.get("function", {}).get("arguments")
                if isinstance(arguments, str):
                    try:
                        call["function"]["arguments"] = json.loads(arguments or "{}")
                    except json.JSONDecodeError:
                        call["function"]["arguments"] = {}
            result["tool_calls"] = calls
        usage = data.get("usage", {}) or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        result["usage"] = {"input_tokens": usage.get("prompt_tokens", 0),
                           "uncached_input_tokens": max(0, usage.get("prompt_tokens", 0) - cached),
                           "cached_input_tokens": cached,
                           "output_tokens": usage.get("completion_tokens", 0), "tariff": "standard"}
        return result


class AnthropicAdapter(ProviderAdapter):
    def __init__(self, base_url: str, timeout_seconds: int = 30, **_: Any):
        self.url = f"{base_url.rstrip('/')}/messages"
        self.timeout = timeout_seconds

    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        system = "\n".join(str(m["content"]) for m in messages if m["role"] == "system")
        chat: list[dict] = []
        for message in messages:
            if message["role"] == "user":
                chat.append({"role": "user", "content": message.get("content", "")})
            elif message["role"] == "assistant":
                blocks: list[dict] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls", []):
                    function = call["function"]
                    blocks.append({"type": "tool_use", "id": call["id"], "name": function["name"],
                                   "input": function["arguments"]})
                chat.append({"role": "assistant", "content": blocks or ""})
            elif message["role"] == "tool":
                inhalt = message["content"]
                # Bild-Anlagen als echte Bildblöcke – Haiku & Co. SEHEN damit
                # die Vorlagen, statt eine Textbeschreibung zu raten.
                if isinstance(inhalt, dict) and "_bildbloecke" in inhalt:
                    inhalt = inhalt["_bildbloecke"]
                chat.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": message["tool_call_id"], "content": inhalt,
                }]})
        payload: dict[str, Any] = {"model": spec.model_name, "messages": chat, "max_tokens": spec.max_tokens,
                                   # Auto-Caching: Cache-Punkt auf dem letzten cachebaren Block. Ohne das
                                   # zahlte jede Werkzeug-Runde den kompletten Verlauf (Briefings!) erneut
                                   # voll – gemessen 78k Input für eine Dokumentenprüfung. Cache-Reads
                                   # kosten 10 %; die Verbrauchsfelder liest der Adapter bereits aus.
                                   "cache_control": {"type": "ephemeral"},
                                   "service_tier": os.getenv("ANTHROPIC_SERVICE_TIER", "standard_only")}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
                for t in tools
            ]
        response = requests.post(self.url, json=payload, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, timeout=self.timeout)
        if not response.ok:
            # Body statt bare raise_for_status(): "Your credit balance is too low"
            # steckt nur dort, nicht in der generischen "400 Bad Request"-Meldung –
            # ohne den Text griff die Dauerhaft-Fehler-Sperre nie.
            raise requests.HTTPError(f"{response.status_code} {response.reason} für {self.url}: "
                                     f"{response.text[:400]}", response=response)
        data = response.json()
        text = "".join(block.get("text", "") for block in data["content"] if block["type"] == "text")
        calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": b["input"]}}
                 for b in data["content"] if b["type"] == "tool_use"]
        result: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            result["tool_calls"] = calls
        usage = data.get("usage", {})
        result["usage"] = {"input_tokens": usage.get("input_tokens", 0),
                           "uncached_input_tokens": usage.get("input_tokens", 0),
                           "cached_input_tokens": usage.get("cache_read_input_tokens", 0),
                           "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                           "output_tokens": usage.get("output_tokens", 0),
                           "service_tier": usage.get("service_tier", "standard"),
                           "tariff": f"{usage.get('service_tier', 'standard')}:{usage.get('speed', 'standard')}"}
        return result


class GoogleAdapter(ProviderAdapter):
    def __init__(self, base_url: str, timeout_seconds: int = 30, **_: Any):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def call(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None) -> dict:
        contents = []
        system_parts = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content", "")))
                continue
            if role == "tool":
                parts = [{"functionResponse": {"name": message.get("name", "tool"),
                                                "response": {"result": _werkzeug_text(message.get("content", ""))}}}]
                contents.append({"role": "user", "parts": parts})
                continue
            parts = []
            if message.get("content"):
                parts.append({"text": str(message["content"])})
            for call in message.get("tool_calls", []):
                function = call["function"]
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    import json
                    arguments = json.loads(arguments)
                parts.append({"functionCall": {"name": function["name"], "args": arguments}})
            if parts:
                contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        url = f"{self.base_url}/models/{spec.model_name}:generateContent?key={os.environ['GOOGLE_API_KEY']}"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": spec.max_tokens}}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [{"name": item["function"]["name"],
                "description": item["function"]["description"], "parameters": item["function"]["parameters"]} for item in tools]}]
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        parts = data["candidates"][0]["content"].get("parts", [])
        result: dict[str, Any] = {"role": "assistant", "content": "".join(item.get("text", "") for item in parts)}
        calls = [{"id": f"google-{index}", "type": "function", "function": {
            "name": item["functionCall"]["name"], "arguments": item["functionCall"].get("args", {})}}
            for index, item in enumerate(parts) if "functionCall" in item]
        if calls:
            result["tool_calls"] = calls
        usage = data.get("usageMetadata", {})
        cached = usage.get("cachedContentTokenCount", 0)
        result["usage"] = {"input_tokens": usage.get("promptTokenCount", 0),
                           "uncached_input_tokens": max(0, usage.get("promptTokenCount", 0) - cached),
                           "cached_input_tokens": cached, "output_tokens": usage.get("candidatesTokenCount", 0),
                           "reasoning_tokens": usage.get("thoughtsTokenCount", 0),
                           "service_tier": "standard", "tariff": "standard"}
        return result


class ModelRouter:
    def __init__(self, config_path: str | Path | None = None):
        self.last_context: dict | None = None
        path = Path(config_path) if config_path else Path(__file__).parents[1] / "config" / "models.yaml"
        with path.open(encoding="utf-8") as handle:
            self.config = yaml.safe_load(handle)
        self.models = {
            name: ModelSpec(
                name=name, provider=data["provider"], model_name=data["model_name"],
                cost_input=float(data["cost_per_1k_input"]),
                cost_cached_input=float(data.get("cost_per_1k_cached_input", data["cost_per_1k_input"])),
                cost_output=float(data["cost_per_1k_output"]),
                latency=int(data["avg_latency_ms"]), max_tokens=int(data["max_tokens"]),
                context_length=int(data["context_length"]), tags=list(data["tags"]),
                priority=int(data.get("priority", 5)), weight=float(data.get("weight", 1.0)),
                reasoning_effort=data.get("reasoning_effort"), think=data.get("think"),
                keep_alive=data.get("keep_alive"),
            ) for name, data in self.config["models"].items()
        }
        self.router_cfg = self.config["router"]
        providers = self.config.get("providers", {})
        self.adapters: dict[str, ProviderAdapter] = {
            "ollama": OllamaAdapter(**providers["ollama"]),
            "openai": OpenAIAdapter(**providers["openai"]),
            "anthropic": AnthropicAdapter(**providers["anthropic"]),
            "google": GoogleAdapter(**providers["google"]),
        }
        for name, settings in providers.items():
            if name not in self.adapters and settings.get("api_key_env"):
                self.adapters[name] = OpenAICompatAdapter(**settings)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with connection() as db:
            row = db.execute("SELECT COALESCE(SUM(cost_usd),0) AS total FROM model_usage WHERE substr(created_at,1,7)=?", (month,)).fetchone()
        self.budget_used = float(row["total"])
        self.budget_limit = float(os.getenv(
            "JUDE_CLOUD_BUDGET_USD", self.router_cfg["budget_limit_monthly_usd"]
        ))
        self.request_cost_limit = float(os.getenv(
            "JUDE_CLOUD_REQUEST_LIMIT_USD", self.router_cfg.get("request_cost_limit_usd", 1.0)
        ))
        self.paid_models_enabled = os.getenv("JUDE_PAID_MODELS_ENABLED", "true").casefold() in {
            "1", "true", "yes", "ja", "on",
        }
        self._call_lock = threading.RLock()
        self._letztes_lokales: ModelSpec | None = None

    def vorwaermen(self, intervall_s: int = 60) -> None:
        """Dauerhaft gehaltene Ollama-Modelle im Hintergrund ins VRAM laden.

        Ohne das ist erst der erste Aufruf nach dem Start warm und zahlt die
        7,5 s Ladezeit – ausgerechnet dann, wenn Tino gerade etwas fragt.
        Der Waechter laedt nur nach, wenn Ollama gar nichts geladen hat: zwei
        8B-Modelle passen nicht zusammen in 8 GB, ein Nachladen gegen ein
        gerade benutztes Modell wuerde dieses nur rauswerfen. Zurueck kommt
        das zuletzt benutzte Modell, beim Start das Standardmodell."""
        adapter = self.adapters.get("ollama")
        specs = [spec for spec in self.models.values()
                 if spec.provider == "ollama" and spec.keep_alive == -1]
        if not adapter or not specs:
            return
        standard = self.models.get(self.router_cfg.get("standard_model", ""))

        def _wunschmodell() -> ModelSpec:
            for kandidat in (self._letztes_lokales, standard):
                if kandidat in specs:
                    return kandidat
            return specs[0]

        def _geladen() -> list[str]:
            antwort = requests.get(f"{adapter.base_url}/api/ps", timeout=5)
            antwort.raise_for_status()
            return [m.get("name", "") for m in antwort.json().get("models", [])]

        def _laden(spec: ModelSpec) -> None:
            # Leere Nachrichtenliste laedt das Modell, ohne zu generieren.
            requests.post(f"{adapter.base_url}/api/chat", timeout=300, json={
                "model": spec.model_name, "messages": [], "stream": False,
                "keep_alive": spec.keep_alive,
                "options": {"num_ctx": spec.context_length},
            }).raise_for_status()
            logger.info("Modell %s vorgewaermt.", spec.model_name)

        def _schleife() -> None:
            while True:
                try:
                    if not _geladen():
                        _laden(_wunschmodell())
                except Exception as exc:                    # nur Komfort, nie kritisch
                    logger.warning("Vorwaermen fehlgeschlagen: %s", exc)
                time.sleep(intervall_s)

        threading.Thread(target=_schleife, name="modell-vorwaermen", daemon=True).start()

    # Handlungsabsicht: Verben, die auf eine auszuführende Aktion (Werkzeug) deuten,
    # nicht auf reines Erklären/Plaudern.
    _ACTIONABLE = re.compile(
        r"\b(führ(e|st)?\s+.*\s*aus|ausführen|starte|stoppe|erstelle|erzeuge|generiere|rendere|"
        r"prüfe|überprüfe|klone|committe|pushe?|pulle?|deploy|sende|schicke|lösche|entferne|"
        r"installiere|aktualisiere|richte\s+.*\bein|kümmere?\s+dich|öffne|lade\b|hol(e)?\b|"
        r"schalte|zeig(e)?\s+mir\s+(die|den|das|aktuelle)|liste|suche|schreibe\s+.*\b(datei|in)\b|"
        r"ssh|verbinde|run|execute|clone|create|delete|send|deploy|fetch|list|"
        # Betriebsfragen (Abnahme, Kontingent, Team-Ergebnisse) brauchen Werkzeuge –
        # ohne diese Begriffe blieb dolphin dran und schwafelte statt nachzusehen.
        r"abnahme|abnehmen|abzunehmen|freigeben|freigabe|approve|review|"
        r"kontingent|token|budget|verbraucht|verbrannt|burned|"
        r"team|mitarbeiter|austausch|generiert|generated|produziert|erzeugt|erledigt)\b",
        re.IGNORECASE,
    )

    def _is_actionable(self, prompt: str) -> bool:
        return bool(self._ACTIONABLE.search(prompt))

    def estimate_complexity(self, prompt: str) -> int:
        return ComplexityEstimator.estimate(prompt)

    @staticmethod
    def task_type(prompt: str) -> str:
        value = prompt.casefold()
        groups = {
            "code": ("code", "python", "javascript", "typescript", "debug", "test", "implement", "repository", "git "),
            "trading": ("ict", "smc", "trading", "ohlcv", "xau", "btc", "kill zone", "fvg"),
            "research": ("recherch", "quelle", "beleg", "fake", "news", "wissenschaft", "analyse"),
            "document": ("pdf", "dokument", "ocr", "bericht", "zusammenfassung"),
            "home": ("home assistant", "licht", "alexa", "growcontroller"),
            "medien": ("bild", "generier", "render", "blender", "foto", "zeichne", "male ", "3d", "szene",
                       "grafik", "icon", "logo", "bearbeite das bild", "beschreib", "was ist auf dem bild", "analysiere das bild", "erkenne"),
        }
        for kind, terms in groups.items():
            if any(term in value for term in terms):
                return kind
        return "allgemein"

    def _learned_adjustment(self, model: ModelSpec, task_type: str) -> float:
        with connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n,AVG(user_feedback) AS score FROM route_decisions "
                "WHERE task_type=? AND final_model=? AND user_feedback IS NOT NULL",
                (task_type, model.name),
            ).fetchone()
        return float(row["score"] or 0) * 4 if int(row["n"]) >= 3 else 0.0

    def _provider_enabled(self, provider: str) -> bool:
        if provider == "ollama":
            return True
        key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}.get(
            provider) or self.config.get("providers", {}).get(provider, {}).get("api_key_env")
        return key is None or (self.paid_models_enabled and bool(os.getenv(key)))

    def select_model(self, prompt: str, allow_uncensored: bool = False, force_local: bool = False,
                     needs_tools: bool = False) -> ModelSpec:
        task_type = self.task_type(prompt)
        candidates = [m for m in self.models.values() if self._provider_enabled(m.provider)]
        if force_local or self.budget_used >= self.budget_limit or self.router_cfg.get("local_first", True):
            candidates = [m for m in candidates if "lokal" in m.tags]
        if allow_uncensored:
            candidates = [m for m in candidates if "unzensiert" in m.tags]
        elif needs_tools and (task_type != "allgemein" or self._is_actionable(prompt)):
            # Werkzeuglastige ODER klar handlungsorientierte Anfragen ("führe aus",
            # "erstelle", "klone" …) erzwingen ein Tools-fähiges Modell, damit der
            # Assistent handelt statt nur zu erklären. Reines Plaudern bleibt beim Standardmodell.
            tool_capable = [m for m in candidates if "tools" in m.tags]
            candidates = tool_capable or candidates
        if not candidates:
            raise ValueError("Kein passendes und aktiviertes Modell konfiguriert.")
        standard_name = self.router_cfg.get("standard_model")
        # Nur echte Werkzeug-/Code-/Medien-Anfragen dürfen das Tools-Modell (qwen) zur
        # Basis machen. Alles andere – Plaudern, Analyse, Kreatives – bleibt beim
        # unzensierten Standardmodell (dolphin).
        code_or_tools = (task_type in ("code", "medien")
                         or (needs_tools and (task_type != "allgemein" or self._is_actionable(prompt))))

        def score(model: ModelSpec) -> float:
            is_local = "lokal" in model.tags
            local_preference = 2 if is_local and self.router_cfg.get("prefer_local", True) else 0
            task_fit = 7 if task_type in model.tags else 4 if task_type == "allgemein" and "allgemein" in model.tags else 0
            uncensored_default = 2 if "unzensiert" in model.tags and not code_or_tools else 0
            standard_bonus = 6 if model.name == standard_name and not code_or_tools else 0
            return (local_preference + task_fit + uncensored_default + standard_bonus + model.priority
                    - model.latency / 300
                    - (model.cost_input + model.cost_output) * 100 + self._learned_adjustment(model, task_type)) * model.weight

        return max(candidates, key=score)

    def _resolve_fallbacks(self, selected: ModelSpec, allow_uncensored: bool,
                           needs_tools: bool = False) -> list[ModelSpec]:
        result = [selected]
        for entry in self.router_cfg.get("fallback_chain", []):
            matches = ([m for m in self.models.values() if entry[5:] in m.tags] if entry.startswith("tags:")
                       else [self.models[entry]] if entry in self.models else [])
            for model in sorted(matches, key=lambda item: item.priority, reverse=True):
                if model not in result and self._provider_enabled(model.provider):
                    # Ein Modell ohne 'tools' kann eine Werkzeugkette nicht
                    # bedienen – es beschreibt den Aufruf hoechstens. Gemessen:
                    # ein Lauf fiel auf dolphin3 zurueck und brauchte 679
                    # Sekunden fuer nichts. Solche Stufen gehoeren nicht in die
                    # Kette, sobald Werkzeuge im Spiel sind.
                    if needs_tools and "tools" not in model.tags:
                        continue
                    if not allow_uncensored or "unzensiert" in model.tags:
                        result.append(model)
        return result

    def _cloud_affordable(self, spec: ModelSpec, messages: list[dict]) -> bool:
        if spec.provider == "ollama":
            return True
        approximate_input = sum(len(str(item.get("content", ""))) for item in messages) // 4
        approximate_output = min(spec.max_tokens, 4096)
        estimate = (approximate_input * spec.cost_input + approximate_output * spec.cost_output) / 1000
        return estimate <= self.request_cost_limit and self.budget_used + estimate <= self.budget_limit

    @staticmethod
    def _obviously_inadequate(response: dict) -> bool:
        text = str(response.get("content", "")).strip().casefold()
        if response.get("tool_calls"):
            return False
        markers = ("ich kann dabei nicht", "i can't help", "als ki kann ich", "keine informationen", "fehler:")
        return not text or any(marker in text for marker in markers)

    def _local_quality_check(self, prompt: str, response: dict, complexity: int) -> bool:
        if self._obviously_inadequate(response):
            return False
        if complexity < int(self.router_cfg.get("quality_judge_min_complexity", 6)) or response.get("tool_calls"):
            return True
        judge = next((model for model in self.models.values() if "routing_judge" in model.tags), None)
        if not judge:
            return True
        request = (
            "Bewerte, ob die Antwort die Anfrage fachlich ausreichend, vollständig und konkret beantwortet. "
            "Antworte nur als JSON {\"adequate\":true|false,\"reason\":\"kurz\"}.\n"
            f"ANFRAGE:\n{prompt[:12000]}\nANTWORT:\n{str(response.get('content', ''))[:18000]}"
        )
        try:
            judged = self.call_llm(judge, [{"role": "user", "content": request}], request_id=None)
            parsed = json.loads(str(judged.get("content", "")).strip().removeprefix("```json").removesuffix("```").strip())
            return bool(parsed.get("adequate"))
        except Exception as exc:
            logger.warning("Lokale Routing-Qualitätsprüfung fehlgeschlagen: %s", exc)
            return True

    def call_llm(self, spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None,
                 request_id: str | None = None) -> dict:
        with self._call_lock:
            supported_tools = tools if "tools" in spec.tags else None
            if spec.provider == "ollama":
                # Wer zuletzt dran war, ist am ehesten gleich wieder dran. Der
                # Waechter holt genau dieses Modell zurueck – sonst laege nach
                # einem Dolphin-Gespraech plotzlich qwen3 im VRAM.
                self._letztes_lokales = spec
            response = self.adapters[spec.provider].call(spec, messages, supported_tools)
            usage = response.pop("usage", {}) or {}
            input_tokens, output_tokens = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            uncached = int(usage.get("uncached_input_tokens", input_tokens))
            cached = int(usage.get("cached_input_tokens", 0))
            cache_write = int(usage.get("cache_write_tokens", 0))
            reasoning = int(usage.get("reasoning_tokens", 0))
            multiplier = float(usage.get("cost_multiplier", 1.0))
            cost = ((uncached * spec.cost_input) + (cached * spec.cost_cached_input)
                    + (cache_write * spec.cost_input * 1.25) + (output_tokens * spec.cost_output)) / 1000 * multiplier
            self.budget_used += cost
            # Kontextfuellung des letzten Aufrufs: prompt_eval_count ist die
            # exakte Tokenzahl des gesamten Gespraechs, wie es beim Modell ankam.
            if input_tokens:
                self.last_context = {"model": spec.name, "input_tokens": input_tokens,
                                     "context_length": spec.context_length}
            with connection() as db:
                db.execute("INSERT INTO model_usage(created_at,provider,model,input_tokens,output_tokens,cached_input_tokens,"
                           "cache_write_tokens,reasoning_tokens,service_tier,tariff,cost_multiplier,request_id,cost_usd) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), spec.provider, spec.model_name, input_tokens, output_tokens,
                            cached, cache_write, reasoning, str(usage.get("service_tier", "standard")),
                            str(usage.get("tariff", "standard")), multiplier, request_id, cost))
            return response

    def call_with_fallback(self, messages: list[dict], tools: list[dict] | None = None,
                           allow_uncensored: bool = False, force_model: str | None = None) -> dict:
        # Routing an der eigentlichen Nutzeranfrage ausrichten, nicht an Tool-Ergebnissen –
        # so bleibt eine Aktion über den gesamten Werkzeug-Loop beim passenden Modell.
        user_messages = [m for m in messages if m.get("role") == "user"]
        prompt = str((user_messages[-1] if user_messages else messages[-1]).get("content", ""))
        complexity, task_type = self.estimate_complexity(prompt), self.task_type(prompt)
        # Sub-Agenten geben ihr Modell fest vor: die Routing-Heuristik waehlte fuer
        # Werkzeugketten das Basismodell, das damit ueberfordert war.
        if force_model and force_model in self.models:
            selected = self.models[force_model]
            # Schutz gegen genau den Fehler, der die Mitarbeiter lahmgelegt hat:
            # ein fest vorgegebenes Modell ohne 'tools' bekam eine Werkzeugkette
            # und schrieb den Aufruf als Fliesstext hin. Wird die Vorgabe hier
            # unbrauchbar, entscheidet wieder die Heuristik.
            if tools and "tools" not in selected.tags:
                logger.warning("Modell %s kann keine Werkzeuge – Vorgabe verworfen.", force_model)
                selected = self.select_model(prompt, allow_uncensored=allow_uncensored,
                                             needs_tools=True)
        else:
            selected = self.select_model(prompt, allow_uncensored=allow_uncensored, needs_tools=bool(tools))
        route_id = uuid.uuid4().hex[:16]
        started = time.monotonic()
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        attempts: list[dict] = []
        with connection() as db:
            db.execute("INSERT INTO route_decisions(id,created_at,task_type,complexity,selected_model,prompt_hash) VALUES(?,?,?,?,?,?)",
                       (route_id, datetime.now(timezone.utc).isoformat(), task_type, complexity, selected.name, prompt_hash))
        errors: list[str] = []
        chain = self._resolve_fallbacks(selected, allow_uncensored, needs_tools=bool(tools))

        def _has_stronger_tier(index: int) -> bool:
            return any(self._provider_enabled(chain[j].provider) for j in range(index + 1, len(chain)))

        for position, spec in enumerate(chain):
            # Provider kürzlich mit 429 oder einem dauerhaften Fehler (kein
            # Guthaben, gesperrt) gescheitert? Dann gar nicht erst versuchen –
            # 15 Minuten bei 429, bis Mitternacht bei leerem Guthaben. Auf
            # debug, weil die Zeile sonst pro Kettenglied ins Log läuft.
            if _provider_429_gesperrt(spec.provider):
                attempts.append({"model": spec.name, "status": "skipped_provider_gesperrt"})
                logger.debug("Provider %s: Sperre aktiv, Versuch übersprungen.", spec.provider)
                continue
            if not self._cloud_affordable(spec, messages):
                attempts.append({"model": spec.name, "status": "skipped_cost_limit"})
                continue
            try:
                attempt_started = time.monotonic()
                response = self.call_llm(spec, messages, tools, request_id=route_id)
                adequate = True
                # Günstige Stufen (lokal, Groq, …) werden bei schweren Anfragen geprüft und
                # eskalieren zur nächsten Stufe – aber nur, solange es eine stärkere gibt.
                if (not allow_uncensored and "top_tier" not in spec.tags
                        and _has_stronger_tier(position)):
                    adequate = self._local_quality_check(prompt, response, complexity)
                attempts.append({"model": spec.name, "status": "ok" if adequate else "inadequate",
                                 "latency_ms": int((time.monotonic() - attempt_started) * 1000)})
                if not adequate:
                    continue
                # Erfolg: Provider freigeben (nicht mehr als gesperrt merken).
                _provider_429_merken(spec.provider, False)
                response["_model"] = spec.name
                response["_route_id"] = route_id
                with connection() as db:
                    db.execute("UPDATE route_decisions SET final_model=?,success=1,escalated=?,latency_ms=?,attempts_json=? WHERE id=?",
                               (spec.name, int(spec != selected), int((time.monotonic() - started) * 1000),
                                json.dumps(attempts), route_id))
                return response
            except Exception as exc:
                # War es eine 429-Antwort oder ein dauerhafter Fehler (kein
                # Guthaben, Account gesperrt)? Dann merken und überspringen –
                # bei einem dauerhaften Fehler sofort, ohne zweiten Versuch.
                dauerhaft = _ist_dauerhafter_fehler(exc)
                war_429 = dauerhaft or "429" in str(exc)
                _provider_429_merken(spec.provider, war_429, sofort=dauerhaft)
                errors.append(f"{spec.name}: {exc}")
                attempts.append({"model": spec.name, "status": "error", "error": str(exc)[:500]})
                logger.warning("Modell %s fehlgeschlagen: %s", spec.name, exc)
        with connection() as db:
            db.execute("UPDATE route_decisions SET latency_ms=?,attempts_json=?,error=? WHERE id=?",
                       (int((time.monotonic() - started) * 1000), json.dumps(attempts), " | ".join(errors), route_id))
        raise RuntimeError("Alle Modellaufrufe fehlgeschlagen: " + " | ".join(errors))

    def feedback(self, route_id: str, value: int) -> dict:
        if value not in {-1, 1}:
            raise ValueError("Feedback muss -1 oder 1 sein.")
        with connection() as db:
            db.execute("UPDATE route_decisions SET user_feedback=?,feedback_at=? WHERE id=?",
                       (value, datetime.now(timezone.utc).isoformat(), route_id))
            row = db.execute("SELECT id,task_type,final_model,user_feedback FROM route_decisions WHERE id=?", (route_id,)).fetchone()
        if not row:
            raise KeyError("Routing-Entscheidung nicht gefunden.")
        return dict(row)

    def context_budget(self) -> int:
        """Kleinstes Kontextfenster der lokalen Modelle. Der Verlauf muss in
        jedes Modell der Fallback-Kette passen – die Cloud-Fenster sind riesig,
        der Engpass ist immer das lokale Basismodell."""
        local = [spec.context_length for spec in self.models.values() if spec.provider == "ollama"]
        return min(local) if local else 16384

    def status(self) -> dict[str, Any]:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with connection() as db:
            totals = db.execute(
                "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,"
                "COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,"
                "COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,"
                "COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,COALESCE(SUM(cost_usd),0) AS cost_usd "
                "FROM model_usage WHERE substr(created_at,1,7)=?", (month,)
            ).fetchone()
            decisions = db.execute(
                "SELECT COUNT(*) AS total,COALESCE(SUM(escalated),0) AS escalated,"
                "SUM(CASE WHEN user_feedback=1 THEN 1 ELSE 0 END) AS positive_feedback,"
                "SUM(CASE WHEN user_feedback=-1 THEN 1 ELSE 0 END) AS negative_feedback "
                "FROM route_decisions WHERE substr(created_at,1,7)=?", (month,)
            ).fetchone()
            recent = db.execute(
                "SELECT id,created_at,task_type,complexity,selected_model,final_model,success,escalated,"
                "latency_ms,user_feedback,error FROM route_decisions ORDER BY created_at DESC LIMIT 25"
            ).fetchall()
            by_model = db.execute(
                "SELECT provider,model,SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,"
                "SUM(cached_input_tokens) AS cached_input_tokens,SUM(reasoning_tokens) AS reasoning_tokens,"
                "SUM(cost_usd) AS cost_usd,COUNT(*) AS calls,GROUP_CONCAT(DISTINCT tariff) AS tariffs "
                "FROM model_usage "
                "WHERE substr(created_at,1,7)=? GROUP BY provider,model ORDER BY cost_usd DESC", (month,)
            ).fetchall()
        return {
            "paid_models_enabled": self.paid_models_enabled,
            "budget_used_usd": round(self.budget_used, 6),
            "budget_limit_usd": self.budget_limit,
            "request_cost_limit_usd": self.request_cost_limit,
            "month": month,
            "usage": dict(totals),
            "context": self.last_context,
            "routing_training": dict(decisions),
            "usage_by_model": [dict(row) for row in by_model],
            "recent_routes": [dict(row) for row in recent],
            "models": [{
                "name": model.name,
                "provider": model.provider,
                "model_name": model.model_name,
                "enabled": self._provider_enabled(model.provider),
                "local": model.provider == "ollama",
                "tags": model.tags,
            } for model in self.models.values()],
        }
