"""DWD-Regenradar (RV-Komposit): 30 min Rückschau, 90 min Vorhersage.

RainViewer liefert vorwärts höchstens 30 Minuten und zeitweise gar nichts –
zu wenig. Der DWD stellt frei sein RV-Komposit bereit: deutschlandweites
Radar (Grid DE1200, 1 km, 5-Minuten-Takt) mit 2 Stunden Nowcast. Jeder Lauf
ist ein tar.bz2 mit 25 RADOLAN-Binärdateien (_000 Analyse … _120 Vorhersage).

Dekodierung (RADOLAN-Spezifikation):
- ASCII-Header bis ETX (0x03), danach little-endian uint16 je Rasterzelle
- Bit 13 (0x2000) = außerhalb der Radarabdeckung; Wertebits 0–11
- Wert × 0,01 = mm je 5 min (Header ``PR E-02`` / ``INT 5``)

Projektion: polarstereographisch (Kugel R=6370,04 km, Bezug 60°N/10°E),
Gitterursprung Südwest bei x=-543,462 km, y=-4808,645 km (DE1200, wradlib).
Gerendert wird je Frame ein RGBA-PNG in Web-Mercator-Ausrichtung, das die
GUI als Leaflet-ImageOverlay über die Karte legt – exakt georeferenziert
über die feste Bounding-Box BOUNDS.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
import threading
import time
from datetime import datetime, timezone

import numpy as np
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://opendata.dwd.de/weather/radar/composite/rv/"
GRID_COLS, GRID_ROWS = 1100, 1200
X0_KM, Y0_KM = -543.4622, -4808.645          # Südwest-Ecke des DE1200-Gitters
EARTH_KM = 6370.04
# Ausgabe-Bounding-Box (umfasst das gesamte DE1200-Gebiet großzügig)
BOUNDS = {"lat_min": 45.6, "lat_max": 56.0, "lon_min": 1.2, "lon_max": 19.0}
OUT_W = OUT_H = 1100
PAST_RUNS = 18                                # 18 × 5 min = 90 min Rückschau
FORECAST_MAX_MIN = 90                         # 1,5 h Vorhersage
# Intensitätsstufen in mm/h -> RGBA (blau -> gelb -> rot -> magenta)
SCALE = [(0.1, (110, 170, 255, 120)), (0.3, (55, 125, 255, 150)), (1.0, (0, 190, 150, 170)),
         (2.0, (255, 220, 0, 185)), (5.0, (255, 150, 0, 200)), (10.0, (255, 60, 0, 215)),
         (20.0, (205, 0, 90, 225)), (50.0, (255, 0, 255, 235))]


def _sample_index() -> np.ndarray:
    """Für jedes Ausgabepixel (Web-Mercator-linear über BOUNDS) den Index im
    DE1200-Gitter vorberechnen; -1 markiert Pixel außerhalb des Gitters."""
    lon = np.linspace(BOUNDS["lon_min"], BOUNDS["lon_max"], OUT_W)
    merc = lambda lat: np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    y_lin = np.linspace(merc(BOUNDS["lat_max"]), merc(BOUNDS["lat_min"]), OUT_H)
    lat = np.degrees(2 * np.arctan(np.exp(y_lin)) - np.pi / 2)
    lat_g, lon_g = np.meshgrid(np.radians(lat), np.radians(lon), indexing="ij")
    m = (1 + np.sin(np.radians(60.0))) / (1 + np.sin(lat_g))
    x = EARTH_KM * m * np.cos(lat_g) * np.sin(lon_g - np.radians(10.0))
    y = -EARTH_KM * m * np.cos(lat_g) * np.cos(lon_g - np.radians(10.0))
    col = np.floor(x - X0_KM).astype(np.int32)
    row = np.floor(y - Y0_KM).astype(np.int32)   # Zeile 0 = Süd
    ok = (col >= 0) & (col < GRID_COLS) & (row >= 0) & (row < GRID_ROWS)
    index = np.where(ok, row * GRID_COLS + col, -1)
    return index


def _decode(blob: bytes) -> np.ndarray:
    """RADOLAN-Binärdatei -> mm/h als float16-Fläche (NaN außerhalb Abdeckung)."""
    etx = blob.index(b"\x03")
    data = np.frombuffer(blob, dtype="<u2", offset=etx + 1)
    if data.size != GRID_COLS * GRID_ROWS:
        raise ValueError(f"Unerwartete Rastergröße: {data.size}")
    values = (data & 0x0FFF).astype(np.float32) * 0.01 * 12.0   # mm/5min -> mm/h
    values[(data & 0x2000) != 0] = np.nan
    return values.astype(np.float16)


class DWDRadarService:
    def __init__(self):
        self._index = None
        self._lock = threading.Lock()
        self._analyses: dict[str, np.ndarray] = {}    # Lauf-Zeit -> Analyse (_000)
        self._forecasts: dict[int, np.ndarray] = {}   # Minuten-Offset -> Fläche
        self._latest_run: str | None = None
        self._pngs: dict[str, bytes] = {}
        self._fetched_at = 0.0

    # ------------------------------------------------------------ Beschaffung

    @staticmethod
    def _list_runs() -> list[str]:
        response = requests.get(BASE_URL, timeout=20)
        response.raise_for_status()
        stamps = sorted(set(re.findall(r"DE1200_RV(\d{10})\.tar\.bz2", response.text)))
        return stamps

    @staticmethod
    def _fetch_run(stamp: str, offsets: set[int]) -> dict[int, np.ndarray]:
        response = requests.get(f"{BASE_URL}DE1200_RV{stamp}.tar.bz2", timeout=60)
        response.raise_for_status()
        result: dict[int, np.ndarray] = {}
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:bz2") as archive:
            for member in archive.getmembers():
                match = re.search(r"_(\d{3})$", member.name)
                if not match or int(match.group(1)) not in offsets:
                    continue
                result[int(match.group(1))] = _decode(archive.extractfile(member).read())
        return result

    def _refresh(self) -> None:
        if time.monotonic() - self._fetched_at < 240 and self._latest_run:
            return
        stamps = self._list_runs()
        if not stamps:
            raise RuntimeError("DWD-Verzeichnis liefert keine Läufe.")
        wanted = stamps[-(PAST_RUNS + 1):]
        # Rückschau-Analysen parallel holen: 18 sequenzielle Downloads kosteten
        # beim Kaltstart 15-30 s, parallel sind es wenige Sekunden.
        missing = [s for s in wanted[:-1] if s not in self._analyses]
        if missing:
            from concurrent.futures import ThreadPoolExecutor
            def _one(stamp):
                try:
                    return stamp, self._fetch_run(stamp, {0})[0]
                except Exception as exc:                # einzelner Altlauf fehlt: egal
                    logger.warning("DWD-Lauf %s nicht ladbar: %s", stamp, exc)
                    return stamp, None
            with ThreadPoolExecutor(max_workers=8) as pool:
                for stamp, field in pool.map(_one, missing):
                    if field is not None:
                        self._analyses[stamp] = field
        latest = wanted[-1]
        if latest != self._latest_run:
            offsets = {0} | set(range(5, FORECAST_MAX_MIN + 1, 5))
            run = self._fetch_run(latest, offsets)
            self._analyses[latest] = run.pop(0)
            self._forecasts = run
            self._latest_run = latest
            self._pngs.clear()
        for stale in [s for s in self._analyses if s not in wanted]:
            del self._analyses[stale]
        self._fetched_at = time.monotonic()

    # -------------------------------------------------------------- Rendering

    def _render(self, field: np.ndarray) -> bytes:
        from PIL import Image
        if self._index is None:
            self._index = _sample_index()
        flat = np.append(field.astype(np.float32), np.nan)      # -1 -> NaN-Pixel
        sampled = flat[self._index]
        rgba = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
        for threshold, color in SCALE:
            rgba[sampled >= threshold] = color
        png = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(png, "PNG", optimize=False)
        return png.getvalue()

    # ------------------------------------------------------------------- API

    @staticmethod
    def _stamp_time(stamp: str, plus_min: int = 0) -> int:
        base = datetime.strptime(stamp, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
        return int(base.timestamp()) + plus_min * 60

    def warmup(self) -> None:
        """Cache im Hintergrund füllen (Downloads + alle PNG-Frames), damit der
        erste GUI-Aufruf nicht auf den DWD warten muss. Fire-and-forget."""
        def _run():
            try:
                for frame in self.frames()["frames"]:
                    self.png(frame["key"])
            except Exception as exc:
                logger.warning("Radar-Warmup fehlgeschlagen: %s", exc)
        threading.Thread(target=_run, name="dwd-radar-warmup", daemon=True).start()

    def frames(self) -> dict:
        with self._lock:
            self._refresh()
            keys = []
            for stamp in sorted(self._analyses):
                kind = "now" if stamp == self._latest_run else "past"
                keys.append({"key": f"a{stamp}", "time": self._stamp_time(stamp), "kind": kind})
            for offset in sorted(self._forecasts):
                keys.append({"key": f"f{offset:03d}", "time": self._stamp_time(self._latest_run, offset),
                             "kind": "forecast"})
        return {"mode": "dwd", "frames": keys, "bounds": BOUNDS, "source": "DWD RV-Komposit",
                "interval_min": 5, "status": "live"}

    def png(self, key: str) -> bytes:
        with self._lock:
            self._refresh()
            if key in self._pngs:
                return self._pngs[key]
            if key.startswith("a") and key[1:] in self._analyses:
                field = self._analyses[key[1:]]
            elif key.startswith("f") and int(key[1:]) in self._forecasts:
                field = self._forecasts[int(key[1:])]
            else:
                raise KeyError(f"Unbekannter Radar-Frame: {key}")
            image = self._render(field)
            self._pngs[key] = image
            return image
