"""Die Markenwerte von Nurovelle an einer Stelle – und Vorlagen, die sie einhalten.

Ausgelesen aus der Live-Seite nurovelle.de am 14.08.2026; dort standen sie als
CSS-Variablen, waren aber nirgends im System hinterlegt. Entsprechend beliebig
sah alles aus, was das Team erzeugte.

Warum Vorlagen statt Bildmodell: Onepager und Infografiken bestehen fast nur aus
Schrift, und Bildmodelle verhunzen Schrift zuverlässig. Als SVG gesetzt stimmen
Typografie, Farben und Abstände exakt, und eine Zahl lässt sich später ändern,
ohne das Bild neu zu erzeugen.
"""

from __future__ import annotations

from html import escape

#: Farbwerte der Marke.
SCHWARZ = "#1a1a1a"
SCHWARZ_MATT = "#1d1d1d"
GRUEN_TIEF = "#0f241c"
GRUEN_SMARAGD = "#112a20"
GRUEN_FOREST = "#10412e"
GRUEN_AKZENT = "#55863f"
GOLD_KANTE = "#a55d07"
GOLD_GLANZ = "#fae385"
GOLD_MITTE = "#b47e11"
HELL = "#e4e0d6"
HELL_GEDECKT = "#b9c3bd"

#: Schriften. Exo 2 für Überschriften, Inter für Fließtext – wie auf der Seite.
SCHRIFT_TITEL = "'Exo 2', 'Trebuchet MS', sans-serif"
SCHRIFT_TEXT = "Inter, 'DejaVu Sans', sans-serif"

CLAIM = "Building Intelligent Systems"

#: Gemeinsame Grundlage: Verlauf, Goldkante, Schriftimport.
_DEFS = f"""
  <defs>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#a54e07"/><stop offset="35%" stop-color="{GOLD_MITTE}"/>
      <stop offset="55%" stop-color="#fef1a2"/><stop offset="75%" stop-color="#bc881b"/>
      <stop offset="100%" stop-color="#a54e07"/>
    </linearGradient>
    <linearGradient id="grund" x1="0" y1="0" x2="0.6" y2="1">
      <stop offset="0%" stop-color="{GRUEN_TIEF}"/><stop offset="60%" stop-color="{SCHWARZ}"/>
      <stop offset="100%" stop-color="{SCHWARZ_MATT}"/>
    </linearGradient>
  </defs>
"""


def _kopf(breite: int, hoehe: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{breite}" height="{hoehe}" '
            f'viewBox="0 0 {breite} {hoehe}" font-family="{SCHRIFT_TEXT}">{_DEFS}'
            f'<rect width="{breite}" height="{hoehe}" fill="url(#grund)"/>'
            f'<rect x="0" y="0" width="{breite}" height="3" fill="url(#gold)"/>')


def _fuss(breite: int, hoehe: int) -> str:
    """Wortmarke unten links, Claim daneben – auf jedem Erzeugnis gleich."""
    return (f'<text x="56" y="{hoehe-42}" fill="{HELL}" font-family="{SCHRIFT_TITEL}" '
            f'font-size="26" font-weight="700" letter-spacing="1.5">Nurovelle</text>'
            f'<text x="56" y="{hoehe-22}" fill="{HELL_GEDECKT}" font-size="13" '
            f'letter-spacing="2.2">{CLAIM.upper()}</text></svg>')


def _umbruch(text: str, zeichen: int) -> list[str]:
    zeilen, zeile = [], ""
    for wort in str(text).split():
        if len(zeile) + len(wort) + 1 > zeichen and zeile:
            zeilen.append(zeile)
            zeile = wort
        else:
            zeile = f"{zeile} {wort}".strip()
    if zeile:
        zeilen.append(zeile)
    return zeilen


