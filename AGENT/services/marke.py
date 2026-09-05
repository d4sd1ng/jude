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
# Exakte Werte aus project_files/nurovelle-tokens.css (kanonische Quelle,
# gespiegelt aus styleguide.md) – die vorherigen Werte hier waren am
# 14.08. von der Live-Seite abgelesen und wichen an mehreren Stellen ab.
SCHWARZ = "#050706"
SCHWARZ_MATT = "#080b09"
GRUEN_TIEF = "#0a1913"
GRUEN_SMARAGD = "#112b21"
GRUEN_FOREST = "#163a26"  # entspricht --color-emerald-bright
GRUEN_AKZENT = "#163a26"  # kein eigener heller Akzentgruen-Ton im Styleguide – bislang erfunden, hier auf Emerald Bright vereinheitlicht
GOLD_KANTE = "#a55d07"
GOLD_GLANZ = "#fae385"
GOLD_MITTE = "#b47e11"
# Bestätigt von Tino (03.09.): so ist es live auf der Homepage umgesetzt,
# unabhängig davon, wie die Semantic-Mapping-Kommentare in nurovelle-tokens.css
# es beschreiben – Rahmen bekommen den 2-Farben-Verlauf, das goldene Wort in
# Überschriften den 4-Farben-Verlauf.
GOLD_RAHMEN_VERLAUF = ("#dfbd69", "#926f34")           # Rahmen/Karten-Frames (2 Farben)
GOLD_WORT_VERLAUF = ("#ae8625", "#f7ef8a", "#d2ac47", "#edc967")  # das eine goldene Wort (4 Farben)
GOLD_DOKUMENT_VERLAUF = ("#c5a059", "#fdf0cd", "#d4af37")  # Rückfall für Medien ohne CSS-Verlauf (E-Mail-Volltext)
HELL = "#f5f7f4"
HELL_GEDECKT = "#b9c3bd"

#: Schriften. Exo 2 für Überschriften, Inter für Fließtext – wie auf der Seite.
SCHRIFT_TITEL = "'Exo 2', 'Trebuchet MS', sans-serif"
SCHRIFT_TEXT = "Inter, 'DejaVu Sans', sans-serif"

CLAIM = "Building intelligent systems"

# An JEDEN generate_image-Aufruf angehaengt, nicht nur dem Mitarbeiter als
# Anweisung mitgegeben: ein Prompt ohne diese Zeilen erzeugte zuverlaessig
# beliebige Bilder, weil die Marke nur in der Rolle stand, nicht im
# tatsaechlichen API-Aufruf (gemessen 03.09.2026, "branding isnt really
# considered on generating anything"). Aus styleguide.md Abschnitt 17.
BILD_STIL_SUFFIX = (
    f" Bildstil: mattes Schwarz ({SCHWARZ}) und dunkles glaenzendes Smaragdgruen "
    f"({GRUEN_SMARAGD}), Gunmetal-Flaechen, Gold nur sparsam als einzelner Akzent "
    f"(Kante, Lichtsaum oder Detail, {GOLD_MITTE}) - kein zweiter Akzent, kein "
    "Farbverlauf quer durchs Bild. Raeumliche Form, technische Glas-/Metallwirkung, "
    "ruhiges gerichtetes Licht - wirkt wie eine teure Manufaktur, nicht wie eine "
    "Werbeanzeige. Kein Text, keine Labels, keine Logos im Bild, sofern nicht "
    "ausdruecklich verlangt. Ausgeschlossen: Cyan, Blau als Hauptfarbe, Violett, "
    "Neon, Regenbogenverlaeufe, generische KI-Roboter, Stockfoto-Buerowelt, "
    "Sci-Fi-Konsolen, Gaming- oder Casino-Anmutung."
)

