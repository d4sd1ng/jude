"""Web- und Nachrichtensuche für die Agenten.

``web_search`` benutzte bisher ausschließlich DuckDuckGos *Instant-Answer*-
Schnittstelle. Die liefert nur enzyklopädische Kurzantworten und für jede
Nachrichtenanfrage schlicht nichts — die Agenten liefen damit ins Leere und
gaben nach mehreren Versuchen auf ("Keine aktuelle Meldung gefunden"), obwohl
es reichlich Meldungen gab.

Jetzt gilt: erst die Sofortantwort (billig, gut für Definitionen), sonst die
Trefferliste aus Google News (liefert Schlagzeile, Quelle, Datum und Link).
``news_search`` ist derselbe Weg als eigenständiges Werkzeug, wenn es dem
Agenten ausdrücklich um Nachrichten geht.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from defusedxml import ElementTree

from core.tool_registry import Tool, ToolRegistry

HEADERS = {"User-Agent": "Mozilla/5.0 (kompatibel; Jude/1.0)"}


def _sofortantwort(query: str) -> str | None:
    """DuckDuckGo Instant Answer – nur für Begriffe und Definitionen brauchbar."""
    try:
        response = requests.get("https://api.duckduckgo.com/", params={
            "q": query, "format": "json", "no_html": 1, "skip_disambig": 1,
        }, timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    if data.get("AbstractText"):
        return f"{data['AbstractText']}\nQuelle: {data.get('AbstractURL', '')}"
    topics = [item for item in data.get("RelatedTopics", [])
              if isinstance(item, dict) and item.get("Text")]
    if topics:
        return "\n".join(f"- {item['Text']} ({item.get('FirstURL', '')})" for item in topics[:5])
    return None


def news_search(query: str, limit: int = 6, language: str = "de") -> str:
    """Aktuelle Meldungen zu *query* – Schlagzeile, Quelle, Datum, Link."""
    land = "DE" if language == "de" else "US"
    url = (f"https://news.google.com/rss/search?q={quote_plus(query)}"
           f"&hl={language}&gl={land}&ceid={land}:{language}")
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    treffer = []
    for item in root.iter("item"):
        titel = (item.findtext("title") or "").strip()
        if not titel:
            continue
        # Google hängt die Quelle als " - Quelle" an den Titel.
        schlagzeile, _, quelle = titel.rpartition(" - ")
        datum = item.findtext("pubDate") or ""
        try:
            datum = parsedate_to_datetime(datum).astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError):
            pass
        # Der Link fehlte hier lange (Google-News-Redirects sind ~320 Zeichen
        # je Treffer und sprengten frueher Groqs Minutenbudget) - aber genau
        # dieser Link ist scrapers einzige Quelle fuer die von seiner eigenen
        # Rolle verlangte "oeffentliche URL" zur Verifizierung. Ohne ihn kann
        # er die Anforderung nie erfuellen, sucht immer weiter und scheitert
        # zuverlaessig an "Maximale Anzahl der Tool-Schritte ueberschritten" -
        # 55% aller Laeufe im September, gefunden 13.09.2026 bei Tinos Audit-
        # Auftrag. Ein funktionierendes Werkzeug mit langem Link ist besser
        # als eines, das die eigentliche Aufgabe strukturell unmoeglich macht;
        # der Redirect loest beim tatsaechlichen Abruf (scrape_public_url)
        # ganz normal zum echten Artikel auf.
        link = (item.findtext("link") or "").strip()
        treffer.append(f"- {schlagzeile or titel} [{quelle or 'unbekannt'}, {datum}] {link}")
        if len(treffer) >= max(1, min(int(limit), 20)):
            break
    if not treffer:
        return f"Keine Meldungen zu {query!r} gefunden."
    stand = datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"Meldungen zu {query!r} (Stand {stand}):\n" + "\n".join(treffer)


def web_search(query: str) -> str:
    """Sofortantwort, sonst aktuelle Treffer aus der Nachrichtensuche."""
    antwort = _sofortantwort(query)
    if antwort:
        return antwort
    try:
        return news_search(query)
    except requests.RequestException as exc:
        return f"Suche nicht erreichbar: {exc}"


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="web_search",
        description="Sucht im Web: liefert eine Sofortantwort, sonst aktuelle Treffer mit Quelle und Link.",
        func=web_search,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        untrusted=True,
    ))
    registry.register(Tool(
        name="news_search",
        description=("Sucht aktuelle Nachrichtenmeldungen zu einem Stichwort und liefert Schlagzeile, "
                     "Quelle, Datum und Link. Für Recherche zu Firmen, Produkten und Ereignissen."),
        func=news_search,
        param_schema={"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Anzahl Treffer, Standard 6"},
            "language": {"type": "string", "description": "'de' oder 'en', Standard 'de'"},
        }, "required": ["query"]},
        untrusted=True,
    ))
