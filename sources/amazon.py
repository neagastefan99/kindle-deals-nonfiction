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


# Currency-agnostic price patterns (t_ccbd16c0): Amazon serves region-
# specific prices ("RON 9.02", "lei 9,02", "$1.99") depending on the
# detected geo/cookie state, so the parser must never anchor on "$". A
# price token is a currency prefix + digits, or a bare DECIMAL number —
# a bare integer is not enough ("Kindle 2nd edition" must not count as
# a price).
CURRENCY_PREFIX = r"(?:[A-Z]{3}\s*|\$\s*|€\s*|£\s*)"
PRICE_TOKEN = rf"(?:{CURRENCY_PREFIX}\d+(?:\.\d+)?|\d+\.\d+)"
OR_PRICE_TO_BUY = re.compile(
    rf"\bor\s+(?:{CURRENCY_PREFIX})?(\d+(?:\.\d+)?)\s+to\s+buy",
    re.IGNORECASE)


class CurlCffiFetcher(BaseScraper):
    """curl_cffi transport, compatible with fetch_all interface."""

    def fetch_all(self, urls: list[str]) -> dict[str, Any]:
        out = {}
        for u in urls:
            out[u] = self.fetch_html(u)
        return out


class AmazonDealsScraper:
    """Scrapes Amazon Kindle deal pages for non-fiction books."""

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
        """Extract the FIRST numeric token from raw price text.

        Old behavior stripped all non-digits then float()'d the whole string,
        which chokes on Amazon's duplicated price spans ("$12.99 $12.99" →
        "12.9912.99" → None) and silently mangles list prices. Taking the first
        numeric token handles duplication and Lightpanda's spaced "1 . 99" form.

        Currency-agnostic (t_ccbd16c0): a leading currency code/symbol
        (RON / USD / lei / $ / € / £) is stripped first so a page served in
        a non-US storefront still parses instead of returning None.
        """
        if not raw:
            return None
        compact = re.sub(r"\s+", "", str(raw))
        compact = re.sub(
            r"^(?:RON|USD|EUR|GBP|LEI|\$|€|£|&euro;|&pound;)",
            "", compact, flags=re.IGNORECASE)
        m = re.search(r"\d+(?:\.\d+)?", compact)
        if not m:
            return None
        try:
            return round(float(m.group(0)), 2)
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

    @staticmethod
    def _kindle_swatch_text(container) -> str | None:
        """Text of the KINDLE row inside a #tmmSwatches / div#formats container.

        Amazon renders each format as a `.swatchElement` row; the Kindle one
        has id `tmm-grid-swatch-KINDLE`. Fall back to scanning rows by text,
        then to slicing the container text at the next known format token.
        """
        if container is None:
            return None
        kindle = container.select_one('#tmm-grid-swatch-KINDLE')
        if kindle:
            return AmazonDealsScraper._clean_text(kindle.get_text(" ", strip=True))
        for row in container.select('.swatchElement'):
            text = AmazonDealsScraper._clean_text(row.get_text(" ", strip=True))
            if re.search(r'\bKindle\b', text, re.IGNORECASE):
                return text
        full = AmazonDealsScraper._clean_text(container.get_text(" ", strip=True))
        m = re.search(
            r'Kindle\b(.{0,200}?)(?=\s+(?:Audiobook|Audible|Hardcover|Paperback|'
            r'Mass Market|Audio CD|Board Book|MP3 CD|Library Binding|Other)\b)',
            full, re.IGNORECASE)
        if m:
            return f"Kindle {m.group(1)}".strip()
        return None

    @staticmethod
    def _kindle_row_element(container):
        """The KINDLE row element inside a #tmmSwatches / div#formats block.

        Amazon renders each format as a row; the Kindle one has id
        `tmm-grid-swatch-KINDLE` (legacy `<li>`, modern `.swatchElement`,
        or the `.a-button-inner` of the swatch grid). Falls back to scanning
        rows by text.
        """
        if container is None:
            return None
        kindle = container.select_one('#tmm-grid-swatch-KINDLE')
        if kindle:
            return kindle
        for row in container.select('.swatchElement, .a-button-inner, li'):
            text = AmazonDealsScraper._clean_text(row.get_text(" ", strip=True))
            if re.search(r'\bKindle\b', text, re.IGNORECASE):
                return row
        return None

    @staticmethod
    def _kindle_row_prices(container) -> tuple[float | None, float | None]:
        """(deal, list) price pair from the KINDLE row of #tmmSwatches.

        Returns the EBOOK edition's OWN prices (spike t_e934a2a3 §5/6a):
          - list: the struck-through price element inside the Kindle row
            (`.a-text-price` / `[data-a-strike]`) — the ebook's own list
            price when Amazon renders it beside the deal;
          - deal: the row's current price — `.ebook-price-value` / its
            aria-label, else the first `$X.XX` token NOT inside the struck
            element; on membership rows ("Kindle $0.00 or $1.99 to buy")
            the "to buy" price wins.
        When the page exposes no struck-through list (e.g. the live
        B0B2P2N58X row is just "Kindle $1.99 Available instantly"), list
        stays None and the caller falls back to apex-basisprice-value (the
        print list, imperfect but the only remaining source).
        Returns (None, None) when the row or its price is absent (e.g.
        Lightpanda leaves #tmmSwatches empty) so apex becomes the fallback.
        """
        if container is None:
            return None, None
        kindle_row = AmazonDealsScraper._kindle_row_element(container)
        if kindle_row is None:
            return None, None

        # List price: the struck-through element, wherever it sits in the row.
        list_price = None
        struck = None
        for sel in ('.a-text-price', '[data-a-strike="true"]'):
            el = kindle_row.select_one(sel)
            if el:
                p = AmazonDealsScraper._clean_price(el.get_text(" ", strip=True))
                if p:
                    list_price = p
                    struck = el
                break

        # Deal price: the "to buy" price wins on membership rows
        # ("Kindle $0.00 or $1.99 to buy" — the aria-label shows $0.00).
        # Then the dedicated price element (`.ebook-price-value`), else the
        # first price token NOT inside the struck element.
        deal = None
        text = AmazonDealsScraper._clean_text(kindle_row.get_text(" ", strip=True))
        m = OR_PRICE_TO_BUY.search(text)
        if m:
            deal = AmazonDealsScraper._clean_price(m.group(1))
        if deal is None:
            price_el = (kindle_row.select_one('.ebook-price-value')
                        or kindle_row.select_one(
                            'span[aria-label*="$"], span[aria-label*="RON"], '
                            'span[aria-label*="EUR"], span[aria-label*="lei"], '
                            'span[aria-label*="€"], span[aria-label*="£"]'))
            if price_el is not None:
                deal = AmazonDealsScraper._clean_price(
                    price_el.get('aria-label') or price_el.get_text(" ", strip=True))
        if deal is None:
            # First token NOT inside the struck element — the struck text
            # (list price) may precede the deal token in the DOM.
            if struck is not None:
                struck_text = AmazonDealsScraper._clean_text(
                    struck.get_text(" ", strip=True))
                text = text.replace(struck_text, "")
            tokens = re.findall(PRICE_TOKEN, text)
            if tokens:
                deal = AmazonDealsScraper._clean_price(tokens[0])

        # Some layouts render the list as a second, higher token with no
        # strike markup ("Kindle $1.99 $9.99") — keep that as a fallback.
        if list_price is None and deal is not None:
            text = AmazonDealsScraper._clean_text(kindle_row.get_text(" ", strip=True))
            tokens = re.findall(PRICE_TOKEN, text)
            if len(tokens) >= 2:
                second = AmazonDealsScraper._clean_price(tokens[1])
                if second is not None and second > deal:
                    list_price = second

        return deal, list_price

    @staticmethod
    def _buybox_digital_list_price(soup) -> float | None:
        """Ebook list price from the buybox basisprice, ONLY when labelled
        Digital (t_ccbd16c0, spike RC-1).

        Amazon no longer renders a struck-through list price inside the
        Kindle format swatch; the only list price in the no-JS HTML lives
        in the buybox as `apex-basisprice-value` next to a label that says
        either "Digital List Price: $X" (the EBOOK's own list — the correct
        savings basis) or "Print List Price: $X" (the paperback/hardcover
        list — must NEVER be used as a savings basis). Returns None when no
        Digital-labelled basisprice is present.
        """
        if soup is None:
            return None
        for el in soup.select('.apex-basisprice-value'):
            box = el.find_parent('div') or el
            label = box.select_one(
                '.apex-basisprice-label, .apex-basisprice-offscreen-label')
            if label and 'digital list price' in label.get_text(' ', strip=True).lower():
                p = AmazonDealsScraper._clean_price(el.get_text(' ', strip=True))
                if p:
                    return p
        return None

    @staticmethod
    def _kindle_price_basis(soup) -> float | None:
        """Ebook list price from a \"Kindle Price\" basis element (spike §6a).

        Classic buybox layouts render `Kindle Price: $1.99 / List Price:
        $9.99` in a `.kindle-price` / `[class*="kindle-price"]` block. The
        "List Price" token there is the EBOOK's own list price — unlike
        `apex-basisprice-value`, which is the PRINT list. Returns None when
        only the print basis is present.
        """
        if soup is None:
            return None
        for sel in (
            '#kindle-price-basis', '.kindle-price', '.ebooks-price-basis',
            '[class*="kindle-price"]', '[class*="ebooks-price"]',
        ):
            for el in soup.select(sel):
                text = AmazonDealsScraper._clean_text(el.get_text(" ", strip=True))
                if 'print list price' in text.lower():
                    continue
                m = re.search(
                    rf'list\s*price[^0-9]*?(?:{CURRENCY_PREFIX})?\s?(\d+(?:\.\d+)?)',
                    text, re.IGNORECASE | re.DOTALL)
                if m:
                    p = AmazonDealsScraper._clean_price(m.group(1))
                    if p:
                        return p
        return None

    @staticmethod
    def _detect_availability(soup) -> dict[str, Any]:
        """Availability of the KINDLE edition from a product page (spike §5/6b).

        Returns {"available": bool | None, "preorder": bool} where
        available=None means no positive signal was found (caller decides;
        the scraper treats unknown as keep). Signals, scoped to the Kindle row:
          'Available instantly'            → available
          'Currently unavailable'          → unavailable
          'not currently available for purchase' → unavailable
          'will be released on' / 'Pre-order'    → preorder (not buyable now)
        Fallback when no swatch block exists: 'Buy now with 1-Click' in
        #buybox → available (plus the same unavailable/preorder phrases).
        """
        out: dict[str, Any] = {"available": None, "preorder": False}

        container = soup.select_one('#tmmSwatches') or soup.select_one('div#formats')
        kindle_text = AmazonDealsScraper._kindle_swatch_text(container)

        if kindle_text:
            low = kindle_text.lower()
            if "available instantly" in low:
                out["available"] = True
            elif ("currently unavailable" in low
                  or "not currently available for purchase" in low):
                out["available"] = False
            elif "pre-order" in low or "will be released on" in low:
                out["preorder"] = True
                out["available"] = False
            elif OR_PRICE_TO_BUY.search(low):
                # Kindle-Unlimited membership row ("Kindle $0.00 or $1.99 to
                # buy") — buyable now (t_ccbd16c0, spike RC-4). The KU $0.00
                # token is not an availability signal by itself; the "or $X
                # to buy" part means you can buy the ebook right now.
                out["available"] = True
            # No status token on the Kindle row → unknown (keep)
            return out

        buybox = soup.select_one('#buybox')
        if buybox is not None:
            low = AmazonDealsScraper._clean_text(
                buybox.get_text(" ", strip=True)).lower()
            if "buy now with 1-click" in low:
                out["available"] = True
            elif "pre-order" in low or "this title will be released on" in low:
                out["preorder"] = True
                out["available"] = False
            elif "currently unavailable" in low:
                out["available"] = False
        return out

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
            # Prefer the author row DOM: div.a-row.a-color-secondary contains
            # "by <Author> | Sold by: <Publisher> ...". Regex over the FULL card
            # text grabs the FIRST "by", which can be inside the title itself
            # (e.g. "...Explained by Its Most Brilliant Teacher"), mangling the
            # author. Restricting the search to the author row avoids that.
            author_row = card.select_one('div.a-row.a-color-secondary')
            if author_row:
                row_text = author_row.get_text(" ", strip=True)
                author_match = re.search(
                    r"by\s+([^|]+?)(?:\s*\|\s*Sold by)", row_text, re.IGNORECASE
                )
                if author_match:
                    author = author_match.group(1).strip()
            if not author:
                author_match = re.search(r"by\s+([^|]+?)(?:\s*\|\s*Sold by)", raw_text)
                if author_match:
                    author = author_match.group(1).strip()

            price = None
            buy_match = re.search(
                rf"Or\s+(?:{CURRENCY_PREFIX})?(\d+\.?\d*)\s+to\s+buy",
                raw_text, re.IGNORECASE)
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

        # Edition guard (spike t_e934a2a3 §6c): only report ASINs that resolve
        # to the KINDLE ebook edition. #tmmSwatches lists every format (Kindle /
        # Audiobook / Hardcover / Paperback) with its own price; a Kindle-ebook
        # listing always has a Kindle row WITH a price. If the swatch block
        # exists and shows format rows but the only prices come from print/audio
        # rows (no Kindle row, or a Kindle row without a price), this ASIN is
        # NOT the Kindle ebook edition → mark non-ebook so scraper.py drops it.
        # An EMPTY swatch container (Lightpanda renders the block but leaves it
        # blank) carries no format evidence → UNKNOWN (is_ebook unset, keep).
        swatch_box = soup.select_one('#tmmSwatches') or soup.select_one('div#formats')
        kindle_text = self._kindle_swatch_text(swatch_box)
        if kindle_text and re.search(PRICE_TOKEN, kindle_text, re.IGNORECASE):
            info["is_ebook"] = True
        elif swatch_box is not None and len(
                self._clean_text(swatch_box.get_text(" ", strip=True))) > 0:
            info["is_ebook"] = False

        # KINDLE row price — the EBOOK edition's own deal price (spike
        # t_e934a2a3 §5/6a). The apex block's basis price is the PRINT list
        # price ("Print List Price: $12.99" on B0B2P2N58X), which inflates
        # savings %. The Kindle row in #tmmSwatches / div#formats carries the
        # ebook's own price; prefer it and keep apex as fallback (Lightpanda
        # may leave #tmmSwatches empty).
        kindle_deal, kindle_list = self._kindle_row_prices(swatch_box)
        if kindle_deal is not None:
            info["price"] = kindle_deal
            info["price_source"] = "kindle_row"
        if kindle_list is not None:
            info["list_price"] = kindle_list
            info["list_source"] = "kindle_row"

        # Apex price-to-pay fallback: text like "$ 1 . 99" (Lightpanda leaves
        # .a-offscreen empty, skill documents .apex-pricetopay-value).
        if "price" not in info:
            apex = soup.select_one('.apex-pricetopay-value')
            if apex:
                price = self._clean_price(apex.get_text(" ", strip=True))
                if price:
                    info["price"] = price
                    info["price_source"] = "apex_pricetopay"

        # List price fallback: a "Kindle Price" basis element — the EBOOK's
        # own list price (spike §6a). NOTE (t_13047664): apex-basisprice-value
        # is the PRINT list price ("Print List Price: $19.99"), never the
        # ebook's own Digital List Price. Using it as the savings basis
        # inflated Shards of Earth to "90% off" when the real ebook list is
        # $5.00 (~60% off). We therefore do NOT fall back to the print basis:
        # if the page exposes no ebook-specific list price, list_price stays
        # unset and the caller's require_discount gate drops the book rather
        # than reporting an unverifiable savings %.
        if "list_price" not in info:
            # Layer 1 (t_ccbd16c0, spike RC-1): the ebook's Digital List
            # Price from the buybox apex-basisprice — the modern location of
            # the list price (Amazon stopped rendering a struck list inside
            # the Kindle swatch). ONLY the element labelled "Digital List
            # Price" is the ebook's own list; "Print List Price" is skipped
            # (never a savings basis — t_13047664).
            digital_list = self._buybox_digital_list_price(soup)
            if digital_list:
                info["list_price"] = digital_list
                info["list_source"] = "apex_basisprice_digital"
            else:
                basis_price = self._kindle_price_basis(soup)
                if basis_price:
                    info["list_price"] = basis_price
                    info["list_source"] = "kindle_price_basis"

        # Savings (spike §6a): recompute from price/list_price ourselves.
        # apex-savings-percentage references the PRINT list price and is
        # therefore not trustworthy — never read it.
        price = info.get("price")
        if price and info.get("list_price") and info["list_price"] > price:
            info["savings_pct"] = round((1 - price / info["list_price"]) * 100)

        cover = self.extract_cover_url(soup)
        if cover:
            info["cover_url"] = cover

        # Availability of the KINDLE edition (spike t_e934a2a3 §5/6b)
        avail = self._detect_availability(soup)
        if avail["available"] is not None:
            info["available"] = avail["available"]
        if avail["preorder"]:
            info["preorder"] = True

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
