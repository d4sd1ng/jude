"""Kleines Plugin-Register mit Function-Calling-Schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    param_schema: dict

    def to_openai_format(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.param_schema,
        }}


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name.isidentifier():
            raise ValueError(f"Ungültiger Tool-Name: {tool.name}")
        self.tools[tool.name] = tool

    def register_function(self, name: str, description: str, param_schema: dict):
        def decorator(func: Callable[..., Any]):
            self.register(Tool(name, description, func, param_schema))
            return func
        return decorator

    def get_tools_openai(self) -> list[dict]:
        return [tool.to_openai_format() for tool in self.tools.values()]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(tool_name)
        if tool is None:
            return f"Tool '{tool_name}' nicht gefunden."
        if not isinstance(arguments, dict):
            return "Tool-Argumente müssen ein Objekt sein."
        try:
            return str(tool.func(**arguments))
        except Exception as exc:
            return f"Tool '{tool_name}' fehlgeschlagen: {exc}"
