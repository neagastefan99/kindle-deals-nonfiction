"""JSON-backed persistence for seen books and run history."""

import json
import os
from datetime import datetime, timezone
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
        """Record a book as seen, updating price if lower."""
        seen = self._read_seen()
        now = datetime.now(timezone.utc).isoformat()
        
        if asin in seen:
            entry = seen[asin]
            entry["last_seen"] = now
            if price < entry.get("lowest_price", float("inf")):
                entry["lowest_price"] = price
                entry["price_dropped_on"] = now
        else:
            seen[asin] = {
                "title": title,
                "author": author,
                "url": url,
                "first_seen": now,
                "last_seen": now,
                "lowest_price": price,
            }
        self._write_seen(seen)
    
    def log_run(self, stats: dict[str, Any]) -> None:
        """Append run statistics to the log."""
        stats["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(self.log_path, "a") as f:
            f.write(json.dumps(stats, default=str) + "\n")
