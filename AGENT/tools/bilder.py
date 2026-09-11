from __future__ import annotations

from core.tool_registry import Tool, ToolRegistry
from services.images import ImageService
from services.render3d import BlenderService
from services.vision import VisionService

_images = ImageService()
_blender = BlenderService()
_vision = VisionService()


def analyze_image(path: str, question: str = "Beschreibe dieses Bild genau auf Deutsch.") -> dict:
    return _vision.describe_path(path, question)


def generate_image(prompt: str, size: str = "1024x1024", marke: bool = True) -> dict:
    return _images.generate(prompt, size, marke=marke)


def _svg_speichern(svg: str, kind: str, breite: int, hoehe: int) -> dict:
    """SVG-Grafiken (infografik/onepager) ablegen wie generate_image – und zu
    PNG rendern.

    Die reine SVG-Ausgabe war mit "kein Rasterizer installiert" begruendet.
    Das galt fuer die Python-Bibliotheken; Playwrights Chromium liegt aber auf
    der Maschine und rendert SVG samt der echten Marken-Schriften (siehe
    services/rasterizer.py). Gemessen 05.09.2026: 1080x1350 in 0,6 s.

    Das SVG bleibt neben dem PNG liegen – darin ist der Text noch editierbar.
    Abgenommen wird das PNG: es ist das, was Tino in der Vorschau sieht, und
    was jedes Zielsystem ohne Umweg annimmt.
    """
    import uuid
    from datetime import datetime, timezone
    from core.paths import IMAGES_DIR
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rumpf = f"{stamp}_{kind}_{uuid.uuid4().hex[:8]}"
    svg_pfad = IMAGES_DIR / f"{rumpf}.svg"
    svg_pfad.write_text(svg, encoding="utf-8")

    from services.rasterizer import svg_zu_png
    try:
        png_pfad = svg_zu_png(svg, breite, hoehe, name=f"{rumpf}.png")
    except Exception as exc:
        # Kein stiller Rueckfall: der Mitarbeiter muss erfahren, dass er nur
        # ein SVG in der Hand haelt, sonst legt er es als "Grafik" vor und die
        # Vorschau bleibt leer.
        return {"path": str(svg_pfad), "file": svg_pfad.name, "kind": kind,
                "svg": str(svg_pfad), "png": None,
                "hinweis": f"SVG erzeugt, aber nicht zu PNG gerendert: {exc}"}
    return {"path": str(png_pfad), "file": png_pfad.name, "kind": kind,
            "png": str(png_pfad), "svg": str(svg_pfad),
            "hinweis": "PNG als Erzeugnis vorlegen; das SVG daneben bleibt aenderbar."}


def grafik_infografik(kicker: str, titel: str, kennzahlen: list, fussnote: str = "",
                      breite: int = 1080, hoehe: int = 1350) -> dict:
    from services.marke import infografik
    svg = infografik(kicker, titel, kennzahlen, fussnote, breite, hoehe)
    return _svg_speichern(svg, "infografik", breite, hoehe)


def grafik_onepager(titel: str, untertitel: str, abschnitte: list, abschluss: str = "",
                    breite: int = 1240, hoehe: int = 1754) -> dict:
    from services.marke import onepager
    svg = onepager(titel, untertitel, abschnitte, abschluss, breite, hoehe)
    return _svg_speichern(svg, "onepager", breite, hoehe)


def render_3d_scene(blender_python: str, title: str = "szene", width: int = 1024,
                    height: int = 1024, engine: str = "BLENDER_EEVEE_NEXT") -> dict:
    return _blender.render(blender_python, title=title, width=width, height=height, engine=engine)


