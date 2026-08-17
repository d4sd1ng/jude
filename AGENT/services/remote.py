"""SSH/SCP zu freigegebenen Hosts – schlüsselbasiert, ohne Passworteingabe.

Erlaubte Hosts kommen aus ``JUDE_SSH_HOSTS`` (kommagetrennt) oder ersatzweise
aus den Host-Aliassen in ``~/.ssh/config``. Aufrufe laufen mit ``BatchMode=yes``
(keine Passwortabfrage – nur Schlüsselauthentifizierung) und werden über die
Bestätigungs-Warteschlange freigegeben.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from services.filesystem import resolve_path

_SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]


class SSHService:
    def allowed_hosts(self) -> list[str]:
        env = os.getenv("JUDE_SSH_HOSTS", "").strip()
        if env:
            return sorted({h.strip() for h in env.split(",") if h.strip()})
        hosts: set[str] = set()
        config = Path.home() / ".ssh" / "config"
        if config.is_file():
            for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"\s*Host\s+(.+)", line, re.IGNORECASE)
                if match:
                    hosts.update(h for h in match.group(1).split() if not any(c in h for c in "*?"))
        return sorted(hosts)

    def _check(self, host: str) -> None:
        allowed = self.allowed_hosts()
        if host not in allowed:
            raise PermissionError(
                f"SSH-Host nicht freigegeben: {host}. Erlaubt (JUDE_SSH_HOSTS): {', '.join(allowed) or 'keine'}"
            )

    def run(self, host: str, command: str, timeout: int = 120) -> dict:
        self._check(host)
        if not command.strip():
            raise ValueError("Kein Befehl angegeben.")
        process = subprocess.run(["ssh", *_SSH_OPTS, host, command],
                                 capture_output=True, text=True, timeout=timeout)
        return {"host": host, "exit_code": process.returncode,
                "output": (process.stdout + process.stderr)[-20000:].strip()}

    def transfer(self, host: str, direction: str, remote_path: str, local_path: str, timeout: int = 300) -> dict:
        self._check(host)
        if direction == "upload":
            local = resolve_path(local_path)
            if not local.is_file():
                raise FileNotFoundError(f"Lokale Datei fehlt: {local}")
            source, target = str(local), f"{host}:{remote_path}"
        elif direction == "download":
            local = resolve_path(local_path, for_write=True)  # nur unter AI-Data
            local.parent.mkdir(parents=True, exist_ok=True)
            source, target = f"{host}:{remote_path}", str(local)
        else:
            raise ValueError("direction muss 'upload' oder 'download' sein.")
        process = subprocess.run(["scp", *_SSH_OPTS, source, target],
                                 capture_output=True, text=True, timeout=timeout)
        if process.returncode:
            raise RuntimeError((process.stderr or process.stdout).strip() or "SCP fehlgeschlagen.")
        return {"host": host, "direction": direction, "remote": remote_path, "local": str(local)}
