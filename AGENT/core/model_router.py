"""Konfigurierbare Modellauswahl und Provider-Adapter."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from services.database import connection

logger = logging.getLogger(__name__)


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
        payload: dict[str, Any] = {
            "model": spec.model_name,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": spec.context_length, "num_predict": spec.max_tokens},
        }
        if tools:
            payload["tools"] = tools
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                message = data.get("message", {})
                message["usage"] = {
                    "input_tokens": data.get("prompt_eval_count", 0),
                    "output_tokens": data.get("eval_count", 0),
                }
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
                                    "output": str(message.get("content", ""))})
                continue
            if message.get("content"):
                input_items.append({"role": role, "content": str(message["content"])})
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
        response.raise_for_status()
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
                chat.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"],
                }]})
        payload: dict[str, Any] = {"model": spec.model_name, "messages": chat, "max_tokens": spec.max_tokens,
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
        response.raise_for_status()
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
                                                "response": {"result": message.get("content", "")}}}]
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
                reasoning_effort=data.get("reasoning_effort"),
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
        key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}.get(provider)
        return key is None or (self.paid_models_enabled and bool(os.getenv(key)))

    def select_model(self, prompt: str, allow_uncensored: bool = False, force_local: bool = False,
                     needs_tools: bool = False) -> ModelSpec:
        task_type = self.task_type(prompt)
        candidates = [m for m in self.models.values() if self._provider_enabled(m.provider)]
        if force_local or self.budget_used >= self.budget_limit or self.router_cfg.get("local_first", True):
            candidates = [m for m in candidates if "lokal" in m.tags]
        if allow_uncensored:
            candidates = [m for m in candidates if "unzensiert" in m.tags]
        elif needs_tools:
            candidates = [m for m in candidates if "tools" in m.tags]
        if not candidates:
            raise ValueError("Kein passendes und aktiviertes Modell konfiguriert.")

        def score(model: ModelSpec) -> float:
            is_local = "lokal" in model.tags
            local_preference = 2 if is_local and self.router_cfg.get("prefer_local", True) else 0
            task_fit = 7 if task_type in model.tags else 4 if task_type == "allgemein" and "allgemein" in model.tags else 0
            uncensored_default = 2 if task_type == "allgemein" and "unzensiert" in model.tags else 0
            return (local_preference + task_fit + uncensored_default + model.priority - model.latency / 300
                    - (model.cost_input + model.cost_output) * 100 + self._learned_adjustment(model, task_type)) * model.weight

        return max(candidates, key=score)

    def _resolve_fallbacks(self, selected: ModelSpec, allow_uncensored: bool) -> list[ModelSpec]:
        result = [selected]
        for entry in self.router_cfg.get("fallback_chain", []):
            matches = ([m for m in self.models.values() if entry[5:] in m.tags] if entry.startswith("tags:")
                       else [self.models[entry]] if entry in self.models else [])
            for model in sorted(matches, key=lambda item: item.priority, reverse=True):
                if model not in result and self._provider_enabled(model.provider):
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
            with connection() as db:
                db.execute("INSERT INTO model_usage(created_at,provider,model,input_tokens,output_tokens,cached_input_tokens,"
                           "cache_write_tokens,reasoning_tokens,service_tier,tariff,cost_multiplier,request_id,cost_usd) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), spec.provider, spec.model_name, input_tokens, output_tokens,
                            cached, cache_write, reasoning, str(usage.get("service_tier", "standard")),
                            str(usage.get("tariff", "standard")), multiplier, request_id, cost))
            return response

    def call_with_fallback(self, messages: list[dict], tools: list[dict] | None = None,
                           allow_uncensored: bool = False) -> dict:
        prompt = str(messages[-1].get("content", ""))
        complexity, task_type = self.estimate_complexity(prompt), self.task_type(prompt)
        selected = self.select_model(prompt, allow_uncensored=allow_uncensored, needs_tools=bool(tools))
        route_id = uuid.uuid4().hex[:16]
        started = time.monotonic()
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        attempts: list[dict] = []
        with connection() as db:
            db.execute("INSERT INTO route_decisions(id,created_at,task_type,complexity,selected_model,prompt_hash) VALUES(?,?,?,?,?,?)",
                       (route_id, datetime.now(timezone.utc).isoformat(), task_type, complexity, selected.name, prompt_hash))
        errors: list[str] = []
        for spec in self._resolve_fallbacks(selected, allow_uncensored):
            if not self._cloud_affordable(spec, messages):
                attempts.append({"model": spec.name, "status": "skipped_cost_limit"})
                continue
            try:
                attempt_started = time.monotonic()
                response = self.call_llm(spec, messages, tools, request_id=route_id)
                adequate = True
                if spec.provider == "ollama" and not allow_uncensored and any(
                        self._provider_enabled(provider) for provider in ("openai", "anthropic", "google")):
                    adequate = self._local_quality_check(prompt, response, complexity)
                attempts.append({"model": spec.name, "status": "ok" if adequate else "inadequate",
                                 "latency_ms": int((time.monotonic() - attempt_started) * 1000)})
                if not adequate:
                    continue
                response["_model"] = spec.name
                response["_route_id"] = route_id
                with connection() as db:
                    db.execute("UPDATE route_decisions SET final_model=?,success=1,escalated=?,latency_ms=?,attempts_json=? WHERE id=?",
                               (spec.name, int(spec != selected), int((time.monotonic() - started) * 1000),
                                json.dumps(attempts), route_id))
                return response
            except Exception as exc:
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
