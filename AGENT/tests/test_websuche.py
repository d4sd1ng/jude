from __future__ import annotations

from unittest.mock import Mock, patch

from tools.websuche import news_search

_RSS_BEISPIEL = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Nvidia stellt neuen Chip vor - Reuters</title>
  <link>https://news.google.com/rss/articles/lange-weiterleitung-abc123</link>
  <pubDate>Sun, 13 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


@patch("tools.websuche.requests")
def test_news_search_enthaelt_den_link(mock_requests):
    """scraper's eigene Rolle verlangt eine oeffentliche URL je Kandidat zur
    Verifizierung - fehlte der Link (wie vor diesem Fix), konnte er die
    Anforderung strukturell nie erfuellen und scheiterte zuverlaessig an
    'Maximale Anzahl der Tool-Schritte ueberschritten' (55% aller Laeufe im
    September, gefunden 13.09.2026 bei Tinos Audit-Auftrag)."""
    response = Mock()
    response.content = _RSS_BEISPIEL
    response.raise_for_status.return_value = None
    mock_requests.get.return_value = response

    ergebnis = news_search("Nvidia")

    assert "https://news.google.com/rss/articles/lange-weiterleitung-abc123" in ergebnis
    assert "Nvidia stellt neuen Chip vor" in ergebnis
