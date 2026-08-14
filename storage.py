"""JSON-backed persistence for seen books and run history."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.seen_path = self.data_dir / "seen_books.json"
        self.log_path = self.data_dir / "run_log.jsonl"
    
    def _read_seen(self) -> dict[str, dict[str, Any]]:
        if not self.seen_path.exists():
            return {}
        with open(self.seen_path) as f:
            return json.load(f)
    
    def _write_seen(self, data: dict[str, dict[str, Any]]) -> None:
        with open(self.seen_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @staticmethod
    def _today() -> str:
        """UTC date key used in price_history (YYYY-MM-DD)."""
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _cutoff(lookback_days: int) -> str:
        """ISO date string `lookback_days` ago — oldest date still in the window."""
        return (datetime.now(timezone.utc).date() - timedelta(days=lookback_days)).isoformat()

    def is_new(self, asin: str) -> bool:
        """Check if this ASIN has never been seen before."""
        return asin not in self._read_seen()
    
    def is_better_price(self, asin: str, price: float) -> bool:
        """Check if this price is lower than previously recorded."""
        seen = self._read_seen()
        if asin not in seen:
            return True
        return price < seen[asin].get("lowest_price", float("inf"))
    
    def mark_seen(self, asin: str, title: str, price: float, 
                  author: str = "", url: str = "") -> None:
        """Record a book as seen, updating price if lower and appending today's
        price to the per-ASIN price_history (date -> price)."""
        seen = self._read_seen()
        now = datetime.now(timezone.utc).isoformat()
        today = self._today()
        
        if asin in seen:
            entry = seen[asin]
            entry["last_seen"] = now
            # Refresh metadata: parser fixes can correct earlier mangled
            # title/author/url values (e.g. author regex grabbed 'by' from
            # the title). Only update non-empty new values.
            if title:
                entry["title"] = title
            if author:
                entry["author"] = author
            if url:
                entry["url"] = url
            if price < entry.get("lowest_price", float("inf")):
                entry["lowest_price"] = price
                entry["price_dropped_on"] = now
            # price_history: one entry per day (last price of the day wins).
            # Backfills legacy entries that predate price tracking.
            history = entry.setdefault("price_history", {})
            history[today] = price
        else:
            seen[asin] = {
                "title": title,
                "author": author,
                "url": url,
                "first_seen": now,
                "last_seen": now,
                "lowest_price": price,
                "price_history": {today: price},
            }
        self._write_seen(seen)

    def best_price_30d(self, asin: str, price: float, lookback_days: int = 30) -> bool:
        """BookBub best-price gate: True unless a cheaper price was recorded
        within the last `lookback_days`. Reporting at a price HIGHER than the
        recent best would re-surface a worse deal than we already saw."""
        seen = self._read_seen()
        if asin not in seen:
            return True
        entry = seen[asin]
        cutoff = self._cutoff(lookback_days)
        hist = entry.get("price_history", {})
        recent = [p for d, p in hist.items() if d >= cutoff]
        if recent:
            return price <= min(recent)
        # Legacy entry without price_history: fall back to the recorded lowest
        # if it was set within the window, otherwise allow (no data to judge).
        pdo = entry.get("price_dropped_on") or ""
        if pdo[:10] >= cutoff:
            return price <= entry.get("lowest_price", float("inf"))
        return True

    def days_at_price(self, asin: str, price: float, lookback_days: int = 30) -> int:
        """Number of days within the lookback window this book was recorded at
        exactly `price` (from price_history)."""
        seen = self._read_seen()
        if asin not in seen:
            return 0
        cutoff = self._cutoff(lookback_days)
        hist = seen[asin].get("price_history", {})
        return sum(1 for d, p in hist.items() if d >= cutoff and abs(p - price) < 1e-6)

    def is_stale(self, asin: str, price: float, max_days: int = 14,
                 lookback_days: int = 30) -> bool:
        """BookBub anti-stale gate: True if the book has been at this price for
        more than `max_days` within the lookback window — a permanent markdown,
        not a limited-time deal."""
        return self.days_at_price(asin, price, lookback_days) > max_days

    def should_report(self, asin: str, price: float, max_stale_days: int = 14,
                      lookback_days: int = 30) -> bool:
        """Combined BookBub-inspired gate used before reporting: surface a deal
        only if the current price is the best seen in the lookback window and
        the book hasn't been parked at this price for too long."""
        return (
            self.best_price_30d(asin, price, lookback_days)
            and not self.is_stale(asin, price, max_stale_days, lookback_days)
        )
    
    def log_run(self, stats: dict[str, Any]) -> None:
        """Append run statistics to the log."""
        stats["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(self.log_path, "a") as f:
            f.write(json.dumps(stats, default=str) + "\n")
