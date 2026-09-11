"""Deklarative task_type -> Modell-Eskalationsleiter (config/task_registry.yaml).

Ersetzt schrittweise die Heuristik in ModelRouter.select_model (Task-Typ und
Komplexitaet aus dem Prompt-Text geraten) durch eine einmal festgelegte
Zuordnung je Rolle - siehe AGENT/core/ai_router.py fuer den Aufrufer.
"""

from __future__ import annotations

from pathlib import Path

import yaml


class TaskRegistry:
    def __init__(self, path=None):
        self.path = path or (Path(__file__).parents[1] / "config" / "task_registry.yaml")
        self._task_types: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        daten = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._task_types = daten.get("task_types", {})
        if not self._task_types:
            raise ValueError(f"{self.path}: keine task_types definiert.")

    def validate(self, bekannte_modelle: set[str]) -> None:
        """Bricht sofort ab, wenn eine Kette ein nicht existierendes Modell
        nennt - sonst faellt das erst beim ersten echten Aufruf auf."""
        for task_type, spec in self._task_types.items():
            for name in spec.get("tiers", []):
                if name not in bekannte_modelle:
                    raise ValueError(
                        f"task_registry.yaml: task_type {task_type!r} nennt "
                        f"unbekanntes Modell {name!r}.")

    def tiers_for(self, task_type: str) -> list[str]:
        spec = self._task_types.get(task_type)
        if spec is None:
            raise KeyError(f"Unbekannter task_type {task_type!r}. "
                           f"Bekannt: {', '.join(sorted(self._task_types))}.")
        return list(spec.get("tiers", []))

    def requires_tools(self, task_type: str) -> bool:
        spec = self._task_types.get(task_type)
        if spec is None:
            raise KeyError(f"Unbekannter task_type {task_type!r}. "
                           f"Bekannt: {', '.join(sorted(self._task_types))}.")
        return bool(spec.get("requires_tools", True))

    def known_task_types(self) -> list[str]:
        return sorted(self._task_types)
