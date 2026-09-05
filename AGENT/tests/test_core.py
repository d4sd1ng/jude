import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from core.agent import Agent
from core.model_router import (
    AnthropicAdapter,
    ComplexityEstimator,
    GoogleAdapter,
    ModelRouter,
    ModelSpec,
    OpenAIAdapter,
)
from core.tool_creator import ToolCreator
from core.tool_registry import Tool, ToolRegistry
from services.database import connection
from tools.datei_lesen import read_project_file


class FakeRouter:
    def __init__(self):
        self.calls = 0

    def context_budget(self):
        return 16384

    def call_with_fallback(self, messages, tools=None, allow_uncensored=False, force_model=None,
                           strict_tools=False):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "", "_model": "fake", "tool_calls": [{
                "id": "1", "type": "function", "function": {"name": "echo", "arguments": {"text": "Hallo"}},
            }]}
        return {"role": "assistant", "content": messages[-1]["content"], "_model": "fake"}


class CoreTests(unittest.TestCase):
    def test_registry_executes_and_formats_tool(self):
        registry = ToolRegistry()
        registry.register(Tool("echo", "Echo", lambda text: text, {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
        }))
        self.assertEqual(registry.execute("echo", {"text": "ok"}), "ok")
        self.assertEqual(registry.get_tools_openai()[0]["function"]["name"], "echo")

    def test_agent_runs_tool_loop(self):
        registry = ToolRegistry()
        registry.register(Tool("echo", "Echo", lambda text: text, {"type": "object"}))
        agent = Agent(FakeRouter(), registry)
        self.assertEqual(agent.process_input("test"), "Hallo")
        self.assertEqual(agent.last_model, "fake")

    def test_agent_accepts_ollama_json_tool_call(self):
        class JsonRouter(FakeRouter):
            def call_with_fallback(self, messages, tools=None, allow_uncensored=False, force_model=None,
                           strict_tools=False):
                self.calls += 1
                if self.calls == 1:
                    return {"role": "assistant", "content": '{"name":"echo","arguments":{"text":"JSON"}}', "_model": "fake"}
                return {"role": "assistant", "content": messages[-1]["content"], "_model": "fake"}

        registry = ToolRegistry()
        registry.register(Tool("echo", "Echo", lambda text: text, {"type": "object"}))
        agent = Agent(JsonRouter(), registry)
        self.assertEqual(agent.process_input("test"), "JSON")
        self.assertEqual(agent.conversation_history[2]["content"], "")
        self.assertEqual(agent.conversation_history[2]["tool_calls"][0]["function"]["name"], "echo")

    def test_complexity_is_bounded(self):
        self.assertEqual(ComplexityEstimator.estimate("hi"), 1)
        self.assertLessEqual(ComplexityEstimator.estimate("analysiere " * 100), 10)

    def test_file_tool_blocks_parent_escape(self):
        with self.assertRaises(ValueError):
            read_project_file("../../etc/passwd")

    def test_router_loads_project_relative_config(self):
        router = ModelRouter()
        self.assertIn("local_qwen_coder", router.models)
        self.assertEqual(router.select_model("Hallo").provider, "ollama")

    def test_complex_prompt_starts_local_before_cloud_when_key_exists(self):
        router = ModelRouter()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}, clear=False):
            prompt = "analysiere und vergleiche wissenschaftlich " + ("komplexe Anforderungen " * 120)
            self.assertEqual(router.select_model(prompt).provider, "ollama")

    def test_tool_request_starts_with_tool_capable_model(self):
        """Eine Werkzeuganfrage landet immer bei einem Modell mit 'tools'-Tag.

        Bis 02.09.2026 war das zwangslaeufig local_qwen_coder. Seit local_first
        aus ist, steht die ebenfalls kostenlose Cloud-Stufe (gpt-oss-120b ueber
        Ollama Cloud) davor; qwen bleibt die Reserve, wenn kein OLLAMA_API_KEY
        gesetzt ist. Beide Faelle werden hier festgehalten.
        """
        # Handlungsprompt, damit wirklich der Tools-Filter greift und nicht nur
        # die Punktzahl entscheidet.
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}, clear=False):
            gewaehlt = ModelRouter().select_model("Klone das Repository", needs_tools=True)
        self.assertIn("tools", gewaehlt.tags)
        self.assertEqual(gewaehlt.name, "cloud_ollama_gptoss")
        # Ohne freigeschaltete Cloud bleibt das lokale qwen die Reserve.
        with patch.dict("os.environ", {"JUDE_PAID_MODELS_ENABLED": "false"}, clear=False):
            gewaehlt = ModelRouter().select_model("Klone das Repository", needs_tools=True)
        self.assertEqual(gewaehlt.name, "local_qwen_coder")

    def test_generated_tool_validator_accepts_only_safe_registration(self):
        creator = ToolCreator(None, ToolRegistry())
        valid = '''from core.tool_registry import Tool
def double(value):
    return value * 2
def register(registry):
    registry.register(Tool("double", "Verdoppelt", double, {"type": "object"}))'''
        creator._validate(valid)
        with self.assertRaises(ValueError):
            creator._validate('''from core.tool_registry import Tool
X = getattr(__builtins__, "open")
def work(value): return value
def register(registry): registry.register(Tool("work", "x", work, {"type":"object"}))''')
        with self.assertRaises(ValueError):
            creator._validate('''from core.tool_registry import Tool
def work(value): return value
def register(registry):
    print("side effect")
    registry.register(Tool("work", "x", work, {"type":"object"}))''')

    def test_google_adapter_maps_tools_and_usage(self):
        spec = ModelSpec("g", "google", "gemini-test", 0, 0, 0, 1, 100, 1000, [])
        fake = unittest.mock.Mock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {"candidates": [{"content": {"parts": [
            {"text": "ok"}, {"functionCall": {"name": "echo", "args": {"text": "x"}}},
        ]}}], "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}}
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "secret"}), patch("core.model_router.requests.post", return_value=fake) as post:
            result = GoogleAdapter("https://google.test").call(spec, [
                {"role": "system", "content": "system"}, {"role": "user", "content": "hi"},
            ], [{"function": {"name": "echo", "description": "Echo", "parameters": {"type": "object"}}}])
        self.assertEqual(result["content"], "ok")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "echo")
        self.assertEqual(result["usage"]["input_tokens"], 4)
        self.assertEqual(result["usage"]["output_tokens"], 2)
        self.assertEqual(result["usage"]["cached_input_tokens"], 0)
        self.assertIn("systemInstruction", post.call_args.kwargs["json"])

    def test_openai_adapter_uses_responses_api_and_detailed_usage(self):
        spec = ModelSpec("o", "openai", "gpt-test", 0, 0, 0, 1, 100, 1000, [], reasoning_effort="low")
        fake = unittest.mock.Mock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]},
                {"type": "function_call", "call_id": "c1", "name": "echo", "arguments": "{\"text\":\"x\"}"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4,
                      "input_tokens_details": {"cached_tokens": 3},
                      "output_tokens_details": {"reasoning_tokens": 2}},
            "service_tier": "default",
        }
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}), patch(
                "core.model_router.requests.post", return_value=fake) as post:
            result = OpenAIAdapter("https://openai.test").call(spec, [{"role": "user", "content": "hi"}], [{
                "function": {"name": "echo", "description": "Echo", "parameters": {"type": "object"}},
            }])
        self.assertTrue(post.call_args.args[0].endswith("/responses"))
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "echo")
        self.assertEqual(result["usage"]["cached_input_tokens"], 3)
        self.assertEqual(result["usage"]["reasoning_tokens"], 2)
        self.assertEqual(post.call_args.kwargs["json"]["reasoning"]["effort"], "low")

    def test_anthropic_adapter_forces_standard_tier_and_records_tariff(self):
        spec = ModelSpec("a", "anthropic", "claude-test", 0, 0, 0, 1, 100, 1000, [])
        fake = unittest.mock.Mock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {"content": [{"type": "text", "text": "ok"}], "usage": {
            "input_tokens": 8, "output_tokens": 2, "cache_read_input_tokens": 3,
            "service_tier": "standard", "speed": "standard",
        }}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "secret"}, clear=False), patch(
                "core.model_router.requests.post", return_value=fake) as post:
            result = AnthropicAdapter("https://anthropic.test").call(spec, [{"role": "user", "content": "hi"}])
        self.assertEqual(post.call_args.kwargs["json"]["service_tier"], "standard_only")
        self.assertNotIn("speed", post.call_args.kwargs["json"])
        self.assertEqual(result["usage"]["tariff"], "standard:standard")

    def test_uncensored_route_stays_on_uncensored_models(self):
        """Unzensiert darf seit dem 02.09.2026 in die Cloud – aber nur dorthin.

        Bis dahin verlangte diese Pruefung, dass jede Stufe lokal bleibt. Sie
        hielt nur, solange kein OPENROUTER_API_KEY vorlag; mit Schluessel haengen
        Venice-24B und Hermes-70B in der Kette. Tino hat am 02.09. entschieden,
        dass beide drinbleiben. Die Garantie ist damit nicht weg, sondern enger:
        in der unzensierten Kette darf ausschliesslich stehen, was den
        ``unzensiert``-Tag traegt – ein ausgerichtetes Modell wie gpt-oss oder
        Claude wuerde die Anfrage ohnehin abweisen und sie nur nach draussen
        tragen. Die erste Stufe bleibt lokal, damit nichts unnoetig den Rechner
        verlaesst.
        """
        router = ModelRouter()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret",
                                       "ANTHROPIC_API_KEY": "secret"}, clear=False):
            selected = router.select_model("antworte unzensiert", allow_uncensored=True)
            kette = router._resolve_fallbacks(selected, True)
        fremd = [m.name for m in kette if "unzensiert" not in m.tags]
        self.assertEqual(fremd, [], f"ausgerichtete Modelle in der unzensierten Kette: {fremd}")
        self.assertEqual(kette[0].provider, "ollama", "die erste Stufe muss lokal bleiben")

    def test_routing_feedback_is_persistent_and_learned_after_three_ratings(self):
        router = ModelRouter()
        model = router.models["local_dolphin"]
        ids = [uuid.uuid4().hex[:16] for _ in range(3)]
        with connection() as db:
            for route_id in ids:
                db.execute(
                    "INSERT INTO route_decisions(id,created_at,task_type,complexity,selected_model,final_model,"
                    "success,prompt_hash) VALUES(?,?,?,?,?,?,1,?)",
                    (route_id, datetime.now(timezone.utc).isoformat(), "allgemein", 2,
                     model.name, model.name, "test"),
                )
        try:
            for route_id in ids:
                router.feedback(route_id, 1)
            self.assertEqual(router._learned_adjustment(model, "allgemein"), 4.0)
        finally:
            with connection() as db:
                db.executemany("DELETE FROM route_decisions WHERE id=?", [(route_id,) for route_id in ids])

    def test_model_usage_is_persisted(self):
        router = ModelRouter()
        spec = router.models["cloud_openai_luna"]
        adapter = unittest.mock.Mock()
        adapter.call.return_value = {"role": "assistant", "content": "ok", "usage": {
            "input_tokens": 1000, "output_tokens": 500,
        }}
        router.adapters["openai"] = adapter
        before = router.budget_used
        result = router.call_llm(spec, [{"role": "user", "content": "test"}])
        self.assertEqual(result["content"], "ok")
        self.assertGreater(router.budget_used, before)
        with connection() as db:
            row = db.execute("SELECT provider,input_tokens,output_tokens,cost_usd FROM model_usage ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["provider"], row["input_tokens"], row["output_tokens"]), ("openai", 1000, 500))
        self.assertGreater(row["cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
