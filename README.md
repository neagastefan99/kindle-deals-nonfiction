# 📚 Kindle SFF Deals Bot

Daily cron job that scrapes Amazon Kindle deals for **Science Fiction & Fantasy** books and delivers a formatted report to Telegram every morning at 9:00 AM.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## ✨ Features

- **Dual scraping**: API-first approach with HTML DOM fallback
- **Multi-source**: Today's Deals SFF, SFF Monthly Deals, SFF Best Sellers
- **Smart filtering**: Price cap ($4.99), genre keywords, author tracking
- **Deduplication**: Never reports the same book twice; detects price drops
- **Product-page enrichment**: Fetches real apex-pricetopay price + list price + savings %
- **US locale**: Forces Amazon.com US store with proper cookies and headers
- **Anti-bot**: `curl_cffi` with Chrome 124 TLS fingerprint impersonation
- **Telegram-ready**: MarkdownV2 formatted output with links and prices
- **Zero LLM cost**: `no_agent=true` cron job (pure Python script)

## 📊 Sample Report

```
📚 Kindle SFF Deals — 2026-08-09 20:00 UTC

24 deals found | 🆕 24 new | 📉 0 price drops

1. Jade City (The Green Bone Saga Book 1) — Fonda Lee — $1.99 ~~was $19.99~~ (90% off) — [Link]
2. Shards of Earth — Adrian Tchaikovsky — $1.99 ~~was $19.99~~ (90% off) — [Link]
3. Dungeon Crawler Carl — Matt Dinniman — $4.99 ~~was $20.00~~ (75% off) — [Link]
...
```

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Hermes Agent (for cron scheduling)

### Install

```bash
cd ~/kindle-deals-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run manually

```bash
./run.sh
```

### Schedule via Hermes cron

The bot is deployed as a Hermes `no_agent` cron job:
- **Schedule**: Daily at 6:00 UTC (9:00 AM Romania)
- **Script**: `kindle-deals-bot.sh` (wrapper in `~/.hermes/scripts/`)

```bash
# The cron job was created with:
hermes cronjob create \
  --name "Kindle SFF Deals Daily" \
  --schedule "0 6 * * *" \
  --script kindle-deals-bot.sh \
  --no-agent
```

## 🏗️ Architecture

```
kindle-deals-bot/
├── venv/                   # Python virtualenv
├── requirements.txt        # curl_cffi, beautifulsoup4, lxml, pyyaml
├── config.yaml             # Genre filters, price caps, tracked authors
├── scraper.py              # Main orchestrator
├── run.sh                  # Execution wrapper for cron
├── sources/
│   ├── __init__.py
│   ├── base.py             # BaseScraper: curl_cffi HTTP with Chrome 124 impersonation
│   └── amazon.py           # AmazonDealsScraper: dual API+HTML scraping
├── filters.py              # Price, genre keyword, and author filtering
├── formatter.py            # Telegram MarkdownV2 report formatter
├── storage.py              # JSON persistence: seen_books + run_log
└── data/                   # Runtime state (gitignored)
    ├── seen_books.json     # ASIN → {title, lowest_price, first_seen}
    └── run_log.jsonl       # Append-only run statistics
```

## 🔧 Configuration

Edit `config.yaml`:

```yaml
filters:
  max_price: 4.99          # USD
  genres:                  # Title/description keywords
    - fantasy
    - science fiction
    - litrpg
    - space opera
    # ...
  tracked_authors: []      # Add authors to track specifically
  # Example: tracked_authors: ["Brandon Sanderson", "Joe Abercrombie"]

sources:
  amazon:
    base_url: "https://www.amazon.com"
    sff_todays_deals: "/Science-Fiction-Fantasy-Todays-Deals/s?rh=n:668010011,p_n_deal_type:23566064011"
    sff_best_sellers: "/Best-Sellers-Kindle-Store-Science-Fiction-Fantasy/zgbs/digital-text/668010011"
```

## 📝 License

MIT
