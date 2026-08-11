"""Lightpanda fetcher — batch browser fetch via the Lightpanda CLI.

Replaces curl_cffi as the transport layer. Lightpanda runs headless
Chromium-free JS rendering in a single process, fetching all URLs in one
batch (~3s for 3 pages, ~5s for 10 product pages vs 25-30s with curl_cffi).

Requires: lightpanda binary on PATH or configured via config:
  scraping:
    lightpanda_bin: /tmp/lightpanda-spike/lightpanda
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


# US locale cookies injected into the jar (Amazon region detection)
US_COOKIES = [
    {"name": "session-id", "value": "130-0000000-0000000", "domain": ".amazon.com", "path": "/", "secure": False, "httpOnly": False},
    {"name": "ubid-main", "value": "130-0000000-0000000", "domain": ".amazon.com", "path": "/", "secure": False, "httpOnly": False},
    {"name": "lc-acbuk", "value": "en_US", "domain": ".amazon.com", "path": "/", "secure": False, "httpOnly": False},
    {"name": "i18n-prefs", "value": "USD", "domain": ".amazon.com", "path": "/", "secure": False, "httpOnly": False},
    {"name": "session-id-time", "value": "2082787201l", "domain": ".amazon.com", "path": "/", "secure": False, "httpOnly": False},
]


class LightpandaFetcher:
    """Fetches multiple URLs in one Lightpanda batch process, cache-aware."""

    def __init__(self, config: dict[str, Any]):
        self.scraping_cfg = config.get("scraping", {})
        self.bin = self.scraping_cfg.get(
            "lightpanda_bin", "/tmp/lightpanda-spike/lightpanda"
        )
        self.wait_ms = self.scraping_cfg.get("lightpanda_wait_ms", 6000)
        self.cookie_path = Path(self.scraping_cfg.get(
            "lightpanda_cookies", "/tmp/lightpanda-cookies.json"
        ))
        self._cache: dict[str, BeautifulSoup | None] = {}
        self._ensure_cookie_jar()

    def _ensure_cookie_jar(self) -> None:
        if self.cookie_path.exists():
            return
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cookie_path, "w") as f:
            json.dump(US_COOKIES, f, indent=1)

    def fetch_all(self, urls: list[str]) -> dict[str, BeautifulSoup | None]:
        """Fetch uncached URLs in one batch. Returns {url: soup} for all."""
        missing = [u for u in urls if u not in self._cache]
        if missing:
            cmd = [self.bin, "fetch", *missing, "--dump", "html",
                   "--wait-ms", str(self.wait_ms),
                   "--cookie", str(self.cookie_path),
                   "--json"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"  [WARN] lightpanda batch failed: {e}", flush=True)
                for u in missing:
                    self._cache[u] = None
                return dict(self._cache)

            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                print(f"  [WARN] lightpanda bad output: {proc.stderr[:200]}", flush=True)
                for u in missing:
                    self._cache[u] = None
                return dict(self._cache)

            # Single URL → bare result object; multiple → {"results": [...]}
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
            elif isinstance(data, list):
                results = data
            else:
                results = [data]

            # Map results to requested URLs. Lightpanda percent-encodes URLs
            # (e.g. ':' -> '%3A'), so match by decoded form.
            def norm(u: str) -> str:
                from urllib.parse import unquote
                return unquote(u)

            results_by_norm = {}
            for r in results:
                if not isinstance(r, dict):
                    continue
                url = r.get("url", "")
                if url:
                    results_by_norm[norm(url)] = r

            for u in missing:
                r = results_by_norm.get(norm(u))
                if r is None:
                    self._cache[u] = None
                    continue
                status = r.get("http_status", 0)
                content = r.get("content", "")
                if status == 200 and content:
                    self._cache[u] = BeautifulSoup(content, "lxml")
                else:
                    self._cache[u] = None

        return dict(self._cache)
