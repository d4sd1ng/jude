from __future__ import annotations

from core.tool_registry import Tool, ToolRegistry
from services.images import ImageService
from services.render3d import BlenderService

_images = ImageService()
_blender = BlenderService()


def generate_image(prompt: str, size: str = "1024x1024") -> dict:
    return _images.generate(prompt, size)


def render_3d_scene(blender_python: str, title: str = "szene", width: int = 1024,
                    height: int = 1024, engine: str = "BLENDER_EEVEE_NEXT") -> dict:
    return _blender.render(blender_python, title=title, width=width, height=height, engine=engine)


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="generate_image",
        description="Erzeugt ein Bild aus einer Textbeschreibung über OpenAI (gpt-image-1) und speichert es lokal unter Jude/images.",
        func=generate_image,
        param_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Bildbeschreibung"},
            "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024", "auto"]},
        }, "required": ["prompt"]},
    ))
    registry.register(Tool(
        name="render_3d_scene",
        description=("Rendert eine 3D-Szene lokal und privat mit Blender. Übergib in 'blender_python' ein "
                     "vollständiges bpy-Skript, das Objekte, Materialien, Kamera und Licht aufbaut (ohne Import- "
                     "oder Renderaufruf – das übernimmt Jude). Ideal für 3D-Objekte, Produktrenders, Icons oder Diagramme."),
        func=render_3d_scene,
        param_schema={"type": "object", "properties": {
            "blender_python": {"type": "string", "description": "bpy-Skript, das die Szene aufbaut"},
            "title": {"type": "string"},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "engine": {"type": "string", "enum": ["BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"]},
        }, "required": ["blender_python"]},
    ))
