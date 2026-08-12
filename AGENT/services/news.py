from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests


class CryptoNewsService:
    """Nachrichten über newsdata.io – Crypto plus Tinos weitere Themenbereiche.

    Tinos Schlüssel gehört zu newsdata.io (36 Zeichen, UUID-Format) – nicht zu
    newsapi.org, wogegen der alte Code lief und dauerhaft 401 erntete. Freie
    Stufe: maximal 10 Artikel je Anfrage, 200 Credits am Tag. Jeder Bereich
    kostet eine Anfrage; der Abruf hängt deshalb am Briefing-Knopf, nicht am
    10-Minuten-Ticker (der läuft über Google-News-RSS).
    """

    # Weltlage bekommt das meiste Gewicht (Ansage Tino); Kurs- und
    # Boersenmeldungen fliegen komplett raus - Kurse stehen im Ticker.
    AREAS = {
        "Weltlage": ("Ukraine OR Nahost OR Gaza OR Taiwan OR NATO", 8),
        "Crypto": ("bitcoin OR ethereum OR crypto", 4),
        "Tech": ("Tesla OR SpaceX OR Nvidia OR AMD OR OpenAI", 4),
    }
    BLOCKED_SOURCES = ("wallstreet", "finanzen", "boerse", "börse", "aktien", "4investors",
                       "finanznachrichten", "finanztrends", "sharedeals", "stock3", "investing")
    BLOCKED_TITLES = re.compile(
        r"kurs|aktie|prozent|rally|anleger|wochenbilanz|performance|dividende|börse|boerse"
        r"|am (mittag|vormittag|abend|morgen)|so viel (verlust|gewinn)|so entwickeln sich", re.IGNORECASE)

    def fetch(self, language: str = "de", hours: int = 48, limit: int = 20) -> dict:
        key = os.getenv("NEWS_API_KEY", "").strip().strip('"')
        if not key:
            raise RuntimeError("NEWS_API_KEY fehlt in AGENT/.env.")
        articles, errors = [], []
        for area, (query, take) in self.AREAS.items():
            try:
                found = self._search(key, query, language, 10, area)
                articles += [a for a in found if self._relevant(a)][:take]
            except Exception as exc:
                errors.append(f"{area}: {exc}")
        if not articles and errors:
            raise RuntimeError("newsdata.io: " + " | ".join(errors)[:300])
        return {"articles": articles, "count": len(articles), "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "newsdata.io", "status": "live", "errors": errors}

    @classmethod
    def _relevant(cls, article: dict) -> bool:
        source = (article.get("source") or "").lower()
        if any(b in source for b in cls.BLOCKED_SOURCES):
            return False
        text = f"{article.get('title') or ''} {article.get('description') or ''}"
        return not cls.BLOCKED_TITLES.search(text)

    @staticmethod
    def _search(key: str, query: str, language: str, limit: int, area: str) -> list[dict]:
        response = requests.get("https://newsdata.io/api/1/latest", params={
            "apikey": key, "q": query, "language": language, "size": limit,
        }, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(str(data.get("results", {}))[:150])
        return [{"topic": area, "title": (a.get("title") or "").strip("* "), "description": a.get("description"),
                 "url": a.get("link"), "source": a.get("source_name") or a.get("source_id"),
                 "published_at": a.get("pubDate"), "author": ", ".join(a.get("creator") or []) or None}
                for a in data.get("results", [])]

    @staticmethod
    def journalist_prompt(news: dict) -> str:
        lines = [f"- Titel: {a['title']}\n  Beschreibung: {a['description']}\n  Quelle: {a['source']}\n  Zeit: {a['published_at']}\n  URL: {a['url']}"
                 for a in news["articles"]]
        return ("Erstelle einen sachlichen deutschen Crypto-News-Brief. Trenne bestätigte Fakten, Einordnung und offene Fragen. "
                "Nenne bei jeder Meldung Quelle und Link. Behaupte nichts, was nicht in Titel oder Beschreibung steht, und "
                "erfinde keine Kursursachen. Meldungen:\n" + "\n".join(lines))
