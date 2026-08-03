"""Zweistufige, bestätigungspflichtige Erzeugung neuer Plugins."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
import subprocess
import tempfile
from pathlib import Path

from services.filesystem import AI_DATA_ROOT
from core.tool_registry import Tool, ToolRegistry



class ToolCreator:
    ALLOWED_IMPORTS = {"core", "math", "datetime", "json", "re", "statistics", "decimal", "fractions", "collections", "itertools"}
    FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
                       "getattr", "setattr", "delattr", "globals", "locals", "vars"}
    FORBIDDEN_ATTRIBUTES = {"system", "popen", "spawn", "unlink", "rmdir", "remove", "write_text", "write_bytes", "connect", "request"}

    def __init__(self, llm_router, tool_registry: ToolRegistry, code_dir: str | Path | None = None):
        self.router = llm_router
        self.registry = tool_registry
        self.code_dir = Path(code_dir) if code_dir else AI_DATA_ROOT / "Jude" / "generated_tools"
        self.pending: dict[str, str] = {}

    def generate_draft(self, request: str) -> str:
        prompt = (
            "Erzeuge ein eigenständiges Python-Plugin für diese Aufgabe: " + request + "\n"
            "Das Modul muss `from core.tool_registry import Tool` importieren, genau eine Arbeitsfunktion definieren "
            "und danach `def register(registry): registry.register(Tool(...))` definieren. "
            "Rufe register niemals auf Modulebene auf. Tool erhält name, description, func und param_schema; "
            "param_schema ist ein vollständiges JSON-Schema. Keine Datei-, Prozess-, Netzwerk- oder Systemzugriffe. "
            "Antworte ausschließlich mit dem Python-Modul."
        )
        error: Exception | None = None
        code = ""
        for attempt in range(2):
            messages = [{"role": "user", "content": prompt}]
            if attempt and error is not None:
                messages[0]["content"] += f"\nDer erste Entwurf war ungültig ({error}). Korrigiere das Format exakt."
            response = self.router.call_with_fallback(messages)
            code = self._strip_fences(str(response.get("content", "")))
            try:
                self._validate(code)
                break
            except (SyntaxError, ValueError) as exc:
                error = exc
        else:
            raise ValueError(f"Das Modell lieferte zweimal keinen gültigen Tool-Entwurf: {error}")
        token = hashlib.sha256(code.encode()).hexdigest()[:12]
        self.pending[token] = code
        return f"Entwurf {token} geprüft. Mit /show-tool {token} anzeigen und danach mit /approve-tool {token} aktivieren."

    def show(self, token: str) -> str:
        code = self.pending.get(token)
        if code is None:
            raise ValueError("Unbekannter oder abgelaufener Tool-Entwurf.")
        return code

    def approve(self, token: str) -> str:
        code = self.pending.get(token)
        if code is None:
            raise ValueError("Unbekannter oder abgelaufener Tool-Entwurf.")
        self._test_in_docker(code)
        tree = ast.parse(code)
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name != "register"]
        name = functions[0]
        target = self.code_dir / f"{name}.py"
        self.code_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(code + "\n", encoding="utf-8")
        self._load(target)
        del self.pending[token]
        return f"Tool '{name}' wurde als {target.name} aktiviert."

    @staticmethod
    def _strip_fences(code: str) -> str:
        match = re.search(r"```(?:python)?\s*(.*?)```", code, flags=re.IGNORECASE | re.DOTALL)
        return (match.group(1) if match else code).strip()

    def _validate(self, code: str) -> None:
        tree = ast.parse(code)
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        if "register" not in functions or len([name for name in functions if name != "register"]) != 1:
            raise ValueError("Der Entwurf muss genau eine Arbeitsfunktion und register() enthalten.")
        work_name = next(name for name in functions if name != "register")
        register_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register")
        if register_node.decorator_list or len(register_node.args.args) != 1 or register_node.args.args[0].arg != "registry":
            raise ValueError("register() muss genau den Parameter registry besitzen und darf keinen Decorator haben.")
        if len(register_node.body) != 1 or not isinstance(register_node.body[0], ast.Expr):
            raise ValueError("register() darf ausschließlich genau ein Tool registrieren.")
        register_call = register_node.body[0].value
        valid_register = (
            isinstance(register_call, ast.Call)
            and isinstance(register_call.func, ast.Attribute)
            and isinstance(register_call.func.value, ast.Name)
            and register_call.func.value.id == "registry"
            and register_call.func.attr == "register"
            and len(register_call.args) == 1
            and not register_call.keywords
            and isinstance(register_call.args[0], ast.Call)
            and isinstance(register_call.args[0].func, ast.Name)
            and register_call.args[0].func.id == "Tool"
        )
        if not valid_register:
            raise ValueError("register() muss exakt registry.register(Tool(...)) aufrufen.")
        tool_call = register_call.args[0]
        for value in [*tool_call.args, *(keyword.value for keyword in tool_call.keywords)]:
            if isinstance(value, ast.Name) and value.id == work_name:
                continue
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError):
                raise ValueError("Tool-Metadaten dürfen nur Literale und die Arbeitsfunktion enthalten.") from None
        allowed_top_level = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign)
        if any(not isinstance(node, allowed_top_level) for node in tree.body):
            raise ValueError("Auf Modulebene sind nur Imports, Funktionen und konstante Zuweisungen erlaubt.")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                raise ValueError("Decorator sind im Tool-Entwurf nicht erlaubt.")
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                if not names <= self.ALLOWED_IMPORTS or "core" in names:
                    raise ValueError("Nicht freigegebener Import im Tool-Entwurf.")
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in self.ALLOWED_IMPORTS:
                    raise ValueError("Nicht freigegebener Import im Tool-Entwurf.")
                if root == "core" and (node.module != "core.tool_registry" or {alias.name for alias in node.names} != {"Tool"}):
                    raise ValueError("Aus core darf ausschließlich Tool aus core.tool_registry importiert werden.")
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                raise ValueError("Dunder-Namen sind im Tool-Entwurf gesperrt.")
            if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr in self.FORBIDDEN_ATTRIBUTES):
                raise ValueError("Gefährlicher Attributzugriff im Tool-Entwurf.")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_CALLS:
                raise ValueError("Verbotener Funktionsaufruf im Tool-Entwurf.")
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                try:
                    ast.literal_eval(value)
                except (ValueError, TypeError):
                    raise ValueError("Zuweisungen auf Modulebene dürfen nur konstante Literale enthalten.") from None

    @staticmethod
    def _test_in_docker(code: str) -> None:
        temp_root = AI_DATA_ROOT / "Jude" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tool-", dir=temp_root) as tmp:
            source = Path(tmp) / "tool.py"
            source.write_text(code, encoding="utf-8")
            result = subprocess.run([
                "docker", "run", "--rm", "--entrypoint", "python3", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-v", f"{source}:/tool.py:ro",
                os.getenv("JUDE_SANDBOX_IMAGE", "ghcr.io/usestrix/strix-sandbox:1.0.0"),
                "-c", "compile(open('/tool.py', encoding='utf-8').read(), '/tool.py', 'exec')",
            ], capture_output=True, text=True, timeout=60, check=False)
            if result.returncode:
                raise RuntimeError(f"Sandbox-Test fehlgeschlagen: {result.stderr.strip()}")

    def _load(self, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(f"tools.generiert.{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.register(self.registry)

    def as_tool(self) -> Tool:
        return Tool(
            name="create_new_tool", description="Erstellt einen geprüften Tool-Entwurf; Aktivierung verlangt Nutzungsfreigabe.",
            func=self.generate_draft,
            param_schema={"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]},
        )
