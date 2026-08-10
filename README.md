# 📚 Kindle Non-Fiction Deals Bot

Daily cron job that scrapes Amazon Kindle deals for **Non-Fiction** books (Science, History, Philosophy) and delivers a formatted report to Telegram every morning at 9:00 AM.

![Python](https://img.shields.io/badge/Python-3.12-blue)

## ✨ Features

- **Multi-source**: Nonfiction deals page + Science, History, Politics Best Sellers
- **Smart filtering**: Price cap ($4.99), topic keywords, fiction exclusion
- **Deduplication**: Tracks seen books, detects price drops
- **Product-page enrichment**: Fetches real buy-box price + list price + savings %
- **US locale**: Forces Amazon.com US store pricing
- **Anti-bot**: `curl_cffi` with Chrome 124 TLS fingerprint impersonation
- **Telegram-ready**: MarkdownV2 formatted with 📕 title, 💰 price, 🔗 link per book
- **Zero LLM cost**: `no_agent=true` cron job

## 📊 Sample Report

```
📚 Kindle Non-Fiction Deals — 2026-08-10 08:30 UTC

5 deals found | 🆕 5 new | 📉 0 price drops

📖 All Deals

📕 Six Easy Pieces — Richard Feynman
💰 $1.99 ~~$12.99~~ (85% off)  🔗 Link

📕 Children of Ash and Elm: A History of the Vikings
💰 $1.99 ~~$14.99~~ (87% off)  🔗 Link
```

## 🚀 Quick Start

```bash
cd ~/kindle-deals-nonfiction
source venv/bin/activate
pip install -r requirements.txt
./run.sh
```

## 🏗️ Architecture

Same as [kindle-deals-bot](https://github.com/neagastefan99/kindle-deals-bot) with non-fiction specific config and negative keyword filtering to exclude fiction.

## 🔧 Configuration

Edit `config.yaml` — topics and exclude keywords control what appears.
