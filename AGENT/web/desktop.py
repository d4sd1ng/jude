from __future__ import annotations

import threading
import time


def run_desktop(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn
    import webview
    server = uvicorn.Server(uvicorn.Config("web.app:app", host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.8)
    webview.create_window("Jude", f"http://{host}:{port}", width=1280, height=850, min_size=(820, 600))
    webview.start()
    server.should_exit = True
