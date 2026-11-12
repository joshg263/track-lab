#!/usr/bin/env bash
# Headless screenshot of the viewer (needs `make serve` running).
# Usage: scripts/screenshot.sh <out.png> [query-string]
#   e.g. scripts/screenshot.sh docs/after_eyeview.png "seat=main_grandstand&view=eye"
set -euo pipefail
OUT="$1"
QUERY="${2:-}"
CHROME="${CHROME:-google-chrome}"
timeout 90 "$CHROME" --headless=new --no-sandbox --hide-scrollbars \
    --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist \
    --window-size=1280,720 --virtual-time-budget=25000 \
    --screenshot="$OUT" "http://localhost:${PORT:-8000}/index.html?${QUERY}" 2>/dev/null || true
test -s "$OUT" && echo "Saved $OUT"
