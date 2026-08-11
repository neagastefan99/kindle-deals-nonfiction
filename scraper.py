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
from sources.amazon import AmazonDealsScraper
from sources.lightpanda_fetcher import LightpandaFetcher
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
        return AmazonDealsScraper(config, fetcher=LightpandaFetcher(config))
    print(f"  🔌 Engine: curl_cffi", file=sys.stderr)
    return AmazonDealsScraper(config)


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

    # --- Enrich: batch-fetch all product pages, parse once ---
    print("💰 Fetching accurate product-page prices (batch)...", file=sys.stderr)
    product_urls = [b["url"] for b in filtered if b.get("url")]
    soups = scraper.prefetch(product_urls)
    for book in filtered:
        soup = soups.get(book.get("url", ""))
        if not soup:
            continue
        info = scraper.parse_product_page(soup)
        if info.get("price"):
            old = book.get("price")
            book["price"] = info["price"]
            if old != info["price"]:
                print(f"  💵 {book['title'][:50]}... ${old} → ${info['price']}", file=sys.stderr)
        if info.get("list_price"):
            book["list_price"] = info["list_price"]
        if info.get("savings_pct"):
            book["savings_pct"] = info["savings_pct"]
        if info.get("cover_url"):
            book["cover_url"] = info["cover_url"]

    # Re-filter with accurate prices (some may now exceed max_price)
    filtered = book_filter.apply(filtered)
    print(f"  After price enrichment: {len(filtered)} books", file=sys.stderr)

    # --- Deduplicate & track ---
    new_count = 0
    dropped_count = 0
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
        "reported": len(report_books),
    })


if __name__ == "__main__":
    main()
