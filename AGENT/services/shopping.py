from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache

import requests
from bs4 import BeautifulSoup
from defusedxml import ElementTree


class ShoppingService:
    ALLOWED = {"t-shirt", "t-shirts", "sweater", "schuhe"}
    NIKE_CATEGORIES = {
        "t-shirt": "https://www.nike.com/de/w/herren-tops-t-shirts-43h4uz93bsdz9om13znik1",
        "t-shirts": "https://www.nike.com/de/w/herren-tops-t-shirts-43h4uz93bsdz9om13znik1",
        "sweater": "https://www.nike.com/de/w/herren-hoodies-sweatshirts-6riveznik1",
        "schuhe": "https://www.nike.com/de/w/herren-schuhe-nik1zy7ok",
    }
    GSTAR_SEGMENTS = {"t-shirt": "t-shirts", "t-shirts": "t-shirts", "sweater": "sweatshirts", "schuhe": "schuhe"}
    HEADERS = {"User-Agent": "Jude/1.0 personal shopping comparison"}

    def compare(self, category: str, limit: int = 12) -> dict:
        normalized = category.lower().strip()
        if normalized not in self.ALLOWED:
            raise ValueError("Erlaubt sind T-Shirts, Sweater und Schuhe.")
        size = "44" if normalized == "schuhe" else "XXL"
        total_limit = max(2, min(limit, 30))
        per_brand = max(1, (total_limit + 1) // 2)
        products, provider_errors = [], {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            nike_future = pool.submit(self._nike_products, normalized, size, per_brand)
            gstar_future = pool.submit(self._gstar_products, normalized, size, per_brand)
            for brand, future in (("Nike", nike_future), ("G-Star", gstar_future)):
                try:
                    products.extend(future.result())
                except Exception as exc:
                    provider_errors[brand] = str(exc)
        products.sort(key=lambda item: (item["price_eur"] is None, item["price_eur"] or 0, item["brand"]))
        products = products[:total_limit]
        return {
            "category": category, "size": size, "products": products, "count": len(products),
            "brands": {brand: sum(item["brand"] == brand for item in products) for brand in ("Nike", "G-Star")},
            "provider_errors": provider_errors,
            "source": "Offizielle Nike-Produktliste und offizieller G-Star-Produktsitemap/JSON-LD",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "caveat": "Verfügbarkeit und Endpreis müssen im Shop geprüft werden; Jude bestellt nicht.",
        }

    def _nike_products(self, category: str, size: str, limit: int) -> list[dict]:
        response = requests.get(self.NIKE_CATEGORIES[category], headers=self.HEADERS, timeout=(5, 20))
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        size_label = next((node for node in soup.find_all(string=lambda value: value and value.strip() == size)
                           if node.parent and node.parent.parent and node.parent.parent.get("data-url")), None)
        if size_label is None:
            raise RuntimeError(f"Nike-Größenfilter {size} wurde nicht gefunden.")
        filtered_url = str(size_label.parent.parent["data-url"])
        response = requests.get(filtered_url, headers=self.HEADERS, timeout=(5, 20))
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        products = []
        for card in soup.select('[data-testid="product-card"]'):
            link = card.select_one('[data-testid="product-card__link-overlay"]')
            prices = [node.get_text(" ", strip=True).replace("\xa0", " ") for node in card.select('[data-testid="product-price"]')]
            if not link or not prices:
                continue
            numeric = self._price_number(prices[0])
            products.append({
                "brand": "Nike", "title": link.get_text(" ", strip=True), "url": str(link.get("href")),
                "snippet": f"Offizielle, nach Größe {size} gefilterte Nike-Produktliste",
                "prices": prices, "price_eur": numeric, "size": size, "availability": "laut Größenfilter",
            })
            if len(products) >= limit:
                break
        return products

    @staticmethod
    @lru_cache(maxsize=1)
    def _gstar_sitemap_urls() -> tuple[str, ...]:
        url = "https://www.g-star.com/sitemap-output/gstarsite_de/GSProduct-gstarSite_DE_de_de-0.xml.gz"
        response = requests.get(url, headers=ShoppingService.HEADERS, timeout=(5, 30))
        response.raise_for_status()
        if len(response.content) > 5_000_000:
            raise ValueError("G-Star-Produktsitemap überschreitet das Größenlimit.")
        content = gzip.decompress(response.content)
        if len(content) > 50_000_000:
            raise ValueError("Entpackte G-Star-Produktsitemap überschreitet das Größenlimit.")
        root = ElementTree.fromstring(content)
        urls = []
        for entry in root:
            location = next((node.text for node in entry if node.tag.endswith("loc")), None)
            if location and location.startswith("https://www.g-star.com/de_de/shop/herren/"):
                urls.append(location)
        return tuple(urls)

    def _gstar_products(self, category: str, size: str, limit: int) -> list[dict]:
        segment = self.GSTAR_SEGMENTS[category]
        candidates = [url for url in self._gstar_sitemap_urls() if f"/herren/{segment}/" in url][:max(limit * 4, 12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            parsed = list(pool.map(lambda url: self._gstar_product(url, size), candidates))
        return [item for item in parsed if item is not None][:limit]

    def _gstar_product(self, url: str, size: str) -> dict | None:
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=(5, 20))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            product_script = next((node for node in soup.select('script[type="application/ld+json"]')
                                   if '"@type":"ProductGroup"' in node.get_text()), None)
            if product_script is None:
                return None
            data = json.loads(product_script.get_text())
            variant = next((item for item in data.get("hasVariant", [])
                            if str(item.get("size", "")).upper() == size.upper()
                            and str(item.get("offers", {}).get("availability", "")).endswith("InStock")), None)
            if variant is None:
                return None
            offer = variant.get("offers", {})
            price = float(offer["price"])
            return {
                "brand": "G-Star", "title": str(data.get("name", "")), "url": url,
                "snippet": f"{data.get('color', '')} · {data.get('material', '')}".strip(" ·"),
                "prices": [f"{price:.2f} €".replace(".", ",")], "price_eur": price,
                "size": size, "availability": "InStock",
            }
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError):
            return None

    @staticmethod
    def _price_number(value: str) -> float | None:
        cleaned = "".join(char for char in value if char.isdigit() or char in ",.").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
