from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from services.filesystem import AI_DATA_ROOT, BLOCKED_PARTS, read_text, resolve_path, write_text


class CodingService:
    def repositories(self) -> list[dict]:
        repos = []
        for git_dir in AI_DATA_ROOT.rglob(".git"):
            if any(part in BLOCKED_PARTS for part in git_dir.parts):
                continue
            root = git_dir.parent
            try:
                remote = self._run(root, ["git", "remote", "get-url", "origin"], check=False).strip()
                branch = self._run(root, ["git", "branch", "--show-current"], check=False).strip()
                dirty = bool(self._run(root, ["git", "status", "--porcelain"], check=False).strip())
                repos.append({"path": str(root), "remote": remote, "branch": branch, "dirty": dirty})
            except OSError:
                continue
        return sorted(repos, key=lambda item: item["path"])

    def status(self, repo: str) -> str:
        return self._run(self._repo(repo), ["git", "status", "--short", "--branch"])

    def diff(self, repo: str) -> str:
        return self._run(self._repo(repo), ["git", "diff", "--"])[-50000:]

    def read(self, path: str) -> str:
        return read_text(path)

    def write(self, path: str, content: str) -> str:
        return write_text(path, content)

    def create_branch(self, repo: str, branch: str) -> str:
        if not branch.startswith("codex/") or any(char.isspace() for char in branch):
            raise ValueError("Branches müssen mit codex/ beginnen.")
        root = self._repo(repo)
        self._run(root, ["git", "switch", "-c", branch])
        return branch

    def commit(self, repo: str, message: str, paths: list[str]) -> str:
        root = self._repo(repo)
        if not paths or not message.strip():
            raise ValueError("Commit benötigt Nachricht und explizite Pfade.")
        resolved = [str(resolve_path(root / path, for_write=True).relative_to(root)) for path in paths]
        self._run(root, ["git", "add", "--", *resolved])
        self._run(root, ["git", "commit", "-m", message])
        return self._run(root, ["git", "rev-parse", "HEAD"]).strip()

    def push(self, repo: str, branch: str) -> str:
        root = self._repo(repo)
        self._run(root, ["git", "push", "-u", "origin", branch])
        return f"origin/{branch}"

    def clone(self, url: str, name: str | None = None) -> str:
        if not re.match(r"^(https://|git@|ssh://)[\w.@:/~+-]+$", url):
            raise ValueError("Nur gültige https/ssh-Git-URLs sind erlaubt.")
        base = (name or url.rstrip("/").split("/")[-1]).removesuffix(".git")
        if not re.match(r"^[A-Za-z0-9._-]{1,80}$", base):
            raise ValueError("Ungültiger Zielname.")
        destination_root = AI_DATA_ROOT / "Projects"
        destination_root.mkdir(parents=True, exist_ok=True)
        target = resolve_path(destination_root / base, for_write=True)
        if target.exists():
            raise ValueError(f"Zielverzeichnis existiert bereits: {target}")
        self._run(destination_root, ["git", "clone", "--", url, str(target)])
        return str(target)

    def pull(self, repo: str) -> str:
        root = self._repo(repo)
        return self._run(root, ["git", "pull", "--ff-only"]).strip() or "Aktualisiert"

    def create_pr(self, repo: str, title: str, body: str, draft: bool = True) -> str:
        root = self._repo(repo)
        args = ["gh", "pr", "create", "--title", title, "--body", body]
        if draft:
            args.append("--draft")
        return self._run(root, args).strip()

    def test(self, repo: str) -> dict:
        root = self._repo(repo)
        commands: list[list[str]] = []
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").is_dir():
            project_python = root / ".venv" / "bin" / "python"
            commands.append([str(project_python) if project_python.is_file() else "python3", "-m", "pytest", "-q"])
        if (root / "package.json").exists():
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            if "test" in package.get("scripts", {}):
                runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
                commands.append([runner, "test"])
        if (root / "Cargo.toml").exists():
            commands.append(["cargo", "test"])
        if (root / "go.mod").exists():
            commands.append(["go", "test", "./..."])
        if not commands:
            return {"status": "not_configured", "commands": [], "results": []}
        results = []
        for command in commands:
            process = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=900)
            results.append({"command": command, "exit_code": process.returncode, "output": (process.stdout + process.stderr)[-30000:]})
        return {"status": "passed" if all(r["exit_code"] == 0 for r in results) else "failed", "commands": commands, "results": results}

    def merge_confirmed(self, repo: str, pr_number: int) -> str:
        root = self._repo(repo)
        return self._run(root, ["gh", "pr", "merge", str(pr_number), "--merge"]).strip() or f"PR {pr_number} gemerged"

    def delete_confirmed(self, path: str) -> str:
        target = resolve_path(path, for_write=True)
        if target.is_dir():
            raise ValueError("Verzeichnislöschung wird nicht unterstützt.")
        target.unlink()
        return f"{target} gelöscht"

    @staticmethod
    def _repo(repo: str) -> Path:
        root = resolve_path(repo, for_write=True)
        if not (root / ".git").exists():
            raise ValueError("Kein Git-Repository.")
        return root

    @staticmethod
    def _run(cwd: Path, args: list[str], check: bool = True) -> str:
        process = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=900)
        if check and process.returncode:
            raise RuntimeError((process.stderr or process.stdout).strip())
        return process.stdout
