"""Unit tests for the Kindle Non-Fiction Deals Bot.

Run: cd ~/kindle-deals-nonfiction && PYTHONPATH=. venv/bin/python -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import yaml

from filters import BookFilter
from scraper import is_reportable
from sources.lightpanda_fetcher import LightpandaFetcher


@pytest.fixture
def config():
    cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml"))
    return cfg


@pytest.fixture
def bf(config):
    return BookFilter(config)


def nf_book(**over):
    """A realistic non-fiction book."""
    b = {
        "asin": "B0TEST00001",
        "title": "Six Easy Pieces: Essentials of Physics Explained",
        "author": "Richard Feynman",
        "price": 1.99,
        "url": "https://www.amazon.com/dp/B0TEST00001",
    }
    b.update(over)
    return b


def fic_book(**over):
    """A fiction book that should be excluded."""
    b = {
        "asin": "B0TEST00002",
        "title": "The Ministry for the Future: A Novel",
        "author": "Kim Stanley Robinson",
        "price": 1.99,
        "url": "https://www.amazon.com/dp/B0TEST00002",
    }
    b.update(over)
    return b


# ─── Price ──────────────────────────────────────────────────────────

class TestPrice:
    def test_under_max(self, bf):
        assert bf.matches_price(4.99) is True

    def test_over_max(self, bf):
        assert bf.matches_price(12.99) is False

    def test_none(self, bf):
        assert bf.matches_price(None) is False


# ─── Topic matching (genres list) ───────────────────────────────────

class TestTopic:
    def test_science(self, bf):
        assert bf.matches_genre("The Science of Everything") is True

    def test_history(self, bf):
        assert bf.matches_genre("A History of the Vikings") is True

    def test_philosophy(self, bf):
        assert bf.matches_genre("Philosophy for Beginners") is True

    def test_biography(self, bf):
        assert bf.matches_genre("The Life of Einstein") is True

    def test_no_topic_match(self, bf):
        assert bf.matches_genre("Cooking with Fire") is False


# ─── Fiction exclusion (exclude_keywords) ───────────────────────────

class TestFictionExclusion:
    def test_novel_subtitle(self, bf):
        assert len(bf.apply([fic_book()])) == 0

    def test_book_number(self, bf):
        b = fic_book(title="Grizzlies Hockey Book 2")
        assert len(bf.apply([b])) == 0

    def test_romance(self, bf):
        b = fic_book(title="A Dark Romance Story")
        assert len(bf.apply([b])) == 0

    def test_trilogy(self, bf):
        b = fic_book(title="The Dragon's Trilogy")
        assert len(bf.apply([b])) == 0

    def test_saga(self, bf):
        b = fic_book(title="The Green Bone Saga")
        assert len(bf.apply([b])) == 0

    def test_litrpg(self, bf):
        b = fic_book(title="Dungeon Crawler LitRPG")
        assert len(bf.apply([b])) == 0

    def test_immortal(self, bf):
        b = fic_book(title="The Immortal's Lie")
        assert len(bf.apply([b])) == 0

    def test_exorcist(self, bf):
        b = fic_book(title="The Exorcist's House")
        assert len(bf.apply([b])) == 0

    def test_vampire_plural(self, bf):
        b = fic_book(title="Vampires of the North")
        assert len(bf.apply([b])) == 0

    def test_author_also_checked(self, bf):
        # Even if title is clean, fiction keyword in author name excludes it
        b = fic_book(title="The Quiet Place", author="Vampire Writer")
        assert len(bf.apply([b])) == 0


# ─── Real non-fiction must pass ─────────────────────────────────────

class TestNonFictionPasses:
    def test_six_easy_pieces(self, bf):
        assert len(bf.apply([nf_book()])) == 1

    def test_penguin_history(self, bf):
        b = nf_book(title="The Penguin History of the World")
        assert len(bf.apply([b])) == 1

    def test_if_you_tell(self, bf):
        b = nf_book(title="If You Tell: A True Story of Murder")
        assert len(bf.apply([b])) == 1

    def test_sapiens(self, bf):
        b = nf_book(title="Sapiens: A Brief History of Humankind")
        assert len(bf.apply([b])) == 1

    def test_brief_history_of_time(self, bf):
        b = nf_book(title="A Brief History of Time")
        assert len(bf.apply([b])) == 1

    def test_thinking_fast_slow(self, bf):
        b = nf_book(title="Thinking, Fast and Slow")
        assert len(bf.apply([b])) == 1

    def test_atomic_habits(self, bf):
        b = nf_book(title="Atomic Habits")
        assert len(bf.apply([b])) == 1

    def test_children_of_ash_elm(self, bf):
        b = nf_book(title="Children of Ash and Elm: A History of the Vikings")
        assert len(bf.apply([b])) == 1


# ─── Mixed batch ────────────────────────────────────────────────────

class TestMixedBatch:
    def test_fiction_removed_from_batch(self, bf):
        batch = [
            nf_book(),
            fic_book(),                                   # "A Novel"
            nf_book(asin="B0T3", title="The Wild Blue: The Men and Boys"),
            fic_book(asin="B0T4", title="Dark Olympus Book 1"),
            nf_book(asin="B0T5", title="What I Ate in One Year"),
        ]
        result = bf.apply(batch)
        titles = [b["title"] for b in result]
        assert len(result) == 3
        assert not any("Novel" in t for t in titles)
        assert not any("Book 1" in t for t in titles)

    def test_all_fiction_removed(self, bf):
        batch = [
            fic_book(),
            fic_book(asin="B0T2", title="The Rage of Dragons"),
            fic_book(asin="B0T3", title="Dungeon Crawler Carl Book 4"),
        ]
        assert len(bf.apply(batch)) == 0


# ─── Author extraction on deal cards (regression: B004OVEYNU) ───────

class TestAuthorExtraction:
    def test_author_row_used_when_title_contains_by(self):
        """Six Easy Pieces: '...Explained by Its Most Brilliant Teacher by
        Richard P. Feynman...' — the full-card regex used to grab the 'by'
        inside the title, mangling the author. The author row must be used."""
        from sources.amazon import AmazonDealsScraper
        from bs4 import BeautifulSoup

        html = '''
        <div data-asin="B004OVEYNU">
          <h2><a><span>Six Easy Pieces: Essentials of Physics Explained by Its Most Brilliant Teacher</span></a></h2>
          <div class="a-row a-color-secondary">by Richard P. Feynman , Robert B. Leighton , et al. | Sold by: Hachette Book Group | Mar 22, 2011</div>
          <span class="a-offscreen">$1.99</span>
        </div>
        '''
        cfg = {"sources": {"amazon": {"base_url": "https://www.amazon.com", "deals_x": "/x"}},
               "scraping": {"max_books_per_source": 50}}
        scraper = AmazonDealsScraper(cfg)
        books = scraper.parse_deals_page(BeautifulSoup(html, "lxml"))
        assert len(books) == 1
        assert books[0]["asin"] == "B004OVEYNU"
        assert books[0]["author"] == "Richard P. Feynman , Robert B. Leighton , et al."
        assert "Its Most Brilliant Teacher" not in books[0]["author"]

    def test_fallback_regex_still_works_without_author_row(self):
        from sources.amazon import AmazonDealsScraper
        from bs4 import BeautifulSoup

        html = '''
        <div data-asin="B0TESTAA1">
          <h2><a><span>Some Book</span></a></h2>
          by Jane Doe | Sold by: Amazon.com Services LLC
          <span class="a-offscreen">$2.99</span>
        </div>
        '''
        cfg = {"sources": {"amazon": {"base_url": "https://www.amazon.com", "deals_x": "/x"}},
               "scraping": {"max_books_per_source": 50}}
        scraper = AmazonDealsScraper(cfg)
        books = scraper.parse_deals_page(BeautifulSoup(html, "lxml"))
        assert len(books) == 1
        assert books[0]["author"] == "Jane Doe"
        assert books[0]["price"] == 2.99


# ─── Storage metadata refresh (regression: stale mangled author) ────

class TestStorageRefresh:
    def test_mark_seen_refreshes_title_author_url(self, tmp_path):
        """A previously-seen ASIN with a mangled author must be corrected on
        the next run (parser fixes propagate into seen_books.json)."""
        from storage import Storage

        st = Storage(tmp_path)
        st.mark_seen("B004OVEYNU", "Old title", 1.99, "Its Most Brilliant Teacher by Richard P. Feynman", "http://old")
        st.mark_seen("B004OVEYNU", "Six Easy Pieces: Essentials of Physics Explained by Its Most Brilliant Teacher", 1.99,
                     "Richard P. Feynman , Robert B. Leighton , et al.", "https://www.amazon.com/dp/B004OVEYNU")

        seen = st._read_seen()
        entry = seen["B004OVEYNU"]
        assert entry["title"] == "Six Easy Pieces: Essentials of Physics Explained by Its Most Brilliant Teacher"
        assert entry["author"] == "Richard P. Feynman , Robert B. Leighton , et al."
        assert entry["url"] == "https://www.amazon.com/dp/B004OVEYNU"
        assert entry["lowest_price"] == 1.99

    def test_mark_seen_keeps_lowest_price(self, tmp_path):
        from storage import Storage

        st = Storage(tmp_path)
        st.mark_seen("B0X", "Book", 0.99)
        st.mark_seen("B0X", "Book", 4.99)
        assert st._read_seen()["B0X"]["lowest_price"] == 0.99


# ─── Availability check (spike t_e934a2a3 §6b) ─────────────────────

def _pp_soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")


def _nf_scraper():
    from sources.amazon import AmazonDealsScraper
    cfg = {
        "sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "deals_x": "/x",
        }},
        "scraping": {"max_books_per_source": 50},
    }
    return AmazonDealsScraper(cfg)


KINDLE_AVAILABLE = '''<div id="tmmSwatches"><div class="swatchElement selected" id="tmm-grid-swatch-KINDLE">
<span class="slot-title">Kindle</span><span class="slot-price">$1.99</span>
<span class="a-size-small a-color-secondary">Available instantly</span></div>
<div class="swatchElement" id="tmm-grid-swatch-HARDCOVER">Hardcover $11.71</div></div>'''

KINDLE_UNAVAILABLE = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
<span>Kindle</span><span>$9.99</span>
<span class="a-size-small a-color-secondary">Currently unavailable</span></div></div>'''

KINDLE_UNAVAILABLE_PHRASE = '''<div id="formats"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
Kindle $7.99 This title is not currently available for purchase</div></div>'''

KINDLE_PREORDER = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
Kindle $14.99 This title will be released on November 1, 2026</div></div>'''

BUYBOX_AVAILABLE = '''<div id="buybox"><input id="one-click-button" type="submit" value="Buy now with 1-Click"/>
<span id="checkoutButtonId-announce"> Buy now with 1-Click </span></div>'''

BUYBOX_PREORDER = '''<div id="buybox"><input id="one-click-button" type="submit" value="Pre-order with 1-Click"/>
<span>This title will be released on January 5, 2027</span></div>'''

NO_KINDLE_ROW = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-AUDIO_DOWNLOAD">
Audiobook $0.00</div><div class="swatchElement" id="tmm-grid-swatch-PAPERBACK">Paperback $8.94</div></div>'''


class TestAvailability:
    def test_kindle_row_available(self):
        info = _nf_scraper().parse_product_page(_pp_soup(KINDLE_AVAILABLE))
        assert info.get("available") is True
        assert info.get("preorder", False) is False

    def test_kindle_row_currently_unavailable(self):
        info = _nf_scraper().parse_product_page(_pp_soup(KINDLE_UNAVAILABLE))
        assert info.get("available") is False

    def test_kindle_row_unavailable_phrase(self):
        info = _nf_scraper().parse_product_page(_pp_soup(KINDLE_UNAVAILABLE_PHRASE))
        assert info.get("available") is False

    def test_preorder_release_date(self):
        info = _nf_scraper().parse_product_page(_pp_soup(KINDLE_PREORDER))
        assert info.get("preorder") is True
        assert info.get("available") is False

    def test_buybox_fallback_available(self):
        info = _nf_scraper().parse_product_page(_pp_soup(BUYBOX_AVAILABLE))
        assert info.get("available") is True

    def test_buybox_preorder(self):
        info = _nf_scraper().parse_product_page(_pp_soup(BUYBOX_PREORDER))
        assert info.get("preorder") is True
        assert info.get("available") is False

    def test_no_kindle_row_unknown(self):
        # No Kindle row / no buybox signal → availability not asserted (kept)
        info = _nf_scraper().parse_product_page(_pp_soup(NO_KINDLE_ROW))
        assert "available" not in info
        assert info.get("preorder", False) is False

    def test_none_soup(self):
        assert _nf_scraper().parse_product_page(None) == {}


class TestReportable:
    """scraper.is_reportable — the availability gate applied after enrichment."""

    def test_available_kept(self):
        assert is_reportable({"available": True}) is True

    def test_unavailable_dropped(self):
        assert is_reportable({"available": False}) is False

    def test_preorder_dropped(self):
        assert is_reportable({"preorder": True}) is False

    def test_preorder_also_unavailable_dropped(self):
        assert is_reportable({"available": False, "preorder": True}) is False

    def test_unknown_kept(self):
        # No availability signal (e.g. fetch failed) → keep, no regression
        assert is_reportable({}) is True

    def test_available_none_kept(self):
        assert is_reportable({"available": None}) is True


# ─── Lightpanda 503 rate-limit retry (regression: t_db840b83) ────────

def _fake_subprocess_run(monkeypatch, responses):
    """Patch subprocess.run with a fake returning responses in order (last repeats)."""
    import subprocess as _sp
    state = {"n": 0}

    def fake_run(cmd, capture_output=True, text=True, timeout=300):
        i = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return _sp.CompletedProcess([], 0, stdout=responses[i], stderr="")

    monkeypatch.setattr("sources.lightpanda_fetcher.subprocess.run", fake_run)
    return state


def _lp_result(url, status, content):
    return json.dumps({"results": [{"url": url, "http_status": status, "content": content}]})


def _lp_fetcher(tmp_path):
    cfg = {"scraping": {
        "lightpanda_cookies": str(tmp_path / "cookies.json"),
        "lightpanda_retries": 3,
        "lightpanda_retry_backoff": [0, 0],
    }}
    return LightpandaFetcher(cfg)


class _FakeFetcher:
    """Minimal primary/fallback stand-in with fetch_all + last_failures."""

    def __init__(self, results, failures=None):
        self._results = results
        self.last_failures = failures or {}
        self.calls = 0
        self.last_urls: list[str] = []

    def fetch_all(self, urls):
        self.calls += 1
        self.last_urls = list(urls)
        return dict(self._results)


class TestLightpanda503Retry:
    """Regression: Amazon 503 rate-limit must retry with backoff, and the
    FallbackFetcher must only fall back after retries are exhausted."""

    def test_503_then_200_retries_and_succeeds(self, tmp_path, monkeypatch):
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 503, "<html><body>Sorry! Something went wrong.</body></html>"),
            _lp_result(url, 200, "<html><div data-asin='B0TEST00001'>book</div></html>"),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 2          # exactly one retry
        assert out[url] is not None
        assert f.last_failures == {}    # recovered → no failure recorded

    def test_503_exhaustion_returns_none_and_records_failure(self, tmp_path, monkeypatch, capsys):
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 503, "Sorry! Something went wrong."),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 3          # all retries exhausted
        assert out[url] is None
        assert f.last_failures[url] == "HTTP 503"
        captured = capsys.readouterr()
        assert "failed 1 URL(s) after 3 attempt(s)" in captured.out
        assert "HTTP 503" in captured.out

    def test_error_page_content_detected_even_with_status_200(self, tmp_path, monkeypatch):
        """Lightpanda sometimes reports the CloudFront 503 page with
        http_status 200 — the body signature must still trigger a retry."""
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, "<html><body>Sorry! Something went wrong.<br/>Request ID: abc123</body></html>"),
            _lp_result(url, 200, "<html><div data-asin='B0TEST00001'>book</div></html>"),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 2
        assert out[url] is not None

    def test_real_page_with_status_200_is_not_retried(self, tmp_path, monkeypatch):
        url = "https://www.amazon.com/dp/B0TEST00001"
        big_html = "<html><body>" + ("<div data-asin='B%d'>book</div>" * 200) + "</body></html>"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, big_html),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 1          # no pointless retry
        assert out[url] is not None

    def test_fallback_only_after_exhaustion(self, config, capsys):
        from sources.fallback_fetcher import FallbackFetcher
        from bs4 import BeautifulSoup
        url = "https://www.amazon.com/dp/B0TEST00001"
        primary = _FakeFetcher({url: None}, failures={url: "HTTP 503"})
        fallback = _FakeFetcher({url: BeautifulSoup("<html><div data-asin='X'>b</div></html>", "lxml")})
        ff = FallbackFetcher(primary, fallback, config)
        out = ff.fetch_all([url])
        assert fallback.calls == 1
        assert out[url] is not None
        captured = capsys.readouterr()
        assert "1 URL(s) failed" in captured.out
        assert "503" in captured.out

    def test_no_fallback_on_partial_success(self, config, capsys):
        from sources.fallback_fetcher import FallbackFetcher
        from bs4 import BeautifulSoup
        u1 = "https://www.amazon.com/dp/B0TEST00001"
        u2 = "https://www.amazon.com/dp/B0TEST00002"
        soup = BeautifulSoup("<html><body>ok</body></html>", "lxml")
        primary = _FakeFetcher({u1: soup, u2: None}, failures={u2: "HTTP 503"})
        fallback = _FakeFetcher({u1: soup, u2: soup})
        ff = FallbackFetcher(primary, fallback, config)
        out = ff.fetch_all([u1, u2])
        # Partial failure → fallback runs ONLY for the failed URL, good
        # results are kept, and the 503 is logged.
        assert fallback.calls == 1
        assert fallback.last_urls == [u2]   # only the failed URL is retried
        assert out[u1] is not None and out[u2] is not None
        captured = capsys.readouterr()
        assert "partial failure: 1/2" in captured.out
        assert "HTTP 503" in captured.out
