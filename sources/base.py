"""Base scraper with browser-impersonating HTTP, retry logic, and rate-limiting."""

import random
import time
from typing import Any

from curl_cffi import requests
from bs4 import BeautifulSoup


class BaseScraper:
    """HTTP scraper that impersonates Chrome 124 to avoid bot detection.
    
    curl_cffi replicates Chrome's TLS fingerprint — Amazon sees
    the same handshake as a real Chrome browser, not a Python script.
    """
    
    # Backoff between retries for rate-limit (429) / server-error (5xx)
    # responses. Amazon/CloudFront 503 needs a longer cool-down than a
    # network blip, so use an escalating ladder instead of 2**attempt.
    RETRY_BACKOFF = [5, 15, 30]
    
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.scraping_cfg = config.get("scraping", {})
        self.user_agents = self.scraping_cfg.get("user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ])
        self.delay = self.scraping_cfg.get("request_delay", 2)
        self.session = requests.Session()
        
        # Force US locale: Amazon uses cookies + headers to detect region.
        # Without this, IP-based detection shows RON instead of USD.
        self.session.cookies.update({
            "session-id": "130-0000000-0000000",
            "ubid-main": "130-0000000-0000000",
            "lc-acbuk": "en_US",                     # force US locale
            "i18n-prefs": "USD",                     # force USD currency
            "session-id-time": "2082787201l",
        })
        self._rotate_ua()
    
    def _rotate_ua(self) -> None:
        ua = random.choice(self.user_agents)
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
    
    def _sleep(self) -> None:
        jitter = random.uniform(0.5, 1.5)
        time.sleep(self.delay * jitter)
    
    def fetch_html(self, url: str, max_retries: int = 3) -> BeautifulSoup | None:
        """Fetch a URL and return a BeautifulSoup object. Retries on failure.
        Uses impersonate='chrome124' for TLS fingerprint mimicry.

        HTTP 429 / 5xx (rate-limit, CloudFront 503) are retried with an
        escalating backoff (5s/15s/30s) since Amazon rate-limits need longer
        cool-down than a transient network blip.
        """
        for attempt in range(max_retries):
            try:
                self._rotate_ua()
                resp = self.session.get(
                    url, 
                    timeout=30,
                    impersonate="chrome124",  # TLS fingerprint impersonation
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries - 1:
                        backoff = self.RETRY_BACKOFF[min(attempt, len(self.RETRY_BACKOFF) - 1)]
                        print(f"  [WARN] Attempt {attempt+1}/{max_retries} for {url}: "
                              f"HTTP {resp.status_code} (rate-limit/server error), "
                              f"retrying in {backoff}s")
                        time.sleep(backoff)
                        continue
                    print(f"  [WARN] Attempt {attempt+1}/{max_retries} for {url}: "
                          f"HTTP {resp.status_code} (rate-limit/server error) — giving up")
                    return None
                resp.raise_for_status()
                self._sleep()
                return BeautifulSoup(resp.text, "lxml")
            except requests.RequestException as e:
                print(f"  [WARN] Attempt {attempt+1}/{max_retries} for {url}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        return None
    
    def fetch_json(self, url: str, max_retries: int = 3) -> dict | list | None:
        """Fetch a URL expecting JSON response. Used for back-end API scraping.
        Same 429/5xx rate-limit retry behavior as fetch_html.
        """
        for attempt in range(max_retries):
            try:
                self._rotate_ua()
                resp = self.session.get(
                    url,
                    timeout=30,
                    impersonate="chrome124",
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries - 1:
                        backoff = self.RETRY_BACKOFF[min(attempt, len(self.RETRY_BACKOFF) - 1)]
                        print(f"  [WARN] API attempt {attempt+1}/{max_retries} for {url}: "
                              f"HTTP {resp.status_code} (rate-limit/server error), "
                              f"retrying in {backoff}s")
                        time.sleep(backoff)
                        continue
                    print(f"  [WARN] API attempt {attempt+1}/{max_retries} for {url}: "
                          f"HTTP {resp.status_code} (rate-limit/server error) — giving up")
                    return None
                resp.raise_for_status()
                self._sleep()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                print(f"  [WARN] API attempt {attempt+1}/{max_retries} for {url}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        return None
