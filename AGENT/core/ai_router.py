"""AIRouter: der eine Einstiegspunkt, ueber den jeder Aufrufer ein Modell
bekommt - `router.generate(task_type, messages, tools, agent_name)`.

Ersetzt NICHT ModelRouter, sondern umschliesst ihn: die eigentliche
Ausfuehrung (Provider-Sperren, Verlauf-Zuschneiden, Kostenpruefung, Aufruf,
Protokollierung) bleibt in ModelRouter._execute_chain - hier wird nur
entschieden, WELCHE Kette dafuer infrage kommt, deklarativ aus der
TaskRegistry statt aus einer pro Anfrage geratenen Heuristik.

jude_chat ist die einzige Ausnahme: Tinos eigener Chat hat immer das volle
Werkzeug-Set geladen, auch fuer reines Plaudern - dafuer bleibt die
bestehende weiche Heuristik in ModelRouter.select_model/call_with_fallback
unveraendert aktiv (strict_tools=False), statt sie hier zu duplizieren.

Stage 5 (jolly-napping-tulip.md Teil 2) ist abgeschlossen: jede Rolle in
data/sub_agents.json sowie Judes eigener Chat (main.py) haben
use_ai_router=True und laufen ueber generate() statt ueber die alte,
pro Anfrage geratene Heuristik. Die Heuristik selbst
(ModelRouter.select_model) bleibt Code – sie ist jetzt jude_chats private
Implementierung (siehe oben) und der Fallback, den TaskRegistry-Validierung
beim Start prueft. Sie erst nach einer stabilen Woche route_decisions je
Rolle loeschen, nicht vorher (Plan, Abschnitt 5, Stage 5).
"""

from __future__ import annotations

import uuid

from core.model_router import ModelRouter
from core.task_registry import TaskRegistry


class AIRouter:
    def __init__(self, model_router: ModelRouter, task_registry: TaskRegistry):
        self.router = model_router
        self.registry = task_registry
        self.registry.validate(set(self.router.models))

    #: Wohin eskaliert wird, wenn AgentBuilder._eskalation_noetig() True liefert -
    #: dieselbe Reihenfolge/Begruendung wie AgentBuilder.ESKALATIONS_MODELL/
    #: ESKALATIONS_MODELL_ALT (dort: OpenAI zuerst, Anthropic zweite Wahl, siehe
    #: dortiger Kommentar). Eigene Konstanten statt Import, um keine Abhaengigkeit
    #: von services.team_builder in core/ einzufuehren.
    ESKALATIONS_MODELLE = ("cloud_openai_terra", "cloud_claude_sonnet")

    def generate(self, task_type: str, messages: list[dict], tools: list[dict] | None = None,
                agent_name: str | None = None, allow_uncensored: bool = False,
                eskalieren: bool = False) -> dict:
        if task_type == "jude_chat":
            # Unveraenderter weicher Pfad - siehe Modulkommentar oben.
            return self.router.call_with_fallback(messages, tools, allow_uncensored, strict_tools=False)

        namen = self.registry.tiers_for(task_type)
        braucht_werkzeuge = self.registry.requires_tools(task_type)
        if eskalieren:
            # Eskalationsmodelle VOR die normale Kette setzen, statt sie zu
            # ersetzen - faellt auch die Eskalationsstufe aus, bleibt die
            # normale Kette als Ausweichebene erhalten. Steht eines der
            # Eskalationsmodelle schon in der normalen Kette (z.B. cloud_openai_terra
            # bei content_drafting), wird es nach vorn gezogen statt doppelt
            # aufzutauchen.
            namen = list(self.ESKALATIONS_MODELLE) + [n for n in namen if n not in self.ESKALATIONS_MODELLE]
        kette = [self.router.models[n] for n in namen if n in self.router.models]
        kette = [m for m in kette if self.router._provider_enabled(m.provider)
                and (not braucht_werkzeuge or "tools" in m.tags)]
        if not kette:
            raise ValueError(f"Kein aktiviertes, passendes Modell fuer task_type {task_type!r}.")

        route_id = uuid.uuid4().hex[:16]
        from datetime import datetime, timezone
        self.router._route_repo.insert_with_agent(
            route_id, datetime.now(timezone.utc).isoformat(), task_type, 0, kette[0].name, "", agent_name)
        return self.router._execute_chain(kette, messages, tools, selected_name=kette[0].name, route_id=route_id)
