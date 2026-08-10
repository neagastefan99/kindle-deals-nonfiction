"""Amazon Kindle deals scraper for Science Fiction & Fantasy.

Uses a dual approach:
1. Back-end API scraping (internal JSON endpoints — fast, reliable)
2. HTML DOM parsing (fallback when APIs change)
"""

import json
import re
from typing import Any
from urllib.parse import urljoin

from sources.base import BaseScraper


class AmazonDealsScraper(BaseScraper):
    """Scrapes Amazon Kindle deal pages for SFF books."""
    
    # Known Amazon internal API patterns (discovered via browser DevTools).
    # Update these after Task 5b discovery session.
    API_PATTERNS: list[str] = []
    
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        amz = config["sources"]["amazon"]
        self.base_url = amz["base_url"]
        
        # Build deal URLs dynamically from all configured source paths
        self.deal_urls = []
        for key, path in amz.items():
            if key != "base_url" and "deals" in key:
                self.deal_urls.append(urljoin(self.base_url, path))
        
        self.max_books = config["scraping"].get("max_books_per_source", 50)
    
    @staticmethod
    def _extract_asin(url: str) -> str | None:
        """Extract ASIN from Amazon product URL."""
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        return m.group(1) if m else None
    
    @staticmethod
    def _clean_price(raw: str) -> float | None:
        """Parse Amazon price strings like '$2.99' or 'Kindle Price: $3.99'."""
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
        """Extract the main cover image URL from an Amazon product page.

        Verified live on 2026-08-10: the main cover is in
        #main-image-container img.a-dynamic-image (also id='landingImage').
        The src URL contains size modifiers like ._SY445_SX342_QL70_ML2_.jpg;
        stripping them yields the original full-size image.
        """
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
                    # Strip size modifiers to get full-size image
                    # e.g. .../51wnPeiPpAL._SY445_SX342_QL70_ML2_.jpg -> .../51wnPeiPpAL.jpg
                    url = _re.sub(r'\._[A-Z0-9,%_]+_\.jpg$', '.jpg', url)
                    return url

        # Fallback: any img tag pointing to Amazon image CDN (skip SVG/logos)
        for img in soup.select('img[src*="m.media-amazon.com/images/I/"]'):
            src = img['src']
            if src.endswith('.jpg') and 'sticker' not in src and 'logo' not in src:
                return src
        return None

    # ─── HTML DOM scraping (fallback) ─────────────────────────────────
    
    def scrape_deals_page(self, url: str) -> list[dict[str, Any]]:
        """Scrape a single Amazon deals listing page via HTML parsing."""
        soup = self.fetch_html(url)
        if not soup:
            return []
        
        books = []
        
        # Deal pages use .s-search-results with div[data-asin] cards.
        # Some deal pages use different containers.
        results_container = soup.select_one('.s-search-results')
        
        from bs4 import Tag
        
        if results_container:
            cards = [
                c for c in results_container.children 
                if isinstance(c, Tag) and c.name == 'div' and c.get('data-asin')
            ]
        else:
            # Fallback: find all divs with data-asin that have product content
            cards = [
                c for c in soup.select('div[data-asin]')
                if c.get('data-asin') and c.select_one('h2, .a-size-medium, a[href*=\"/dp/\"]')
            ]
        
        for card in cards[:self.max_books]:
            asin = card.get("data-asin", "")
            if not asin:
                continue
            
            # Title — deal pages use .a-size-medium.a-color-base or .a-text-normal
            title_el = (
                card.select_one('.a-size-medium.a-color-base') or
                card.select_one('a.a-link-normal .a-text-normal') or
                card.select_one('h2 a span') or
                card.select_one('h2 a')
            )
            title = self._clean_text(title_el.text) if title_el else ""
            if not title:
                continue
            
            # Author — extract "by Author Name" from card text
            author = ""
            raw_text = card.get_text(" ", strip=True)
            author_match = re.search(r"by\s+([^|]+?)(?:\s*\|\s*Sold by)", raw_text)
            if author_match:
                author = author_match.group(1).strip()
            
            # Price — prioritize "Or $X.XX to buy" over KU $0.00
            price = None
            
            # First, check for the non-KU purchase price: "Or $X.XX to buy"
            buy_match = re.search(r"Or\s+\$?(\d+\.?\d*)\s+to\s+buy", raw_text, re.IGNORECASE)
            if buy_match:
                price = self._clean_price(buy_match.group(1))
            
            # Fall back to .a-offscreen price if "Or buy" not found or failed
            if price is None:
                price_el = (
                    card.select_one('span.a-price span.a-offscreen') or
                    card.select_one('span.a-offscreen') or
                    card.select_one('span.a-price-whole')
                )
                if price_el:
                    price_text = price_el.get("aria-label") or price_el.text
                    price = self._clean_price(price_text)
            
            # URL
            link_el = card.select_one('a.a-link-normal[href*="/dp/"]') or card.select_one('h2 a')
            url = ""
            if link_el and link_el.get("href"):
                url = urljoin(self.base_url, link_el["href"])
                url = re.sub(r"/ref=.*", "", url)
            
            if not url:
                url = f"{self.base_url}/dp/{asin}"
            
            books.append({
                "asin": asin,
                "title": title,
                "author": author,
                "price": price,
                "url": url,
                # Non-fiction: don't skip genre filtering (different bot, different rules)
            })
        
        return books
    
    def scrape_best_sellers(self, url: str) -> list[dict[str, Any]]:
        """Scrape the SFF Best Sellers page — tries API first, falls back to HTML."""
        # Try API first
        books = self.scrape_api(url)
        if books:
            return books
        
        # Fall back to HTML DOM parsing
        soup = self.fetch_html(url)
        if not soup:
            return []
        
        books = []
        items = soup.select('div.zg-grid-general-faceout')[:self.max_books]
        
        for item in items:
            # Title
            title_el = item.select_one('div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y')
            title = self._clean_text(title_el.text) if title_el else ""
            if not title:
                continue
            
            # Author
            author_el = item.select_one('div.a-row.a-size-small') or item.select_one('span.a-size-small.a-color-secondary')
            author = self._clean_text(author_el.text) if author_el else ""
            
            # Price — Amazon's CSS class suffixes are dynamic (hash-based),
            # so we use a prefix match
            price_el = (
                item.select_one('span[class*="_p13n-sc-price"]') or
                item.select_one('span.a-color-price') or
                item.select_one('span.a-price-whole') or
                item.select_one('span.a-price .a-offscreen')
            )
            price = self._clean_price(price_el.text) if price_el else None
            
            # URL
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
                # Non-fiction: don't skip genre filtering (different bot, different rules)
            })
        
        return books
    
    def scrape_api(self, url: str) -> list[dict[str, Any]]:
        """Try to fetch deals via Amazon's internal JSON endpoints.
        Returns empty list if API approach fails — caller falls back to HTML."""
        # Attempt 1: Try known API patterns first
        for api_url in self.API_PATTERNS:
            try:
                data = self.fetch_json(api_url)
                if data:
                    return self._parse_api_response(data)
            except Exception:
                continue
        
        # Attempt 2: Try to extract embedded JSON from the page
        # (server-side rendered data in <script> tags — common in modern SPAs)
        soup = self.fetch_html(url)
        if soup:
            for script in soup.select('script[type="application/json"], script#__NEXT_DATA__'):
                try:
                    text = script.string or ""
                    if not text.strip():
                        continue
                    data = json.loads(text)
                    books = self._parse_api_response(data)
                    if books:
                        return books
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        
        return []
    
    def _parse_api_response(self, data: dict | list) -> list[dict[str, Any]]:
        """Walk a JSON response from Amazon's internal API and extract book entries.
        Amazon's API structure varies — this method tries multiple common paths."""
        books: list[dict[str, Any]] = []
        
        def walk(obj: Any, depth: int = 0) -> None:
            if depth > 10 or len(books) >= self.max_books:
                return
            if isinstance(obj, dict):
                # Common Amazon JSON patterns: objects with asin + title
                if "asin" in obj and "title" in obj:
                    asin = str(obj.get("asin", ""))
                    title = str(obj.get("title", "")).strip()
                    if title and asin:
                        price = None
                        if "price" in obj:
                            price = self._clean_price(str(obj["price"]))
                        books.append({
                            "asin": asin,
                            "title": title,
                            "author": str(obj.get("author", obj.get("byline", ""))),
                            "price": price,
                            "url": f"{self.base_url}/dp/{asin}",
                        })
                    return
                for v in obj.values():
                    walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, depth + 1)
        
        walk(data)
        return books
    
    # ─── Orchestrator ───────────────────────────────────────────────
    
    def _prime_us_session(self) -> None:
        """Visit Amazon.com first to establish US session cookies.
        Without this, Amazon detects Romanian IP and shows RON prices."""
        print("  🌐 Priming US session on amazon.com...")
        self.fetch_html(self.base_url + "/")
    
    def scrape_all(self) -> list[dict[str, Any]]:
        """Scrape all configured sources. Tries API first, falls back to HTML.
        Deduplicates by ASIN."""
        # Prime session with US locale
        self._prime_us_session()
        
        seen_asins: set[str] = set()
        all_books: list[dict[str, Any]] = []
        
        for url in self.deal_urls:
            print(f"  Scraping: {url}")
            
            # 1. Try back-end API first (fast, reliable)
            print(f"    Trying API...")
            books = self.scrape_api(url)
            if books:
                print(f"    ✓ API returned {len(books)} books")
            else:
                # 2. Fall back to HTML DOM parsing
                print(f"    API empty, falling back to HTML...")
                books = self.scrape_deals_page(url)
                print(f"    HTML returned {len(books)} books")
            
            for book in books:
                if book["asin"] and book["asin"] not in seen_asins:
                    seen_asins.add(book["asin"])
                    all_books.append(book)
            print(f"    Total unique: {len(all_books)}")
        
        return all_books
