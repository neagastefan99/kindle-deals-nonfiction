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
