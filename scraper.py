#!/usr/bin/env python3
"""Kindle Deals Bot — scrapes Amazon SFF deals and prints a Markdown report."""

import sys
import time
from pathlib import Path

import yaml

# Ensure project root is on Python path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from curl_cffi import requests as cffi_requests
from sources.amazon import AmazonDealsScraper, CurlCffiFetcher
from sources.lightpanda_fetcher import LightpandaFetcher
from sources.fallback_fetcher import FallbackFetcher
from filters import BookFilter
from formatter import format_report, format_empty_report
from storage import Storage


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def make_scraper(config: dict) -> AmazonDealsScraper:
    """Build the scraper with the configured engine (lightpanda or curl_cffi)."""
    engine = config.get("scraping", {}).get("engine", "curl_cffi")
    if engine == "lightpanda":
        print(f"  ⚡ Engine: Lightpanda browser", file=sys.stderr)
        primary = LightpandaFetcher(config)
        fallback = CurlCffiFetcher(config)
        fetcher = FallbackFetcher(primary, fallback, config)
        return AmazonDealsScraper(config, fetcher=fetcher)
    print(f"  🔌 Engine: curl_cffi", file=sys.stderr)
    return AmazonDealsScraper(config)


def make_enrichment_fetcher(config: dict):
    """Fetcher for product-page enrichment.

    Product pages are fetched with curl_cffi DIRECTLY (not lightpanda).
    Root cause (t_f893b2c1): lightpanda's User-Agent is "Lightpanda/1.0"
    with Sec-CH-UA "Lightpanda" (and its --user-agent flag rejects browser
    impersonation), so Amazon's bot protection fingerprints it and serves
    the validateCaptcha interstitial on ~75% of product-page requests —
    probabilistic, independent of wait_ms. curl_cffi with chrome124 TLS/UA
    impersonation + the US cookie jar passes ~100%.

    Set `scraping.product_engine: lightpanda` to restore the old
    lightpanda+fallback behavior for product pages.
    """
    product_engine = config.get("scraping", {}).get("product_engine", "curl_cffi")
    if product_engine == "lightpanda":
        return None  # caller falls back to scraper.prefetch (lightpanda+fallback)
    return CurlCffiFetcher(config)


def enrich_books(filtered: list[dict], soups: dict, scraper: AmazonDealsScraper) -> list[dict]:
    """Enrich books with LIVE product-page prices (HARD RULE, t_f893b2c1).

    NEVER report a book whose live price was not confirmed from its product
    page: a book is kept only when a soup exists AND parse_product_page
    yields a price. Everything else (missing page, unparseable page, no
    price on the page) is dropped with a reason — the possibly-stale
    deal-listing price is never carried into the report.
    """
    enriched = []
    for book in filtered:
        soup = soups.get(book.get("url", ""))
        if not soup:
            # (e) failed enrichment → DROP (never keep the stale deal-feed price)
            print(f"  🚫 DROP (no product page): {book['title'][:50]}", file=sys.stderr)
            continue
        info = scraper.parse_product_page(soup)
        if info.get("is_ebook") is False:
            # Edition guard (§6c): ASIN is NOT the Kindle ebook edition
            # (print/audiobook-only listing) — drop, don't report its price.
            book["is_ebook"] = False
            continue
        if not info.get("price"):
            # (e) ambiguous enrichment: no confirmed live price → DROP
            print(f"  🚫 DROP (no live price): {book['title'][:50]}", file=sys.stderr)
            continue
        book["price"] = info["price"]
        if info.get("list_price"):
            book["list_price"] = info["list_price"]
        if info.get("savings_pct") is not None:
            book["savings_pct"] = info["savings_pct"]
        if info.get("cover_url"):
            book["cover_url"] = info["cover_url"]
        if "available" in info:
            book["available"] = info["available"]
        if info.get("preorder"):
            book["preorder"] = True
        enriched.append(book)
    return enriched


def download_cover(cover_url: str, asin: str, covers_dir: Path) -> str | None:
    """Download a cover image via curl_cffi (images don't need JS rendering)."""
    if not cover_url or not asin:
        return None
    try:
        covers_dir.mkdir(parents=True, exist_ok=True)
        dest = covers_dir / f"{asin}.jpg"
        resp = cffi_requests.get(cover_url, timeout=15, impersonate="chrome124")
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return str(dest.resolve())
    except Exception as e:
        print(f"  [WARN] Cover download failed for {asin}: {e}", file=sys.stderr)
        return None


def cleanup_old_covers(covers_dir: Path, days: int = 7) -> None:
    if not covers_dir.exists():
        return
    cutoff = time.time() - (days * 86400)
    for f in covers_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except OSError:
                pass


def is_reportable(book: dict) -> bool:
    """Availability gate (spike t_e934a2a3 §6b): drop books whose product
    page says the Kindle edition is currently unavailable or is a pre-order
    (not buyable/instantly downloadable right now). Unknown → keep."""
    return book.get("available", True) is not False and not book.get("preorder", False)


