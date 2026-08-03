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


def render_3d_objects(objects: list, background: list | None = None, title: str = "szene") -> dict:
    return _blender.render_spec({"objects": objects, "background": background}, title=title)


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
        name="render_3d_objects",
        description=("Rendert eine 3D-Szene lokal mit Blender aus einer einfachen Objektliste (zuverlässig, bevorzugt). "
                     "Jedes Objekt: shape (cube, sphere, cylinder, cone, torus, plane, monkey), location [x,y,z], "
                     "scale (Zahl oder [x,y,z]), color [r,g,b] 0-1, metallic 0-1, roughness 0-1. Optional background [r,g,b]."),
        func=render_3d_objects,
        param_schema={"type": "object", "properties": {
            "objects": {"type": "array", "items": {"type": "object", "properties": {
                "shape": {"type": "string", "enum": ["cube", "sphere", "cylinder", "cone", "torus", "plane", "monkey"]},
                "location": {"type": "array", "items": {"type": "number"}},
                "scale": {"type": "number"},
                "color": {"type": "array", "items": {"type": "number"}},
                "metallic": {"type": "number"}, "roughness": {"type": "number"},
            }, "required": ["shape"]}},
            "background": {"type": "array", "items": {"type": "number"}},
            "title": {"type": "string"},
        }, "required": ["objects"]},
    ))
    registry.register(Tool(
        name="render_3d_scene",
        description=("Fortgeschritten: rendert eine 3D-Szene lokal mit Blender aus einem vollständigen bpy-Skript "
                     "in 'blender_python' (ohne Import-/Renderaufruf). Nur nutzen, wenn render_3d_objects nicht ausreicht."),
        func=render_3d_scene,
        param_schema={"type": "object", "properties": {
            "blender_python": {"type": "string", "description": "bpy-Skript, das die Szene aufbaut"},
            "title": {"type": "string"},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "engine": {"type": "string", "enum": ["BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"]},
        }, "required": ["blender_python"]},
    ))
