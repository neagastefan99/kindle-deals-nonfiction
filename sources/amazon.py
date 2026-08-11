"""Amazon Kindle deals scraper — transport-agnostic.

Parsing logic lives here. The HTTP transport is injected via `fetcher`:
- CurlCffiFetcher (default, from base.py) — battle-tested
- LightpandaFetcher (lightpanda_fetcher.py) — batch browser fetch

Both expose fetch_all(urls) -> dict[url, BeautifulSoup|None].
"""

import json
import re
from typing import Any
from urllib.parse import urljoin

from sources.base import BaseScraper


class CurlCffiFetcher(BaseScraper):
    """curl_cffi transport, compatible with fetch_all interface."""

    def fetch_all(self, urls: list[str]) -> dict[str, Any]:
        out = {}
        for u in urls:
            out[u] = self.fetch_html(u)
        return out


class AmazonDealsScraper:
    """Scrapes Amazon Kindle deal pages for SFF books."""

    API_PATTERNS: list[str] = []

    def __init__(self, config: dict[str, Any], fetcher: Any | None = None):
        self.config = config
        amz = config["sources"]["amazon"]
        self.base_url = amz["base_url"]
        # Any config key containing 'deals' becomes a deal URL to scrape
        self.deal_urls = [
            urljoin(self.base_url, path)
            for key, path in amz.items()
            if key != "base_url" and "deals" in key
        ]
        self.max_books = config["scraping"].get("max_books_per_source", 50)
        self.fetcher = fetcher if fetcher is not None else CurlCffiFetcher(config)

    # ─── Transport passthroughs ────────────────────────────────────

    def fetch_html(self, url: str):
        return self.fetcher.fetch_all([url]).get(url)

    def fetch_json(self, url: str) -> dict | list | None:
        soup = self.fetch_html(url)
        if not soup:
            return None
        for script in soup.select('script[type="application/json"]'):
            try:
                return json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def prefetch(self, urls: list[str]) -> dict[str, Any]:
        """Batch-fetch URLs and return the soup map (cache-aware fetchers reuse)."""
        return self.fetcher.fetch_all(urls)

    @staticmethod
    def _extract_asin(url: str) -> str | None:
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        return m.group(1) if m else None

    @staticmethod
    def _clean_price(raw: str) -> float | None:
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            return round(float(cleaned), 2)
        except ValueError:
            return None

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(text.split()).strip()

    @staticmethod
    def extract_cover_url(soup) -> str | None:
        import re as _re

        for selector in [
            '#main-image-container img.a-dynamic-image',
            '#landingImage',
            '#imgBlkFront',
            '#ebooksImgBlkFront',
            'img.a-dynamic-image',
        ]:
            el = soup.select_one(selector)
            if el and el.get('src'):
                url = el['src']
                if 'm.media-amazon.com/images/I/' in url:
                    url = _re.sub(r'\.[A-Z0-9,%_]+_\.jpg$', '.jpg', url)
                    return url

        for img in soup.select('img[src*="m.media-amazon.com/images/I/"]'):
            src = img['src']
            if src.endswith('.jpg') and 'sticker' not in src and 'logo' not in src:
                return src
        return None

    # ─── HTML DOM parsing ──────────────────────────────────────────

    def parse_deals_page(self, soup) -> list[dict[str, Any]]:
        """Parse a deal listing page soup into book dicts."""
        if not soup:
            return []

        books = []
        results_container = soup.select_one('.s-search-results')

        from bs4 import Tag

        if results_container:
            cards = [
                c for c in results_container.children
                if isinstance(c, Tag) and c.name == 'div' and c.get('data-asin')
            ]
        else:
            cards = [
                c for c in soup.select('div[data-asin]')
                if c.get('data-asin') and c.select_one('h2, .a-size-medium, a[href*="/dp/"]')
            ]

        for card in cards[:self.max_books]:
            asin = card.get("data-asin", "")
            if not asin:
                continue

            title_el = (
                card.select_one('.a-size-medium.a-color-base') or
                card.select_one('a.a-link-normal .a-text-normal') or
                card.select_one('h2 a span') or
                card.select_one('h2 a')
            )
            title = self._clean_text(title_el.text) if title_el else ""
            if not title:
                continue

            author = ""
            raw_text = card.get_text(" ", strip=True)
            author_match = re.search(r"by\s+([^|]+?)(?:\s*\|\s*Sold by)", raw_text)
            if author_match:
                author = author_match.group(1).strip()

            price = None
            buy_match = re.search(r"Or\s+\$?(\d+\.?\d*)\s+to\s+buy", raw_text, re.IGNORECASE)
            if buy_match:
                price = self._clean_price(buy_match.group(1))

            if price is None:
                price_el = (
                    card.select_one('span.a-price span.a-offscreen') or
                    card.select_one('span.a-offscreen') or
                    card.select_one('span.a-price-whole')
                )
                if price_el:
                    price_text = str(price_el.get("aria-label") or price_el.text)
                    price = self._clean_price(price_text)

            link_el = card.select_one('a.a-link-normal[href*="/dp/"]') or card.select_one('h2 a')
            url = ""
            if link_el and link_el.get("href"):
                url = urljoin(self.base_url, str(link_el["href"]))
                url = re.sub(r"/ref=.*", "", url)

            if not url:
                url = f"{self.base_url}/dp/{asin}"

            books.append({
                "asin": asin,
                "title": title,
                "author": author,
                "price": price,
                "url": url,
                "from_sff_page": True,
            })

        return books

    def parse_best_sellers(self, soup) -> list[dict[str, Any]]:
        """Parse a Best Sellers page soup into book dicts."""
        if not soup:
            return []

        books = []
        items = soup.select('div.zg-grid-general-faceout')[:self.max_books]

        for item in items:
            title_el = item.select_one('div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y')
            title = self._clean_text(title_el.text) if title_el else ""
            if not title:
                continue

            author_el = item.select_one('div.a-row.a-size-small') or item.select_one('span.a-size-small.a-color-secondary')
            author = self._clean_text(author_el.text) if author_el else ""

            price_el = (
                item.select_one('span[class*="_p13n-sc-price"]') or
                item.select_one('span.a-color-price') or
                item.select_one('span.a-price-whole') or
                item.select_one('span.a-price .a-offscreen')
            )
            price = self._clean_price(price_el.text) if price_el else None

            link_el = item.select_one('a[href*="/dp/"]')
            url = ""
            if link_el:
                url = urljoin(self.base_url, link_el["href"])
                url = re.sub(r"/ref=.*", "", url)

            asin = self._extract_asin(url) or ""

            books.append({
                "asin": asin,
                "title": title,
                "author": author,
                "price": price,
                "url": url,
                "from_sff_page": True,
            })

        return books

    def parse_product_page(self, soup) -> dict[str, Any]:
        """Extract accurate price + list price + savings + cover from a product page."""
        if not soup:
            return {}

        info: dict[str, Any] = {}

        # Apex price-to-pay: text like "$ 1 . 99" (Lightpanda leaves .a-offscreen empty)
        apex = soup.select_one('.apex-pricetopay-value')
        if apex:
            price = self._clean_price(apex.get_text(" ", strip=True))
            if price:
                info["price"] = price

        # List price (basis)
        basis = soup.select_one('.apex-basisprice-value')
        if basis:
            list_price = self._clean_price(basis.get_text(" ", strip=True))
            if list_price:
                info["list_price"] = list_price

        savings_el = soup.select_one('.apex-savings-percentage')
        if savings_el:
            pct = re.search(r'(\d+)%', savings_el.get_text())
            if pct:
                info["savings_pct"] = int(pct.group(1))

        cover = self.extract_cover_url(soup)
        if cover:
            info["cover_url"] = cover

        return info

    # ─── Orchestrator ───────────────────────────────────────────────

    def scrape_all(self) -> list[dict[str, Any]]:
        """Fetch deal pages in ONE batch, parse, dedupe by ASIN."""
        print("  🌐 Fetching deal pages (batch)...")
        soups = self.prefetch(self.deal_urls)

        seen_asins: set[str] = set()
        all_books: list[dict[str, Any]] = []

        for url in self.deal_urls:
            soup = soups.get(url)
            books = self.parse_deals_page(soup)
            print(f"  {url.split('amazon.com')[-1][:60]}: {len(books)} books")
            for book in books:
                if book["asin"] and book["asin"] not in seen_asins:
                    seen_asins.add(book["asin"])
                    all_books.append(book)

        return all_books

    def scrape_best_sellers(self, url: str) -> list[dict[str, Any]]:
        soup = self.fetch_html(url)
        return self.parse_best_sellers(soup)
