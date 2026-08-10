#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/kindle-deals-nonfiction"
cd "$PROJECT_DIR"
"$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/scraper.py"
