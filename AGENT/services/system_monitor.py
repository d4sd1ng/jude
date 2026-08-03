"""Lokale Systemwerte für das Cockpit: CPU, Speicher, Netzwerk, Temperaturen.

Liest ausschließlich /proc und /sys – keine Zusatzabhängigkeiten. Raten
(CPU-Auslastung, Netzwerkdurchsatz) werden aus der Differenz zur letzten
Abfrage gebildet, der Zustand dafür liegt in der Service-Instanz.
"""

from __future__ import annotations

import time
from pathlib import Path


class SystemMonitorService:
    def __init__(self) -> None:
        self._last_cpu: tuple[float, float] | None = None      # (busy, total)
        self._last_net: tuple[float, int, int] | None = None   # (ts, rx, tx)

    # ------------------------------------------------------------- CPU

    def _cpu_percent(self) -> float:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [float(x) for x in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
        total = sum(values)
        busy = total - idle
        percent = 0.0
        if self._last_cpu is not None:
            last_busy, last_total = self._last_cpu
            delta_total = total - last_total
            if delta_total > 0:
                percent = max(0.0, min(100.0, (busy - last_busy) / delta_total * 100.0))
        self._last_cpu = (busy, total)
        return round(percent, 1)

    # ---------------------------------------------------------- Speicher

    @staticmethod
    def _memory() -> dict:
        info: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0])
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available
        return {"total_mb": total // 1024, "used_mb": used // 1024,
                "percent": round(used / total * 100.0, 1) if total else 0.0}

    # ---------------------------------------------------------- Netzwerk

    def _network(self) -> dict:
        rx = tx = 0
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, rest = line.partition(":")
            if name.strip() == "lo":
                continue
            parts = rest.split()
            rx += int(parts[0])
            tx += int(parts[8])
        now = time.monotonic()
        rx_rate = tx_rate = 0.0
        if self._last_net is not None:
            last_ts, last_rx, last_tx = self._last_net
            elapsed = now - last_ts
            if elapsed > 0:
                rx_rate = max(0.0, (rx - last_rx) / elapsed)
                tx_rate = max(0.0, (tx - last_tx) / elapsed)
        self._last_net = (now, rx, tx)
        return {"rx_kbps": round(rx_rate / 1024.0, 1), "tx_kbps": round(tx_rate / 1024.0, 1)}

    # ------------------------------------------------------ Temperaturen

    @staticmethod
    def _temperatures() -> list[dict]:
        readings: list[dict] = []
        for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            try:
                chip = (hwmon / "name").read_text().strip()
            except OSError:
                continue
            for sensor in sorted(hwmon.glob("temp*_input")):
                try:
                    value = int(sensor.read_text().strip()) / 1000.0
                except (OSError, ValueError):
                    continue
                label_path = hwmon / sensor.name.replace("_input", "_label")
                try:
                    label = label_path.read_text().strip()
                except OSError:
                    label = sensor.name.replace("_input", "")
                readings.append({"chip": chip, "label": label, "celsius": round(value, 1)})
        return readings

    @classmethod
    def _pick(cls, readings: list[dict], chips: tuple[str, ...]) -> float | None:
        candidates = [r["celsius"] for r in readings if r["chip"] in chips]
        return max(candidates) if candidates else None

    # -------------------------------------------------------------- API

    def snapshot(self) -> dict:
        temps = self._temperatures()
        load1, load5, load15 = (float(x) for x in Path("/proc/loadavg").read_text().split()[:3])
        return {
            "cpu_percent": self._cpu_percent(),
            "load": {"1m": load1, "5m": load5, "15m": load15},
            "memory": self._memory(),
            "network": self._network(),
            "temperatures": {
                "cpu": self._pick(temps, ("k10temp", "coretemp", "zenpower")),
                "gpu": self._pick(temps, ("amdgpu", "nouveau", "nvidia")),
                "nvme": self._pick(temps, ("nvme",)),
                "all": temps,
            },
        }