BRAND_BRIEF = f"""NUROVELLE-BRAND-REGELN – VERBINDLICH FÜR JEDES KUNDEN-/ÖFFENTLICHKEITSGERICHTETE ERZEUGNIS
Gilt für: Landingpages, Social-Posts, Banner, Newsletter, Dokumente/Grafiken, die Tino oder ein Kunde zu sehen bekommt.
Gilt NICHT für: interne Status-/Tagesreports an Tino (z.B. "heute nichts zu tun", Lead-Zahlen, Fehlermeldungen). Ein
interner Report bekommt KEINE Tagline, KEINEN CTA, KEIN Gold-Wort und KEINE Marken-Textbausteine – er berichtet nur,
was passiert ist. Diese Regeln auf einen internen Report zu pressen (gemessen 03.09.2026 bei outreach: ein "heute
nichts zu tun"-Report bekam Tagline und CTA angehängt) ist selbst ein Fehler, kein Pflichtprogramm.
Marke: Nurovelle.
Wortmarke: Nurovelle.
Tagline: {CLAIM}.
Kurz-Claim: Klarheit. Prozesse. Zukunft.
Schriften: {SCHRIFT_TITEL} für Überschriften und {SCHRIFT_TEXT} für Fließtext.
Farben: matter schwarzer/graphitfarbener Grund ({SCHWARZ}, {SCHWARZ_MATT}), tiefes Grün ({GRUEN_TIEF}, {GRUEN_SMARAGD}, {GRUEN_FOREST}), Emerald-Akzent ({GRUEN_AKZENT}) und Gold nur sparsam ({GOLD_KANTE}, {GOLD_MITTE}, {GOLD_GLANZ}).
Verläufe: den vorhandenen Goldverlauf für Wortmarke, feine Linien und einzelne Hervorhebungen; den vorhandenen Grundverlauf von tiefem Grün zu mattem Schwarz für Flächen. Keine neuen Verläufe erfinden.
Karten: matte dunkle Flächen, klare Kanten, ruhige hochwertige Abstände. Jede Karte ist entweder eine Gold-Karte oder eine Grün(Smaragd)-Karte – fester Kontrast, nie beliebig kombiniert: eine Gold-Karte bekommt IMMER einen Smaragd-Rahmen, eine Grün-Karte bekommt IMMER einen Gold-Verlaufsrahmen. Diese Zuordnung ist systemweit fix, nicht pro Erzeugnis neu entscheiden.
Goldwort-Regel (aus dem Styleguide, medienabhängig!): Auf der Website trägt die ganze Überschrift den Goldverlauf. Überall sonst (Dokumente, Angebote, Social-Beiträge, Newsletter) trägt GENAU EIN Wort pro Überschrift/Unterzeile den Goldverlauf – das inhaltlich tragende, nie zwei, nie die ganze Zeile; findet sich kein tragendes Wort, wird die Überschrift gekürzt statt ein beliebiges Wort einzufärben. Das Wort "Nurovelle" wird IMMER in Gold gesetzt, unabhängig vom Medium.
Drei Gold-Verläufe, nicht austauschbar (so live auf der Homepage umgesetzt): Rahmen/Karten-Frames bekommen den 2-Farben-Verlauf ({GOLD_RAHMEN_VERLAUF[0]} → {GOLD_RAHMEN_VERLAUF[1]}). Das eine goldene Wort in Überschriften bekommt den 4-Farben-Verlauf ({', '.join(GOLD_WORT_VERLAUF)}). Nur wenn kein CSS-Verlauf möglich ist (z. B. reiner E-Mail-Text ohne background-clip-Unterstützung): Vollton {GOLD_MITTE} oder ersatzweise der 3-Farben-Dokumentverlauf ({', '.join(GOLD_DOKUMENT_VERLAUF)}).
Verbindliche Quelle: project_files/styleguide.md (mit read_project_file lesen) – bei Widerspruch zu diesem Kurzbrief gilt der Styleguide.
Bevorzugter Vorlagenordner: austausch/an-team/vorlagen/nurovelle/.
Logo: austausch/an-team/vorlagen/nurovelle/nurovelle_logo_gold.png.
Wortmarke: austausch/an-team/vorlagen/nurovelle/Nurovelle_schrift_gold.png.
Freigegebene Referenzen: launchpost.png, Potentialanalyse_post.png, karussell_5_wege.zip und Karrussell_7_Prozesse,.zip in diesem Ordner. ZIP-Dateien vor Verwendung vollständig prüfen und nicht als einzelne ungeprüfte Bilder interpretieren.
Weitere Logos, Icons und Karten: ausschließlich freigegebene Dateien aus diesem Vorlagenordner und data/marke/assets/ verwenden. Keine Ersatzlogos, generischen KI-Symbole oder erfundenen Icons.
CTA: Haupt-CTA ist ausschließlich "Kostenlose KI-Potenzialanalyse" mit Link nurovelle.de/analyse.html – wortgleich verwenden, nicht umformulieren. Sekundär-CTAs (z. B. KI-Quick-Start) nur wortgleich aus austausch/an-team/09_monetarisierung.md übernehmen, sonst weglassen. Keine neuen Angebote, Rabatte, Buttons oder CTA-Texte erfinden.
CTA-Umsetzung ist NICHT frei gestaltbar: die Pfeil-Wechsel/Gold-Fill-Interaktion aus austausch/an-team/vorlagen/nurovelle/cta-vorlage.html ist wortgleich das komplette Markup und CSS, das jeder Nurovelle-CTA verwendet. Bei einem HTML/CSS-Erzeugnis mit CTA IMMER zuerst diese Datei mit read_project_file lesen und Markup+CSS 1:1 übernehmen (nur href/Text anpassen) – ein aus der Beschreibung nachgebauter CTA sieht zuverlässig anders aus als das Original.
Dokumente und Grafiken: Wortmarke Nurovelle, die Tagline und die freigegebene CTA sichtbar nach freigegebener Vorlage einsetzen; Farben, Verläufe, Karten, Icons und CTA vor der Abgabe prüfen.
PREMIUM-MASSSTAB, bisher regelmäßig verfehlt: reiner Fließtext ohne Bilder, Icons, Karten oder Formen ist NICHT premium und wird nicht abgenommen. Jedes Dokument/jede Grafik/Landingpage braucht sichtbare visuelle Elemente – Bilder aus dem Vorlagenordner, Icons, Karten mit Rahmen, das Cube-/Glas-Bildstil aus §17 des Styleguides. Ein Text mit Überschrift und Absätzen, aber ohne echte Grafik-/Bildkomponente, gilt als unfertig, unabhängig davon, wie gut der Text selbst ist.
Nie verwenden: Blau als Markenfarbe, alte Marken wie Autonova oder Politara, beliebige Stock-/Roboteroptik oder nicht belegte Logos."""

