from pathlib import Path

from core.tool_registry import Tool, ToolRegistry

PROJECT_ROOT = Path(__file__).parents[2].resolve()


def read_project_file(path: str, max_characters: int = 12000) -> str:
    target = (PROJECT_ROOT / path).resolve()
    if target != PROJECT_ROOT and PROJECT_ROOT not in target.parents:
        raise ValueError("Der Pfad liegt außerhalb des Projekts.")
    if not target.is_file():
        raise FileNotFoundError(path)
    return target.read_text(encoding="utf-8")[:max(1000, min(max_characters, 50000))]


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="read_project_file", description="Liest eine UTF-8-Textdatei innerhalb des Jude-Projekts.",
        func=read_project_file,
        param_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "max_characters": {"type": "integer", "minimum": 1000, "maximum": 50000},
        }, "required": ["path"]},
    ))
