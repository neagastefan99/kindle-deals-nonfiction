"""Book filters: price cap, genre keywords, tracked authors."""

import re
from typing import Any


class BookFilter:
    def __init__(self, config: dict[str, Any]):
        fc = config.get("filters", {})
        self.max_price = fc.get("max_price", 4.99)
        self.min_savings_pct = fc.get("min_savings_pct", 50)
        self.genres = [g.lower() for g in fc.get("genres", fc.get("topics", []))]
        self.tracked_authors = [a.lower() for a in fc.get("tracked_authors", [])]
        self.exclude_keywords = [k.lower() for k in fc.get("exclude_keywords", [])]
        self.exclude_patterns = fc.get("exclude_patterns", [])
    
    def matches_price(self, price: float | None) -> bool:
        """Book must have a price and be under the cap."""
        return price is not None and price <= self.max_price

    def matches_discount(self, savings_pct: float | None) -> bool:
        """BookBub limited-time gate: only a REAL discount (>= min_savings_pct
        off list price) counts as a deal — not just a price under the cap."""
        return savings_pct is not None and savings_pct >= self.min_savings_pct
    
    def matches_genre(self, title: str, author: str = "", 
                      description: str = "", from_sff_page: bool = False) -> bool:
        """Check if title/author/description contains genre keywords.
        If the book came from an SFF-specific Amazon page, genre check is lenient
        (Amazon already categorized it as SFF)."""
        # Books from SFF pages are already genre-verified by Amazon
        if from_sff_page:
            return True
        
        text = f"{title} {author} {description}".lower()
        for genre in self.genres:
            # Use word boundaries for short keywords to avoid false matches
            if len(genre.split()) == 1 and len(genre) <= 8:
                if re.search(rf"\b{re.escape(genre)}\b", text):
                    return True
            elif genre in text:
                return True
        return False
    
    def is_tracked_author(self, author: str) -> bool:
        """Check if author is in the tracked list (fuzzy match).
        Used to promote tracked authors in the report without excluding others."""
        if not self.tracked_authors:
            return False
        
        author_lower = author.lower()
        author_tokens = set(author_lower.replace(",", " ").split())
        
        for tracked in self.tracked_authors:
            tracked_tokens = set(tracked.split())
            if tracked_tokens & author_tokens:
                return True
            if tracked in author_lower or author_lower in tracked:
                return True
        return False
    
    def matches_author(self, author: str) -> bool:
        """Always returns True — tracked authors get promoted, not exclusive."""
        return True
    
    def apply(self, books: list[dict[str, Any]], require_discount: bool = False) -> list[dict[str, Any]]:
        """Filter a list of books. Returns only matches.

        `require_discount=True` enforces the BookBub limited-time gate
        (savings_pct >= min_savings_pct). The pre-enrichment pass leaves it
        off because deal pages don't carry savings yet; the post-enrichment
        pass turns it on so only verified real discounts are surfaced.
        """
        results = []
        for book in books:
            price = book.get("price")
            title = book.get("title", "")
            author = book.get("author", "")
            from_sff = book.get("from_sff_page", False)
            
            if not self.matches_price(price):
                continue
            if require_discount and not self.matches_discount(book.get("savings_pct")):
                continue
            if not self.matches_genre(title, author, from_sff_page=from_sff):
                continue
            if not self.matches_author(author):
                continue
            
            # Exclude fiction: check title+author for negative keywords
            text = f"{title} {author}".lower()
            if any(kw in text for kw in self.exclude_keywords):
                continue
            if any(re.search(pat, text) for pat in self.exclude_patterns):
                continue
            
            results.append(book)
        return results
