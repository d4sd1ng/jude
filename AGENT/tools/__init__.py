"""Automatisches Laden installierter Tools."""

import importlib
import importlib.util
import logging
import os
import pkgutil
from pathlib import Path

from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def load_all_tools(registry: ToolRegistry, **context) -> None:
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{module_info.name}")
            if hasattr(module, "register"):
                module.register(registry)
            if hasattr(module, "register_context"):
                module.register_context(registry, **context)
            if module_info.ispkg and module_info.name == "generiert":
                for child in pkgutil.iter_modules(module.__path__):
                    generated = importlib.import_module(f"{module.__name__}.{child.name}")
                    if hasattr(generated, "register"):
                        generated.register(registry)
        except Exception as exc:
            logger.warning("Tool-Modul %s konnte nicht geladen werden: %s", module_info.name, exc)
    generated_dir = Path(os.getenv("JUDE_GENERATED_TOOLS_DIR", "/media/d4sd1ng/AI-Data/Jude/generated_tools"))
    if generated_dir.is_dir():
        for path in sorted(generated_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(f"jude_generated_{path.stem}", path)
                if spec is None or spec.loader is None:
                    raise ImportError(path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.register(registry)
            except Exception as exc:
                logger.warning("Generiertes Tool %s konnte nicht geladen werden: %s", path.name, exc)
