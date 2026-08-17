from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from defusedxml import ElementTree

USER_AGENT = "JudeFactChecker/1.0 (+personal research; respects robots.txt)"


class ScraperService:
    MAX_BYTES = 4_000_000

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Nur öffentliche HTTP(S)-URLs sind erlaubt.")
        for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
            address = ipaddress.ip_address(item[4][0])
            if not address.is_global:
                raise PermissionError("Private, lokale und reservierte Netzwerkziele sind gesperrt.")
        return url

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        current = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        for _ in range(4):
            self._validate_url(current)
            try:
                response = requests.get(current, headers={"User-Agent": USER_AGENT}, timeout=(5, 8),
                                        allow_redirects=False)
            except requests.RequestException:
                return True
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("location")
                if not location:
                    return True
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                return True
            parser.set_url(current)
            parser.parse(response.text.splitlines())
            return parser.can_fetch(USER_AGENT, url)
        return True

    def _get(self, url: str, *, stream: bool = False) -> requests.Response:
        current = url
        for _ in range(6):
            self._validate_url(current)
            response = requests.get(current, headers={"User-Agent": USER_AGENT}, timeout=20,
                                    stream=stream, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Leere HTTP-Weiterleitung.")
                current = urljoin(current, location)
                response.close()
                continue
            return response
        raise RuntimeError("Zu viele HTTP-Weiterleitungen.")

    def _youtube(self, url: str) -> dict:
        from yt_dlp import YoutubeDL
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "cachedir": False,
                   "extract_flat": False, "socket_timeout": 20}
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        tracks = info.get("subtitles") or info.get("automatic_captions") or {}
        selected = tracks.get("de") or tracks.get("en") or next(iter(tracks.values()), [])
        track = next((item for item in selected if item.get("ext") in {"vtt", "json3"}), None)
        transcript = ""
        if track and track.get("url"):
            response = self._get(track["url"])
            response.raise_for_status()
            raw = response.text
            transcript = re.sub(r"<[^>]+>|\d\d:\d\d:\d\d[.,]\d+\s+-->.*|WEBVTT|Kind:.*|Language:.*", "", raw)
            transcript = "\n".join(dict.fromkeys(line.strip() for line in transcript.splitlines() if line.strip() and not line.strip().isdigit()))
        return {"url": info.get("webpage_url") or url, "title": info.get("title", ""),
                "description": info.get("description", ""), "author": info.get("uploader", ""),
                "published_at": info.get("upload_date", ""), "text": transcript[:120_000],
                "content_type": "video/youtube", "bytes": len(transcript.encode()),
                "transcript_status": "available" if transcript else "unavailable"}

    def extract(self, url: str) -> dict:
        url = self._validate_url(url)
        hostname = (urlparse(url).hostname or "").lower()
        if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            return self._youtube(url)
        if not self._robots_allowed(url):
            raise PermissionError("robots.txt untersagt den Abruf.")
        response = self._get(url, stream=True)
        response.raise_for_status()
        final_url = self._validate_url(response.url)
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise ValueError("Der Scraper verarbeitet aktuell öffentliche HTML-Seiten.")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > self.MAX_BYTES:
                raise ValueError("Seite überschreitet das Größenlimit von 4 MB.")
            chunks.append(chunk)
        soup = BeautifulSoup(b"".join(chunks), "html.parser")
        for node in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
            node.decompose()
        article = soup.find("article") or soup.find("main") or soup.body or soup
        text = "\n".join(line.strip() for line in article.get_text("\n").splitlines() if line.strip())
        def meta(*names: str) -> str:
            for name in names:
                tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
                if tag and tag.get("content"):
                    return str(tag["content"]).strip()
            return ""
        title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
        published = meta("article:published_time", "date", "datePublished")
        return {"url": final_url, "title": title, "description": meta("og:description", "description"),
                "author": meta("author", "article:author"), "published_at": published,
                "text": text[:120_000], "content_type": content_type.split(";")[0], "bytes": size}

    def search(self, query: str, limit: int = 8) -> list[dict]:
        response = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers={"User-Agent": USER_AGENT}, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for item in soup.select(".result")[:max(1, min(limit, 15))]:
            link, snippet = item.select_one(".result__a"), item.select_one(".result__snippet")
            if link and link.get("href"):
                target = urljoin("https://html.duckduckgo.com", link["href"])
                encoded = parse_qs(urlparse(target).query).get("uddg")
                if encoded:
                    target = unquote(encoded[0])
                results.append({"title": link.get_text(" ", strip=True), "url": target,
                                "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
        if len(results) < min(3, limit):
            results.extend(self._bing_news_search(query, limit - len(results)))
        unique = []
        seen = set()
        for item in results:
            if item["url"] not in seen:
                unique.append(item)
                seen.add(item["url"])
        return unique[:max(1, min(limit, 15))]

    @staticmethod
    def _bing_news_search(query: str, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        response = requests.get(
            "https://www.bing.com/news/search",
            params={"q": query, "format": "rss", "mkt": "en-US", "setlang": "en-US"},
            # Englisch liefert für deutsch- und englischsprachige Fakten deutlich vollständigere Quellensätze.
            headers={"User-Agent": USER_AGENT}, timeout=20,
        )
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Suchfeed überschreitet das Größenlimit.")
        root = ElementTree.fromstring(response.content)
        items = []
        for node in root.findall(".//item")[:max(1, min(limit, 15))]:
            title, target, snippet = node.findtext("title", ""), node.findtext("link", ""), node.findtext("description", "")
            encoded = parse_qs(urlparse(target).query).get("url")
            if encoded:
                target = unquote(encoded[0])
            if target:
                items.append({"title": title, "url": target, "snippet": BeautifulSoup(snippet, "html.parser").get_text(" ", strip=True)})
        return items
