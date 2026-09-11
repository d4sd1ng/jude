"""Datenzugriff auf ``route_decisions`` und ``model_usage`` an einer Stelle.

Vorher stand dieselbe SQL an 11 (route_decisions) bzw. 5 (model_usage)
Stellen verteilt in core/model_router.py und core/ai_router.py - jede
Schema-Aenderung musste an jeder Stelle einzeln nachvollzogen werden.
``reviews`` (das dritte vieltraffic-Table) hat mit ``services.review.
ReviewQueue`` bereits genau diese Kapselung; hier wird derselbe Zuschnitt
fuer die anderen zwei Tabellen nachgezogen - reine Verschiebung, keine
Verhaltensaenderung: jede Methode hier fuehrt exakt dieselbe SQL aus wie
vorher am jeweiligen Aufrufort.
"""

from __future__ import annotations

import logging
from typing import Any

from services.database import connection

logger = logging.getLogger(__name__)


class RouteDecisionRepository:
    def insert(self, route_id: str, created_at: str, task_type: str, complexity: int,
              selected_model: str, prompt_hash: str) -> None:
        with connection() as db:
            db.execute(
                "INSERT INTO route_decisions(id,created_at,task_type,complexity,selected_model,prompt_hash) "
                "VALUES(?,?,?,?,?,?)",
                (route_id, created_at, task_type, complexity, selected_model, prompt_hash))

    def insert_with_agent(self, route_id: str, created_at: str, task_type: str, complexity: int,
                          selected_model: str, prompt_hash: str, agent_name: str | None) -> None:
        with connection() as db:
            db.execute(
                "INSERT INTO route_decisions(id,created_at,task_type,complexity,selected_model,"
                "prompt_hash,agent_name) VALUES(?,?,?,?,?,?,?)",
                (route_id, created_at, task_type, complexity, selected_model, prompt_hash, agent_name))

    def mark_success(self, route_id: str, final_model: str, escalated: int, latency_ms: int,
                     attempts_json: str) -> None:
        with connection() as db:
            db.execute(
                "UPDATE route_decisions SET final_model=?,success=1,escalated=?,latency_ms=?,attempts_json=? "
                "WHERE id=?",
                (final_model, escalated, latency_ms, attempts_json, route_id))
        # Bis 10.09.2026 war auch das Routing-Ergebnis selbst stumm - nur in
        # route_decisions sichtbar, nicht im Log. Escalation war damit nur per
        # SQL-Abfrage nachvollziehbar, nie live im laufenden Betrieb sichtbar.
        logger.info("Routing %s: final_model=%s%s, %dms.", route_id, final_model,
                    " (escaliert)" if escalated else "", latency_ms)

    def mark_failure(self, route_id: str, latency_ms: int, attempts_json: str, error: str) -> None:
        with connection() as db:
            db.execute(
                "UPDATE route_decisions SET latency_ms=?,attempts_json=?,error=? WHERE id=?",
                (latency_ms, attempts_json, error, route_id))
        logger.warning("Routing %s fehlgeschlagen nach %dms: %s", route_id, latency_ms, error[:300])

    def record_feedback(self, route_id: str, value: int, feedback_at: str) -> None:
        with connection() as db:
            db.execute(
                "UPDATE route_decisions SET user_feedback=?,feedback_at=? WHERE id=?",
                (value, feedback_at, route_id))

    def get(self, route_id: str) -> dict[str, Any] | None:
        with connection() as db:
            row = db.execute(
                "SELECT id,task_type,final_model,user_feedback FROM route_decisions WHERE id=?",
                (route_id,)).fetchone()
        return dict(row) if row else None

    def feedback_stats(self, task_type: str, model_name: str) -> dict[str, Any]:
        """Rohwerte fuer die Lern-Anpassung - die Schwelle (n>=3) und die
        Gewichtung (*4) sind eine Routing-Entscheidung, keine Datenfrage,
        und bleiben deshalb in ModelRouter._learned_adjustment."""
        with connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n,AVG(user_feedback) AS score FROM route_decisions "
                "WHERE task_type=? AND final_model=? AND user_feedback IS NOT NULL",
                (task_type, model_name)).fetchone()
        return dict(row)

    def monthly_summary(self, month: str) -> dict[str, Any]:
        with connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total,COALESCE(SUM(escalated),0) AS escalated,"
                "SUM(CASE WHEN user_feedback=1 THEN 1 ELSE 0 END) AS positive_feedback,"
                "SUM(CASE WHEN user_feedback=-1 THEN 1 ELSE 0 END) AS negative_feedback "
                "FROM route_decisions WHERE substr(created_at,1,7)=?", (month,)).fetchone()
        return dict(row)

    def recent(self, limit: int = 25) -> list[dict[str, Any]]:
        with connection() as db:
            rows = db.execute(
                "SELECT id,created_at,task_type,complexity,selected_model,final_model,success,escalated,"
                "latency_ms,user_feedback,error FROM route_decisions ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def negative_feedback_prompt_hashes(self) -> set[str]:
        with connection() as db:
            rows = db.execute(
                "SELECT prompt_hash FROM route_decisions WHERE user_feedback = -1").fetchall()
        return {r["prompt_hash"] for r in rows}


class ModelUsageRepository:
    def insert(self, created_at: str, provider: str, model: str, input_tokens: int, output_tokens: int,
              cached_input_tokens: int, cache_write_tokens: int, reasoning_tokens: int,
              service_tier: str, tariff: str, cost_multiplier: float, request_id: str | None,
              cost_usd: float) -> None:
        with connection() as db:
            db.execute(
                "INSERT INTO model_usage(created_at,provider,model,input_tokens,output_tokens,cached_input_tokens,"
                "cache_write_tokens,reasoning_tokens,service_tier,tariff,cost_multiplier,request_id,cost_usd) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (created_at, provider, model, input_tokens, output_tokens, cached_input_tokens,
                 cache_write_tokens, reasoning_tokens, service_tier, tariff, cost_multiplier,
                 request_id, cost_usd))

    def monthly_total(self, month: str) -> float:
        with connection() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS total FROM model_usage WHERE substr(created_at,1,7)=?",
                (month,)).fetchone()
        return float(row["total"])

    def provider_monthly_totals(self, month: str) -> dict[str, float]:
        with connection() as db:
            rows = db.execute(
                "SELECT provider, COALESCE(SUM(cost_usd),0) AS total FROM model_usage "
                "WHERE substr(created_at,1,7)=? GROUP BY provider", (month,)).fetchall()
        return {r["provider"]: float(r["total"]) for r in rows}

    def provider_daily_totals(self, day: str) -> dict[str, float]:
        with connection() as db:
            rows = db.execute(
                "SELECT provider, COALESCE(SUM(cost_usd),0) AS total FROM model_usage "
                "WHERE substr(created_at,1,10)=? GROUP BY provider", (day,)).fetchall()
        return {r["provider"]: float(r["total"]) for r in rows}

    def monthly_usage_summary(self, month: str) -> dict[str, Any]:
        with connection() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,"
                "COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,"
                "COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,"
                "COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,COALESCE(SUM(cost_usd),0) AS cost_usd "
                "FROM model_usage WHERE substr(created_at,1,7)=?", (month,)).fetchone()
        return dict(row)

    def by_model_monthly(self, month: str) -> list[dict[str, Any]]:
        with connection() as db:
            rows = db.execute(
                "SELECT provider,model,SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,"
                "SUM(cached_input_tokens) AS cached_input_tokens,SUM(reasoning_tokens) AS reasoning_tokens,"
                "SUM(cost_usd) AS cost_usd,COUNT(*) AS calls,GROUP_CONCAT(DISTINCT tariff) AS tariffs "
                "FROM model_usage "
                "WHERE substr(created_at,1,7)=? GROUP BY provider,model ORDER BY cost_usd DESC", (month,)).fetchall()
        return [dict(r) for r in rows]
