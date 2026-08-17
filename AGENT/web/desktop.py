from __future__ import annotations

import shutil
import subprocess
import threading
import time
import os
import sys


def _has_webview_backend() -> bool:
    """pywebview braucht ein GTK- (gi) oder Qt-Binding. Fehlt beides (typisch in
    einer venv ohne Systempakete), würde webview.start() abstürzen."""
    for module in ("gi", "PyQt6", "PyQt5", "PySide6", "PySide2"):
        try:
            __import__(module)
            return True
        except Exception:
            continue
    return False


def _open_browser_app(url: str) -> bool:
    for browser in ("brave-browser", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(browser)
        if path:
            subprocess.Popen([path, f"--app={url}", "--class=Jude"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


def run_desktop(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config("web.app:app", host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.8)
    url = f"http://{host}:{port}"

    # Unter Linux hat pywebview je nach Backend/Distribution vereinzelt leere,
    # hängende Fenster erzeugt. Daher zuerst den Browser-App-Weg nutzen.
    prefer_browser = (sys.platform.startswith("linux")
                      and os.getenv("JUDE_DESKTOP_USE_WEBVIEW", "").strip() not in {"1", "true", "yes", "on"})
    if prefer_browser and _open_browser_app(url):
        try:
            while not server.should_exit:
                time.sleep(1.0)
        except KeyboardInterrupt:
            server.should_exit = True
        return

    if _has_webview_backend():
        try:
            import webview
            webview.create_window("Jude", url, width=1280, height=850, min_size=(820, 600))
            webview.start()
            server.should_exit = True
            return
        except Exception:
            pass  # Fallback unten

    # Kein (funktionierendes) Fenster-Backend: Cockpit im Browser-App-Fenster
    # zeigen und den Server im Vordergrund weiterlaufen lassen.
    _open_browser_app(url)
    try:
        while not server.should_exit:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.should_exit = True
