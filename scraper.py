#!/usr/bin/env python3
"""Kindle Deals Bot — scrapes Amazon SFF deals and prints a Markdown report."""

import sys
import time
from pathlib import Path

import yaml

# Ensure project root is on Python path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from sources.amazon import AmazonDealsScraper
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


def download_cover(scraper: AmazonDealsScraper, cover_url: str, asin: str, covers_dir: Path) -> str | None:
    """Download a cover image and return the absolute local path, or None on failure."""
    if not cover_url or not asin:
        return None
    try:
        covers_dir.mkdir(parents=True, exist_ok=True)
        dest = covers_dir / f"{asin}.jpg"
        resp = scraper.session.get(cover_url, timeout=15, impersonate="chrome124")
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return str(dest.resolve())
    except Exception as e:
        print(f"  [WARN] Cover download failed for {asin}: {e}", file=sys.stderr)
        return None


def cleanup_old_covers(covers_dir: Path, days: int = 7) -> None:
    """Remove cover images older than `days` to avoid disk bloat."""
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

    # Clean old covers at start
    cleanup_old_covers(covers_dir)

    # --- Scrape ---
    print("🔍 Scraping Amazon Kindle SFF deals...", file=sys.stderr)
    scraper = AmazonDealsScraper(config)
    all_books = scraper.scrape_all()
    print(f"  Deal books scraped: {len(all_books)}", file=sys.stderr)

    # Also scrape Best Sellers for trending books
    print("🔍 Scraping Amazon Best Sellers...", file=sys.stderr)
    amz = config["sources"]["amazon"]
    # Find best seller URLs from config (keys containing 'best_sellers')
    bs_keys = [k for k in amz if "best_sellers" in k]
    for bs_key in bs_keys:
        bs_url = scraper.base_url + amz[bs_key]
        try:
            bs_books = scraper.scrape_best_sellers(bs_url)
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

    # --- Enrich with accurate product-page prices ---
    # Deal page prices can differ from the actual product page (KU vs buy price,
    # region-specific deals, dynamic pricing). Visit each product page for the
    # real apex-pricetopay price.
    print("💰 Fetching accurate product-page prices...", file=sys.stderr)
    for book in filtered:
        if not book.get("url"):
            continue
        try:
            soup = scraper.fetch_html(book["url"])
            if soup:
                # Apex price-to-pay (the actual current buy-box price)
                apex = soup.select_one('.apex-pricetopay-value .a-offscreen')
                if apex and apex.text.strip():
                    real_price = scraper._clean_price(apex.text.strip())
                    if real_price is not None and real_price > 0:
                        old = book.get("price")
                        book["price"] = real_price
                        if old != real_price:
                            print(f"  💵 {book['title'][:50]}... ${old} → ${real_price}", file=sys.stderr)

                # List price (the regular/print price before discount)
                basis = soup.select_one('.apex-basisprice-value .a-offscreen')
                if basis and basis.text.strip():
                    list_price = scraper._clean_price(basis.text.strip())
                    if list_price and list_price > 0:
                        book["list_price"] = list_price

                # Savings percentage
                savings_el = soup.select_one('.apex-savings-percentage')
                if savings_el:
                    savings_text = savings_el.text.strip()
                    # e.g. "-90%" → 90
                    import re as savings_re
                    pct = savings_re.search(r'(\d+)%', savings_text)
                    if pct:
                        book["savings_pct"] = int(pct.group(1))

                # Extract cover URL from product page
                cover_url = scraper.extract_cover_url(soup)
                if cover_url:
                    book["cover_url"] = cover_url
        except Exception:
            pass  # keep original price if product page fails

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
        
        # Tag tracked authors for promotion in report
        if book_filter.is_tracked_author(author):
            book["tracked_author"] = True
        
        # Always include in the daily report
        report_books.append(book)
        
        # Track in storage: mark as seen, detect new/price-drop for stats
        if is_new:
            new_count += 1
            storage.mark_seen(asin, title, price or 0.0, author, url)
            print(f"  🆕 NEW: {title} (${price})", file=sys.stderr)
        elif better_price:
            dropped_count += 1
            storage.mark_seen(asin, title, price or 0.0, author, url)
            print(f"  📉 DROP: {title} (${price})", file=sys.stderr)
        else:
            # Still update last_seen timestamp without changing price
            storage.mark_seen(asin, title, price or 0.0, author, url)

    # --- Download covers for reported books (capped) ---
    if covers_enabled:
        for book in report_books[:max_covers]:
            cover_url = book.get("cover_url")
            asin = book.get("asin", "")
            if cover_url and asin:
                cover_path = download_cover(scraper, cover_url, asin, covers_dir)
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