#: Gemeinsame Grundlage: Verlauf, Goldkante, Schriftimport.
_DEFS = f"""
  <defs>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{GOLD_RAHMEN_VERLAUF[0]}"/>
      <stop offset="100%" stop-color="{GOLD_RAHMEN_VERLAUF[1]}"/>
    </linearGradient>
    <linearGradient id="goldwort" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{GOLD_WORT_VERLAUF[0]}"/><stop offset="35%" stop-color="{GOLD_WORT_VERLAUF[1]}"/>
      <stop offset="65%" stop-color="{GOLD_WORT_VERLAUF[2]}"/><stop offset="100%" stop-color="{GOLD_WORT_VERLAUF[3]}"/>
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
    teile.append(f'<text x="56" y="130" fill="url(#goldwort)" font-size="20" '
                 f'font-weight="600" letter-spacing="3.4">{escape(kicker.upper())}</text>')
    y = 210
    for zeile in _umbruch(titel, 26)[:3]:
        teile.append(f'<text x="56" y="{y}" fill="{HELL}" font-family="{SCHRIFT_TITEL}" '
                     f'font-size="62" font-weight="700">{escape(zeile)}</text>')
        y += 74
    y += 40
    for eintrag in kennzahlen[:4]:
        teile.append(f'<rect x="56" y="{y-4}" width="4" height="86" fill="url(#gold)"/>')
        teile.append(f'<text x="86" y="{y+48}" fill="url(#goldwort)" '
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
    teile.append(f'<text x="72" y="128" fill="url(#goldwort)" font-size="19" '
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
        teile.append(f'<text x="72" y="{y}" fill="url(#goldwort)" font-family="{SCHRIFT_TITEL}" '
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
