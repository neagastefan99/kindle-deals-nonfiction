"""Fallback fetcher — tries Lightpanda first, falls back to curl_cffi on total failure."""

from typing import Any
from bs4 import BeautifulSoup


class FallbackFetcher:
    """Wraps two fetchers: tries primary first, falls back to fallback."""

    def __init__(self, primary_fetcher, fallback_fetcher, config: dict[str, Any]):
        self.primary = primary_fetcher
        self.fallback = fallback_fetcher
        self.enabled = config.get("scraping", {}).get("auto_fallback", False)

    def fetch_all(self, urls: list[str]) -> dict[str, BeautifulSoup | None]:
        results = self.primary.fetch_all(urls)
        if self.enabled:
            all_none = all(v is None for v in results.values())
            if all_none and len(urls) > 0:
                print("  [FALLBACK] Lightpanda returned no results; retrying with curl_cffi...",
                      flush=True)
                return self.fallback.fetch_all(urls)
        return results
