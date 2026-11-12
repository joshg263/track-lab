#!/bin/bash
# Take screenshots of Japan sites for documentation
# Requires: make serve running, Chrome/Chromium available

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DOCS_DIR="$PROJECT_DIR/docs"

# Check if server is running
if ! curl -s http://localhost:8000 > /dev/null 2>&1; then
    echo "Error: Local server not running. Start with 'make serve'"
    exit 1
fi

# Find Chrome
CHROME=""
for cmd in google-chrome chromium chromium-browser chrome; do
    if command -v "$cmd" &> /dev/null; then
        CHROME="$cmd"
        break
    fi
done

if [ -z "$CHROME" ]; then
    echo "Error: Chrome/Chromium not found"
    exit 1
fi

echo "Using browser: $CHROME"

# Sites to screenshot
SITES=("yamanashi_basin" "hitoyoshi_basin" "iwaki_basin" "sado_highland")

for site in "${SITES[@]}"; do
    echo "Capturing $site..."
    
    # Overview screenshot
    "$CHROME" --headless --disable-gpu --window-size=1280,720 \
        --screenshot="$DOCS_DIR/site_${site}_overview.png" \
        "http://localhost:8000/viewer/site.html?site=$site&capture=1" \
        2>/dev/null || true
    
    sleep 2
    
    # Viewpoint screenshot (would need JS to click the button, skip for now)
    
    if [ -f "$DOCS_DIR/site_${site}_overview.png" ]; then
        echo "  Saved: docs/site_${site}_overview.png"
    else
        echo "  Warning: Screenshot failed for $site"
    fi
done

echo ""
echo "Screenshots complete!"
