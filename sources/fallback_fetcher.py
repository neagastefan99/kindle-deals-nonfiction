"""Fallback fetcher — tries Lightpanda first, falls back to curl_cffi on total failure.

The primary fetcher (LightpandaFetcher) already retries retryable failures
(HTTP 429/5xx rate-limits, error-page bodies) with backoff internally, so a
None result reaching this class means retries were EXHAUSTED. We only fall
back to curl_cffi then, and we log the failure count + reasons clearly.
"""

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
        if not (self.enabled and urls):
            return results

        failed = [u for u in urls if results.get(u) is None]
        if len(failed) == len(urls):
            # Every URL failed after the primary's retries — log why, then fall back.
            detail = ""
            failures = getattr(self.primary, "last_failures", {})
            if failures:
                counts: dict[str, int] = {}
                for reason in failures.values():
                    counts[reason] = counts.get(reason, 0) + 1
                detail = " — " + ", ".join(
                    f"{c}× {r}" for r, c in sorted(counts.items())
                )
            print(
                f"  [FALLBACK] Lightpanda returned no results after retries "
                f"({len(urls)} URL(s) failed{detail}); retrying with curl_cffi...",
                flush=True,
            )
            return self.fallback.fetch_all(urls)

        if failed:
            # Partial success: keep the good results, warn about the stragglers.
            print(
                f"  [WARN] Lightpanda partial failure: {len(failed)}/{len(urls)} "
                f"URL(s) still failing after retries: {', '.join(failed)}",
                flush=True,
            )
        return results
