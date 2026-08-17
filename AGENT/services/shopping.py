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
    """Produktvergleich je Marke (Tino: G-Star und Nike getrennt abfragen).

    Sonderregeln aus der Abnahme: Schuhe heißt ausschließlich Nike Air Max in
    44; Jeans gibt es nur bei G-Star in W33 L34 (Nike führt keine Bundweiten);
    für Oberteile und Jogginghosen ist die Größe L/XL/XXL wählbar.
    """

    CATEGORIES = {"t-shirt", "t-shirts", "sweater", "jeans", "jogginghose", "schuhe"}
    SIZES = {"L", "XL", "XXL"}
    NIKE_CATEGORIES = {
        "t-shirt": "https://www.nike.com/de/w/herren-tops-t-shirts-43h4uz93bsdz9om13znik1",
        "t-shirts": "https://www.nike.com/de/w/herren-tops-t-shirts-43h4uz93bsdz9om13znik1",
        "sweater": "https://www.nike.com/de/w/herren-hoodies-sweatshirts-6riveznik1",
        # Nike führt Jogger unter Hosen & Tights; der Größenfilter grenzt ein.
        "jogginghose": "https://www.nike.com/de/w/herren-hosen-tights-2kq19znik1",
        # Schuhe: bewaehrte Herrenliste mit Groessenfilter; Air-Max-Filter im Titel,
        # weil die Air-Max-Kategorieseite den Groessenfilter anders aufbaut.
        "schuhe": "https://www.nike.com/de/w/herren-schuhe-nik1zy7ok",
    }
    GSTAR_SEGMENTS = {"t-shirt": "t-shirts", "t-shirts": "t-shirts", "sweater": "sweatshirts",
                      "jeans": "jeans", "jogginghose": "hosen", "schuhe": "schuhe"}
    HEADERS = {"User-Agent": "Jude/1.0 personal shopping comparison"}

    def compare(self, category: str, brand: str = "gstar", size: str = "XXL", limit: int = 12) -> dict:
        normalized = category.lower().strip()
        if normalized not in self.CATEGORIES:
            raise ValueError("Erlaubt sind T-Shirts, Sweater, Jeans, Jogginghose und Schuhe.")
        brand = brand.lower().strip()
        if brand not in {"nike", "gstar"}:
            raise ValueError("Marke muss nike oder gstar sein.")
        if normalized == "schuhe":
            brand, size = "nike", "44"          # nur Air Max in 44
        elif normalized == "jeans":
            brand, size = "gstar", "W33 L34"    # Bundweite nur bei G-Star
        else:
            size = size.upper().strip()
            if size not in self.SIZES:
                raise ValueError("Größe muss L, XL oder XXL sein.")
        total_limit = max(2, min(limit, 30))
        provider_errors = {}
        try:
            if brand == "nike":
                products = self._nike_products(normalized, size, total_limit)
            else:
                products = self._gstar_products(normalized, size, total_limit)
        except Exception as exc:
            products = []
            provider_errors["Nike" if brand == "nike" else "G-Star"] = str(exc)
        products.sort(key=lambda item: (item["price_eur"] is None, item["price_eur"] or 0))
        return {
            "category": category, "brand": "Nike" if brand == "nike" else "G-Star", "size": size,
            "products": products, "count": len(products), "provider_errors": provider_errors,
            "source": "Offizielle Nike-Produktliste bzw. offizieller G-Star-Produktsitemap/JSON-LD",
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
            title = link.get_text(" ", strip=True)
            if category == "schuhe" and "air max" not in title.lower():
                continue
            image = card.select_one("img")
            products.append({
                "brand": "Nike", "title": title, "url": str(link.get("href")),
                "image": str(image.get("src")) if image is not None and image.get("src") else None,
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
            image = data.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if isinstance(image, str):
                image = image.replace("h_1024", "h_180")
            return {
                "brand": "G-Star", "title": str(data.get("name", "")), "url": url,
                "image": image,
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
