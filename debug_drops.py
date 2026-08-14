#!/usr/bin/env python3
"""Debug harness: run the real filter chain end-to-end and log WHY each
book is dropped (or reported). Mirrors scraper.py main() exactly, but
records a reason for every book at every gate.

Usage: ./venv/bin/python debug_drops.py
Output: data/debug_drops_<ts>.json + per-book stderr lines.
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from sources.amazon import AmazonDealsScraper, CurlCffiFetcher
from sources.lightpanda_fetcher import LightpandaFetcher
from sources.fallback_fetcher import FallbackFetcher
from filters import BookFilter
from storage import Storage


def load_config() -> dict:
    with open(PROJECT_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def make_scraper(config: dict) -> AmazonDealsScraper:
    engine = config.get("scraping", {}).get("engine", "curl_cffi")
    if engine == "lightpanda":
        primary = LightpandaFetcher(config)
        fallback = CurlCffiFetcher(config)
        fetcher = FallbackFetcher(primary, fallback, config)
        return AmazonDealsScraper(config, fetcher=fetcher)
    return AmazonDealsScraper(config)


def main() -> None:
    config = load_config()
    storage = Storage(PROJECT_ROOT / "data")
    book_filter = BookFilter(config)
    scraper = make_scraper(config)

    rows: list[dict] = []
    reasons: Counter[str] = Counter()

    def log(reason: str, book: dict, extra: dict | None = None):
        reasons[reason] += 1
        row = {
            "reason": reason,
            "asin": book.get("asin"),
            "title": book.get("title", "")[:70],
            "author": book.get("author", ""),
        }
        for k in ("price", "list_price", "list_source", "savings_pct", "is_ebook",
                  "available", "preorder", "url"):
            row[k] = book.get(k)
        if extra:
            row.update(extra)
        rows.append(row)
        print(f"  [{reason}] {row['title']} "
              f"(asin={row['asin']} price={row['price']} "
              f"list={row.get('list_price')} sav={row.get('savings_pct')} "
              f"ebook={row.get('is_ebook')} avail={row.get('available')})",
              file=sys.stderr)

    # ── Stage 1: scrape deals pages ────────────────────────────────
    all_books = scraper.scrape_all()

    # Best sellers — mirror scraper.py main(): every config key with
    # "best_sellers" in it is a Best Sellers page to merge in.
    try:
        amz = config["sources"]["amazon"]
        bs_keys = [k for k in amz if "best_sellers" in k]
        for bs_key in bs_keys:
            bs_url = scraper.base_url + amz[bs_key]
            bs_soup = scraper.prefetch([bs_url]).get(bs_url)
            bs_books = scraper.parse_best_sellers(bs_soup)
            existing_asins = {b["asin"] for b in all_books if b.get("asin")}
            for book in bs_books:
                if book.get("asin") and book["asin"] not in existing_asins:
                    existing_asins.add(book["asin"])
                    all_books.append(book)
    except Exception as e:
        print(f"  [WARN] best sellers failed: {e}", file=sys.stderr)

    print(f"  scraped total: {len(all_books)}", file=sys.stderr)
    for b in all_books:
        log("scraped", b)

    # ── Stage 2: pre-enrichment filter (price cap + genre) ─────────
    filtered: list[dict] = []
    for book in all_books:
        price = book.get("price")
        if not book_filter.matches_price(price):
            log("price_cap_pre", book, {"cap": book_filter.max_price})
            continue
        if not book_filter.matches_genre(book.get("title", ""),
                                         book.get("author", ""),
                                         from_sff_page=book.get("from_sff_page", False)):
            log("genre", book)
            continue
        filtered.append(book)

    # ── Stage 3: enrichment ────────────────────────────────────────
    # Product pages use the SAME route as scraper.py main(): curl_cffi
    # directly (make_enrichment_fetcher), NOT the listing engine — lightpanda
    # is captcha-blocked on product pages (t_f893b2c1).
    from scraper import make_enrichment_fetcher
    product_urls = [b["url"] for b in filtered if b.get("url")]
    enrichment_fetcher = make_enrichment_fetcher(config)
    if enrichment_fetcher is not None:
        soups = enrichment_fetcher.fetch_all(product_urls)
    else:
        soups = scraper.prefetch(product_urls)  # lightpanda + curl_cffi fallback

    # Region check (t_13047664) — mirror scraper.py main()
    from scraper import detect_region
    for soup in soups.values():
        if soup is None:
            continue
        region, evidence = detect_region(str(soup))
        if region == "non-US":
            print(f"  ⚠️ REGION WARNING: enrichment pages resolved to {evidence} — "
                  f"prices may differ from the US storefront the user sees!",
                  file=sys.stderr)
            break

    from scraper import enrich_books
    enriched = enrich_books(filtered, soups, scraper)
    filtered = enriched

    # ── Stage 4: edition guard ─────────────────────────────────────
    filtered = [b for b in filtered if b.get("is_ebook", True)]

    # ── Stage 5: availability gate ─────────────────────────────────
    avail_kept = []
    for book in filtered:
        if book.get("available", True) is False or book.get("preorder", False):
            log("availability", book)
            continue
        avail_kept.append(book)
    filtered = avail_kept

    # ── Stage 5.5: history-based deal fallback (t_d84465dd) ────────
    # The ebook Digital List Price is only server-rendered for ~1 in 7
    # pages, so most real deals have no savings_pct to pass the >=50% gate.
    # A book whose page exposes no digital list (savings_pct AND list_price
    # unset) is flagged list_source="history" when under the cap — the
    # best-price-30d / anti-stale storage gates in Stage 7 still decide
    # whether it's actually a fresh, at-or-below-best drop.
    for book in filtered:
        if (book.get("savings_pct") is None and book.get("list_price") is None
                and book_filter.matches_price(book.get("price"))):
            book["list_source"] = "history"

    # ── Stage 6: post-enrichment re-filter (discount gate) ─────────
    refiltered: list[dict] = []
    for book in filtered:
        price = book.get("price")
        if not book_filter.matches_price(price):
            log("price_cap_post", book, {"cap": book_filter.max_price})
            continue
        if not book_filter.matches_discount_or_history(book):
            log("savings_lt_50", book, {"min": book_filter.min_savings_pct})
            continue
        refiltered.append(book)
    filtered = refiltered

    # ── Stage 7: storage gates (best-price 30d / anti-stale) ───────
    reported = 0
    for book in filtered:
        asin = book.get("asin", "")
        price = book.get("price")
        if not storage.best_price_30d(asin, price or 0.0):
            log("best_price_30d", book, {"hist": storage._read_seen().get(asin, {}).get("price_history", {})})
            continue
        if storage.is_stale(asin, price or 0.0):
            log("anti_stale", book, {"days": storage.days_at_price(asin, price or 0.0)})
            continue
        reported += 1
        log("REPORT", book)

    # ── Summary ────────────────────────────────────────────────────
    print("\n=== DROP REASON STATS ===", file=sys.stderr)
    for reason, count in reasons.most_common():
        print(f"  {count:3d}  {reason}", file=sys.stderr)
    print(f"  {reported:3d}  REPORTED", file=sys.stderr)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = PROJECT_ROOT / "data" / f"debug_drops_{ts}.json"
    out_path.write_text(json.dumps(rows, indent=2, default=str))
    print(f"  → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