def infografik(kicker: str, titel: str, kennzahlen: list[dict],
               fussnote: str = "", breite: int = 1080, hoehe: int = 1350) -> str:
    """Eine Infografik mit bis zu vier Kennzahlen.

    ``kennzahlen``: ``[{"wert": "8 h", "label": "Dokumentation pro Woche"}, …]``
    Die Werte stehen in Gold, die Erläuterung darunter gedeckt – Gold bleibt der
    einzige Akzent, sonst kippt der Auftritt ins Werbliche.
    """
    teile = [_kopf(breite, hoehe)]
    teile.append(f'<text x="56" y="130" fill="{GRUEN_AKZENT}" font-size="20" '
                 f'font-weight="600" letter-spacing="3.4">{escape(kicker.upper())}</text>')
    y = 210
    for zeile in _umbruch(titel, 26)[:3]:
        teile.append(f'<text x="56" y="{y}" fill="{HELL}" font-family="{SCHRIFT_TITEL}" '
                     f'font-size="62" font-weight="700">{escape(zeile)}</text>')
        y += 74
    y += 40
    for eintrag in kennzahlen[:4]:
        teile.append(f'<rect x="56" y="{y-4}" width="4" height="86" fill="url(#gold)"/>')
        teile.append(f'<text x="86" y="{y+48}" fill="url(#gold)" '
                     f'font-family="{SCHRIFT_TITEL}" font-size="58" font-weight="800">'
                     f'{escape(str(eintrag.get("wert","")))}</text>')
        for i, zeile in enumerate(_umbruch(eintrag.get("label", ""), 44)[:2]):
            teile.append(f'<text x="86" y="{y+78+i*24}" fill="{HELL_GEDECKT}" '
                         f'font-size="21">{escape(zeile)}</text>')
        y += 148
    if fussnote:
        for i, zeile in enumerate(_umbruch(fussnote, 62)[:2]):
            teile.append(f'<text x="56" y="{hoehe-120+i*24}" fill="{HELL_GEDECKT}" '
                         f'font-size="18">{escape(zeile)}</text>')
    teile.append(_fuss(breite, hoehe))
    return "".join(teile)


def onepager(titel: str, untertitel: str, abschnitte: list[dict],
             abschluss: str = "", breite: int = 1240, hoehe: int = 1754) -> str:
    """Ein Onepager in A4-Verhältnis. ``abschnitte``: ``[{"kopf":…, "text":…}]``"""
    teile = [_kopf(breite, hoehe)]
    teile.append(f'<text x="72" y="128" fill="{GRUEN_AKZENT}" font-size="19" '
                 f'font-weight="600" letter-spacing="3.4">NUROVELLE</text>')
    y = 200
    for zeile in _umbruch(titel, 30)[:2]:
        teile.append(f'<text x="72" y="{y}" fill="{HELL}" font-family="{SCHRIFT_TITEL}" '
                     f'font-size="56" font-weight="700">{escape(zeile)}</text>')
        y += 68
    for zeile in _umbruch(untertitel, 62)[:3]:
        teile.append(f'<text x="72" y="{y+14}" fill="{HELL_GEDECKT}" font-size="24">'
                     f'{escape(zeile)}</text>')
        y += 34
    y += 60
    teile.append(f'<rect x="72" y="{y}" width="{breite-144}" height="2" fill="url(#gold)"/>')
    y += 56
    for abschnitt in abschnitte[:5]:
        teile.append(f'<text x="72" y="{y}" fill="url(#gold)" font-family="{SCHRIFT_TITEL}" '
                     f'font-size="30" font-weight="700">{escape(abschnitt.get("kopf",""))}</text>')
        y += 40
        for zeile in _umbruch(abschnitt.get("text", ""), 74)[:5]:
            teile.append(f'<text x="72" y="{y}" fill="{HELL}" font-size="21">'
                         f'{escape(zeile)}</text>')
            y += 30
        y += 34
    if abschluss:
        teile.append(f'<rect x="72" y="{hoehe-210}" width="{breite-144}" height="86" '
                     f'fill="{GRUEN_SMARAGD}" stroke="{GOLD_KANTE}" rx="6"/>')
        for i, zeile in enumerate(_umbruch(abschluss, 66)[:2]):
            teile.append(f'<text x="98" y="{hoehe-176+i*26}" fill="{HELL}" font-size="21">'
                         f'{escape(zeile)}</text>')
    teile.append(_fuss(breite, hoehe))
    return "".join(teile)
