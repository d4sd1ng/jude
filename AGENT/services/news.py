from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests


class CryptoNewsService:
    def fetch(self, language: str = "de", hours: int = 48, limit: int = 20) -> dict:
        key = os.getenv("NEWS_API_KEY")
        if not key:
            raise RuntimeError("NEWS_API_KEY fehlt in AGENT/.env.")
        since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 720)))
        response = requests.get("https://newsapi.org/v2/everything", headers={"X-Api-Key": key}, params={
            "q": "(bitcoin OR ethereum OR cryptocurrency OR crypto)", "language": language,
            "from": since.isoformat(), "sortBy": "publishedAt", "pageSize": max(1, min(limit, 100)),
        }, timeout=20)
        response.raise_for_status()
        data = response.json()
        articles = [{"title": a.get("title"), "description": a.get("description"), "url": a.get("url"),
                     "source": (a.get("source") or {}).get("name"), "published_at": a.get("publishedAt"),
                     "author": a.get("author")} for a in data.get("articles", [])]
        return {"articles": articles, "count": len(articles), "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "NewsAPI", "status": "live"}

    @staticmethod
    def journalist_prompt(news: dict) -> str:
        lines = [f"- Titel: {a['title']}\n  Beschreibung: {a['description']}\n  Quelle: {a['source']}\n  Zeit: {a['published_at']}\n  URL: {a['url']}"
                 for a in news["articles"]]
        return ("Erstelle einen sachlichen deutschen Crypto-News-Brief. Trenne bestätigte Fakten, Einordnung und offene Fragen. "
                "Nenne bei jeder Meldung Quelle und Link. Behaupte nichts, was nicht in Titel oder Beschreibung steht, und "
                "erfinde keine Kursursachen. Meldungen:\n" + "\n".join(lines))
