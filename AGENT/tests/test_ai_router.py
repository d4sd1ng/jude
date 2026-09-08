"""AIRouter.generate(): faultbasierte Eskalation.

Beobachtet 07.09.2026: als alle elf Rollen auf use_ai_router=True migriert
wurden, verlor AgentBuilder._eskalation_noetig() jede Wirkung - der
use_ai_router-Pfad in core/agent.py las nie force_model, ueber das die
Eskalation bis dahin lief. Diese Tests sichern den neuen eigenen Kanal
(eskalieren-Flag durch Agent -> AIRouter.generate()).
"""

from __future__ import annotations

from unittest.mock import patch

from core.ai_router import AIRouter
from core.model_router import ModelRouter
from core.task_registry import TaskRegistry


def test_eskalieren_stellt_eskalationsmodell_an_die_erste_stelle():
    with patch.dict("os.environ", {"OLLAMA_API_KEY": "test", "OPENAI_API_KEY": "test"}, clear=False):
        router = ModelRouter()
        ai_router = AIRouter(router, TaskRegistry())
        with patch.object(router, "_execute_chain", return_value={"answer": "x", "tool_calls": []}) as mock:
            ai_router.generate("routine_dispatch", [{"role": "user", "content": "x"}], eskalieren=True)
        kette = mock.call_args[0][0]
    assert kette[0].name in AIRouter.ESKALATIONS_MODELLE


def test_ohne_eskalieren_bleibt_die_normale_reihenfolge():
    with patch.dict("os.environ", {"OLLAMA_API_KEY": "test", "OPENAI_API_KEY": "test"}, clear=False):
        router = ModelRouter()
        ai_router = AIRouter(router, TaskRegistry())
        normale_kette = [n for n in TaskRegistry().tiers_for("routine_dispatch") if n in router.models]
        with patch.object(router, "_execute_chain", return_value={"answer": "x", "tool_calls": []}) as mock:
            ai_router.generate("routine_dispatch", [{"role": "user", "content": "x"}], eskalieren=False)
        kette = mock.call_args[0][0]
    assert kette[0].name == normale_kette[0]


def test_eskalieren_dupliziert_kein_modell_das_schon_in_der_kette_steht():
    """content_drafting hat cloud_openai_terra bereits in seiner eigenen Kette -
    beim Eskalieren darf es nicht doppelt auftauchen."""
    with patch.dict("os.environ", {"OLLAMA_API_KEY": "test", "OPENAI_API_KEY": "test"}, clear=False):
        router = ModelRouter()
        ai_router = AIRouter(router, TaskRegistry())
        with patch.object(router, "_execute_chain", return_value={"answer": "x", "tool_calls": []}) as mock:
            ai_router.generate("content_drafting", [{"role": "user", "content": "x"}], eskalieren=True)
        kette = mock.call_args[0][0]
    namen = [m.name for m in kette]
    assert namen.count("cloud_openai_terra") == 1