def render_3d_objects(objects: list, background: list | None = None, title: str = "szene") -> dict:
    return _blender.render_spec({"objects": objects, "background": background}, title=title)


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="analyze_image",
        description=("Versteht ein gegebenes Bild: beschreibt es oder beantwortet eine Frage dazu. "
                     "'path' ist der Pfad zu einer Bilddatei unter AI-Data (z.B. unter Jude/images)."),
        func=analyze_image,
        param_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "Pfad zur Bilddatei"},
            "question": {"type": "string", "description": "Was über das Bild wissen? Optional."},
        }, "required": ["path"]},
    ))
    registry.register(Tool(
        name="generate_image",
        description=("Erzeugt ein Bild aus einer Textbeschreibung über OpenAI (gpt-image-2.5-flare) und speichert es "
                     "lokal unter Jude/images. Der Nurovelle-Bildstil (Farben, Material, Verbote) wird automatisch "
                     "an jeden Prompt angehängt (marke=true, Standard) – nicht selbst wiederholen. Nur für "
                     "ausdrücklich private/markenfremde Bilder marke=false setzen."),
        func=generate_image,
        param_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Bildbeschreibung (Motiv/Szene – der Markenstil kommt automatisch dazu)"},
            "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024", "auto"]},
            "marke": {"type": "boolean", "description": "Nurovelle-Bildstil automatisch anhängen. Standard true."},
        }, "required": ["prompt"]},
    ))
    registry.register(Tool(
        name="grafik_infografik",
        description=("Erzeugt eine Infografik als SVG-Datei mit echtem, verlaesslich lesbarem Text - "
                     "Wortmarke 'Nurovelle' und Tagline stehen automatisch unten, Kicker/Titel/bis zu vier "
                     "Kennzahlen kommen von dir. Nutze das statt generate_image, wenn die Grafik selbst "
                     "Text/Zahlen zeigen muss - ein Bildgenerator kann Text nicht zuverlaessig rendern, "
                     "dieses Werkzeug schon (echtes SVG-Text-Element, kein generiertes Pixelbild)."),
        func=grafik_infografik,
        param_schema={"type": "object", "properties": {
            "kicker": {"type": "string", "description": "Kurze Kategoriezeile ueber dem Titel."},
            "titel": {"type": "string"},
            "kennzahlen": {"type": "array", "items": {"type": "object", "properties": {
                "wert": {"type": "string"}, "label": {"type": "string"},
                "icon": {"type": "string", "description": "Optional: Pfad zu einer Linien-Icon-Datei im "
                         "Homepage-Repo, z.B. 'Bilder/icons/Vorteile/3.png' (Ordner: branche/1-9, "
                         "service/1-9, Vorteile/1-5) - erscheint klein neben dieser Kennzahl."},
            }}, "description": "Bis zu vier {wert, label, icon?} Paare, z.B. {'wert':'8 h','label':'Dokumentation pro Woche'}."},
            "fussnote": {"type": "string"},
            "breite": {"type": "integer"}, "hoehe": {"type": "integer"},
        }, "required": ["kicker", "titel", "kennzahlen"]},
    ))
    registry.register(Tool(
        name="grafik_onepager",
        description=("Erzeugt einen Onepager (A4-Format) als SVG-Datei mit echtem, verlaesslich lesbarem "
                     "Text - Wortmarke steht automatisch oben, Abschnitte mit Kopf+Text kommen von dir. "
                     "Nutze das statt generate_image, wenn ein Dokument/eine Grafik echten Fliesstext samt "
                     "Ueberschriften braucht."),
        func=grafik_onepager,
        param_schema={"type": "object", "properties": {
            "titel": {"type": "string"}, "untertitel": {"type": "string"},
            "abschnitte": {"type": "array", "items": {"type": "object", "properties": {
                "kopf": {"type": "string"}, "text": {"type": "string"},
                "icon": {"type": "string", "description": "Optional: Pfad zu einer Linien-Icon-Datei im "
                         "Homepage-Repo, z.B. 'Bilder/icons/service/2.png' (Ordner: branche/1-9, "
                         "service/1-9, Vorteile/1-5) - erscheint klein neben diesem Abschnittskopf."},
            }}, "description": "Bis zu fuenf {kopf, text, icon?} Abschnitte."},
            "abschluss": {"type": "string", "description": "Optionale hervorgehobene Schlusszeile/CTA."},
            "breite": {"type": "integer"}, "hoehe": {"type": "integer"},
        }, "required": ["titel", "untertitel", "abschnitte"]},
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