def main() -> None:
    config = load_config()
    storage = Storage(PROJECT_ROOT / "data")
    book_filter = BookFilter(config)

    # Covers config
    covers_cfg = config.get("covers", {})
    covers_enabled = covers_cfg.get("enabled", True)
    covers_dir = PROJECT_ROOT / covers_cfg.get("dir", "data/covers")
    max_covers = covers_cfg.get("max_count", 10)

    cleanup_old_covers(covers_dir)

    # --- Scrape ---
    print("🔍 Scraping Amazon Kindle SFF deals...", file=sys.stderr)
    scraper = make_scraper(config)
    all_books = scraper.scrape_all()
    print(f"  Deal books scraped: {len(all_books)}", file=sys.stderr)

    # Best Sellers — batch fetch + parse
    print("🔍 Scraping Amazon Best Sellers...", file=sys.stderr)
    amz = config["sources"]["amazon"]
    bs_keys = [k for k in amz if "best_sellers" in k]
    for bs_key in bs_keys:
        bs_url = scraper.base_url + amz[bs_key]
        try:
            bs_soup = scraper.prefetch([bs_url]).get(bs_url)
            bs_books = scraper.parse_best_sellers(bs_soup)
            print(f"  {bs_key}: {len(bs_books)} books", file=sys.stderr)
            existing_asins = {b["asin"] for b in all_books if b.get("asin")}
            for book in bs_books:
                if book.get("asin") and book["asin"] not in existing_asins:
                    existing_asins.add(book["asin"])
                    all_books.append(book)
        except Exception as e:
            print(f"  [WARN] {bs_key} failed: {e}", file=sys.stderr)
    print(f"  Total combined: {len(all_books)} books", file=sys.stderr)

    if not all_books:
        print(format_empty_report())
        storage.log_run({"scraped": 0, "filtered": 0, "new": 0, "price_drops": 0, "error": "No books scraped"})
        return

    # --- Filter ---
    filtered = book_filter.apply(all_books)
    print(f"  After filtering: {len(filtered)} books", file=sys.stderr)

    # --- Enrich: fetch all product pages (curl_cffi — lightpanda is
    # captcha-blocked on product pages, see make_enrichment_fetcher), then
    # parse once. The HARD RULE lives in enrich_books(): a book with no
    # product-page-confirmed live price is DROPPED, never reported with the
    # possibly-stale deal-listing price. ---
    print("💰 Fetching accurate product-page prices (batch)...", file=sys.stderr)
    product_urls = [b["url"] for b in filtered if b.get("url")]
    enrichment_fetcher = make_enrichment_fetcher(config)
    if enrichment_fetcher is not None:
        soups = enrichment_fetcher.fetch_all(product_urls)
    else:
        soups = scraper.prefetch(product_urls)  # lightpanda + curl_cffi fallback
    filtered = enrich_books(filtered, soups, scraper)
    dropped_enrich = len(product_urls) - len(filtered)
    if dropped_enrich:
        print(f"  🚫 Enrichment dropped {dropped_enrich} book(s) "
              f"(no page / no live price / non-Kindle)", file=sys.stderr)

    # --- Edition guard (§6c): drop non-Kindle-ebook ASINs ---
    guard_before = len(filtered)
    filtered = [b for b in filtered if b.get("is_ebook", True)]
    guard_dropped = guard_before - len(filtered)
    if guard_dropped:
        print(f"  🚫 Edition guard: dropped {guard_dropped} non-Kindle-ebook listing(s)", file=sys.stderr)

    # --- Availability gate: drop books unavailable on Kindle / pre-orders ---
    avail_before = len(filtered)
    filtered = [b for b in filtered if is_reportable(b)]
    avail_dropped = avail_before - len(filtered)
    if avail_dropped:
        print(f"  ❌ Availability: dropped {avail_dropped} unavailable/pre-order book(s)", file=sys.stderr)

    # Re-filter with accurate prices (some may now exceed max_price)
    # and the BookBub limited-time gate (only real >=50% discounts).
    filtered = book_filter.apply(filtered, require_discount=True)
    print(f"  After price enrichment: {len(filtered)} books", file=sys.stderr)

    # --- Deduplicate & track ---
    new_count = 0
    dropped_count = 0
    suppressed_count = 0
    report_books = []

    for book in filtered:
        asin = book.get("asin", "")
        title = book.get("title", "")
        author = book.get("author", "")
        price = book.get("price")
        url = book.get("url", "")

        if not asin or not title:
            continue

        is_new = storage.is_new(asin)
        better_price = storage.is_better_price(asin, price or 999.99)

        if book_filter.is_tracked_author(author):
            book["tracked_author"] = True

        # BookBub-inspired gates: never re-surface a book at a WORSE price
        # than its best within the last 30 days, and drop books that have sat
        # at this price for >14 days (permanent markdown, not a limited-time
        # deal). Gated books are still recorded (history updates), just not
        # reported as a deal.
        if not storage.should_report(asin, price or 0.0):
            storage.mark_seen(asin, title, price or 0.0, author, url)
            suppressed_count += 1
            print(f"  ⏸ GATED: {title} (${price})", file=sys.stderr)
            continue

        report_books.append(book)

        if is_new:
            new_count += 1
            storage.mark_seen(asin, title, price or 0.0, author, url)
            print(f"  🆕 NEW: {title} (${price})", file=sys.stderr)
        elif better_price:
            dropped_count += 1
            storage.mark_seen(asin, title, price or 0.0, author, url)
            print(f"  📉 DROP: {title} (${price})", file=sys.stderr)
        else:
            storage.mark_seen(asin, title, price or 0.0, author, url)

    # --- Download covers for reported books (capped) ---
    if covers_enabled:
        for book in report_books[:max_covers]:
            cover_url = book.get("cover_url")
            asin = book.get("asin", "")
            if cover_url and asin:
                cover_path = download_cover(cover_url, asin, covers_dir)
                if cover_path:
                    book["cover_path"] = cover_path
    else:
        for book in report_books:
            book.pop("cover_path", None)

    # --- Format & output ---
    print(f"  Reporting: {new_count} new + {dropped_count} price drops", file=sys.stderr)
    report = format_report(report_books, new_count, dropped_count)
    print(report)

    # --- Log run ---
    storage.log_run({
        "scraped": len(all_books),
        "filtered": len(filtered),
        "new": new_count,
        "price_drops": dropped_count,
        "suppressed": suppressed_count,
        "reported": len(report_books),
    })


if __name__ == "__main__":
    main()
