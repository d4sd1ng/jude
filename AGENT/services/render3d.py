"""Lokales 3D-Rendern mit Blender (headless).

Der Agent liefert ein Blender-Python-Skript (``bpy``), das eine Szene aufbaut.
Dieser Dienst kapselt es in ein Gerüst, das Render-Auflösung, Engine und
Ausgabepfad setzt, und ruft Blender im Hintergrund auf. Das Ergebnis landet als
PNG mit Metadaten unter ``Jude/images``.

Die Skriptausführung erfolgt lokal auf dem Rechner des Nutzers – wie beim
Tool-Creator wird generierter Python-Code ausgeführt; ein Timeout und
``--factory-startup`` (keine Nutzer-Add-ons) begrenzen den Rahmen.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone

from core.paths import IMAGES_DIR

_WRAPPER = '''
import bpy, sys
_out = sys.argv[-1]

# Leere Standardszene und setze reproduzierbare Render-Vorgaben.
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "{engine}"
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.film_transparent = {transparent}
scene.render.image_settings.file_format = "PNG"

def _build():
{indented_script}

_build()

# Fallback-Kamera und -Licht, falls das Skript keine gesetzt hat.
if scene.camera is None:
    cam_data = bpy.data.cameras.new("JudeCam")
    cam = bpy.data.objects.new("JudeCam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (7.0, -7.0, 5.0)
    cam.rotation_euler = (1.109, 0.0, 0.785)
    scene.camera = cam
if not any(o.type == "LIGHT" for o in scene.objects):
    light_data = bpy.data.lights.new("JudeSun", type="SUN")
    light = bpy.data.objects.new("JudeSun", light_data)
    scene.collection.objects.link(light)
    light.location = (5.0, -5.0, 8.0)

scene.render.filepath = _out
bpy.ops.render.render(write_still=True)
'''


class BlenderService:
    def __init__(self, blender: str | None = None, timeout: int = 180):
        self.blender = blender or os.getenv("BLENDER_BIN") or shutil.which("blender") or "blender"
        self.timeout = timeout

    def available(self) -> bool:
        return bool(shutil.which(self.blender) or os.path.isfile(self.blender))

    def render(self, blender_python: str, title: str = "szene",
               width: int = 1024, height: int = 1024,
               engine: str = "BLENDER_EEVEE_NEXT", transparent: bool = False) -> dict:
        if not blender_python.strip():
            raise ValueError("Es wurde kein Blender-Skript übergeben.")
        if not self.available():
            raise RuntimeError("Blender wurde nicht gefunden (BLENDER_BIN setzen oder installieren).")
        engine = engine if engine in {"BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"} else "BLENDER_EEVEE_NEXT"

        indented = "\n".join("    " + line for line in blender_python.splitlines()) or "    pass"
        script = _WRAPPER.format(engine=engine, width=int(width), height=int(height),
                                 transparent=bool(transparent), indented_script=indented)

        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_title = "".join(c for c in title if c.isalnum() or c in "-_") or "szene"
        name = f"{stamp}_blender_{safe_title}_{uuid.uuid4().hex[:6]}.png"
        out_path = IMAGES_DIR / name

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
            handle.write(script)
            script_path = handle.name
        try:
            result = subprocess.run(
                [self.blender, "--background", "--factory-startup", "--python", script_path, "--", str(out_path)],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Blender-Render-Timeout nach {self.timeout}s.") from exc
        finally:
            os.unlink(script_path)

        if not out_path.is_file():
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-8:]
            raise RuntimeError("Blender hat kein Bild erzeugt:\n" + "\n".join(tail))

        meta = {"file": name, "kind": "blender", "title": title, "engine": engine,
                "size": f"{width}x{height}", "source": "Blender lokal",
                "created_at": datetime.now(timezone.utc).isoformat()}
        out_path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(out_path), **meta}
