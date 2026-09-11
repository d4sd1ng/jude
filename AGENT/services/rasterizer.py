"""SVG zu PNG rendern – mit dem Chromium, der ohnehin auf der Maschine liegt.

Der Commit vom 05.09.2026, der ``grafik_infografik``/``grafik_onepager``
verdrahtet hat, begruendet die reine SVG-Ausgabe mit "No SVG rasterizer
(cairosvg etc.) is installed". Fuer die Python-Bibliotheken stimmt das, aber
Playwright hat einen vollstaendigen Chromium unter ``~/.cache/ms-playwright/``
abgelegt. Der rendert SVG originalgetreu – gemessen 05.09.2026: 1080x1350 in
0,6 s – und braucht keine Nachinstallation.

Zwei Dinge, die ein reiner SVG-Konverter nicht loesen wuerde:

* **Die Marken-Schriften.** Die SVGs verlangen ``'Exo 2'`` und ``Inter``.
  Beide sind nicht systemweit installiert, liegen aber als woff2 im
  Homepage-Repo. Ohne sie faellt jede Ueberschrift auf eine Ersatzschrift
  zurueck und die Grafik ist nicht mehr markenkonform. Deshalb wird das SVG
  in eine HTML-Seite mit ``@font-face`` eingebettet, statt Chromium direkt
  auf die .svg-Datei zu zeigen.
* **Nachpruefbarkeit.** Das SVG bleibt neben dem PNG liegen: Text darin ist
  editierbar, das PNG ist das, was abgenommen wird.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import logging

from core.paths import AI_DATA_ROOT, IMAGES_DIR

logger = logging.getLogger(__name__)

#: Wo die Marken-Schriften liegen. Ueber AI_DATA_ROOT abgeleitet, nicht
#: absolut – Jude laeuft von derselben Platte unter Debian und Windows.
SCHRIFTEN_DIR = (AI_DATA_ROOT / "Projects" / "homepage_repo" / "homepage"
                 / "assets" / "fonts")

#: Reihenfolge der Suche nach einem Chromium. JUDE_CHROME sticht alles.
_CHROME_KANDIDATEN = (
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
)


def chrome_finden() -> Path | None:
    """Ein benutzbares Chromium – aus JUDE_CHROME, dem PATH oder Playwright."""
    import os
    gesetzt = os.getenv("JUDE_CHROME", "").strip()
    if gesetzt and Path(gesetzt).is_file():
        return Path(gesetzt)
    for name in _CHROME_KANDIDATEN:
        gefunden = shutil.which(name)
        if gefunden:
            return Path(gefunden)
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        # Neuestes chromium-<build> zuerst, damit ein Update automatisch greift.
        for ordner in sorted(cache.glob("chromium-*"), reverse=True):
            binaer = ordner / "chrome-linux64" / "chrome"
            if binaer.is_file():
                return binaer
            binaer = ordner / "chrome-linux" / "chrome"
            if binaer.is_file():
                return binaer
    return None


def verfuegbar() -> bool:
    return chrome_finden() is not None


def _schriften_css() -> str:
    """@font-face fuer die echten Marken-Schriften, sofern vorhanden."""
    regeln = []
    for familie, datei in (("Exo 2", "exo2-var-latin.woff2"),
                           ("Inter", "inter-var-latin.woff2")):
        pfad = SCHRIFTEN_DIR / datei
        if pfad.is_file():
            regeln.append(
                f"@font-face{{font-family:'{familie}';"
                f"src:url('{pfad.as_uri()}') format('woff2');"
                f"font-weight:100 900;font-display:block}}")
        else:
            logger.warning("Marken-Schrift fehlt, Ersatzschrift wird benutzt: %s", pfad)
    return "".join(regeln)


def svg_zu_png(svg: str, breite: int, hoehe: int, name: str | None = None,
               timeout_s: int = 60) -> Path:
    """Rendert ein SVG zu PNG unter ``Jude/images`` und gibt den Pfad zurueck."""
    chrome = chrome_finden()
    if chrome is None:
        raise RuntimeError(
            "Kein Chromium gefunden. Erwartet unter ~/.cache/ms-playwright/, im "
            "PATH oder ueber die Umgebungsvariable JUDE_CHROME.")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    stempel = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ziel = IMAGES_DIR / (name or f"{stempel}_grafik_{uuid.uuid4().hex[:8]}.png")

    seite = (f"<!doctype html><meta charset='utf-8'><style>{_schriften_css()}"
             f"html,body{{margin:0;padding:0;width:{breite}px;height:{hoehe}px;"
             f"overflow:hidden}}svg{{display:block}}</style>{svg}")

    # KEIN --user-data-dir: mit einem frischen Profilordner haengt dieser
    # Chromium und laeuft ins Zeitlimit, ohne ihn rendert derselbe Aufruf in
    # 0,75 s (gemessen 05.09.2026, 25 s Abbruch gegen 0,75 s Erfolg).
    tmp = Path(tempfile.mkdtemp(prefix="jude-raster-"))
    try:
        quelle = tmp / "grafik.html"
        quelle.write_text(seite, encoding="utf-8")
        befehl = [
            str(chrome), "--headless", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=1",
            f"--window-size={breite},{hoehe}",
            f"--screenshot={ziel}", quelle.as_uri(),
        ]
        lauf = subprocess.run(befehl, capture_output=True, timeout=timeout_s)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not ziel.is_file() or ziel.stat().st_size == 0:
        fehler = (lauf.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise RuntimeError(f"Chromium hat kein Bild geschrieben. {fehler}")
    return ziel
