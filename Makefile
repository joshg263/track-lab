# Track Lab - Red Bull Ring terrain spike
# 
# Usage:
#   make setup       - Install Python dependencies
#   make fetch-dem   - Download DEM tiles for the track area
#   make analyze     - Analyze track profile over DEM
#   make viewer-data - Export data for 3D viewer (terrain, track, obstructions, imagery)
#   make fetch-obstructions - Refresh the committed OSM obstruction fixture (network)
#   make fetch-imagery      - Re-bake the committed Sentinel-2 cloudless texture (network)
#   make serve       - Start local viewer server
#   make all         - Run full pipeline (fetch + analyze + viewer-data)
#   make verify      - Run offline verification tests
#   make clean       - Remove generated files

PYTHON ?= python3
PIP ?= pip3

CENTRELINE = data/centrelines/red_bull_ring.geojson
DEM_TILE = cache/dem/Copernicus_DSM_COG_10_N47_00_E014_00_DEM.tif
ANALYSIS_JSON = data/analysis.json
OBSTRUCTIONS = data/obstructions/red_bull_ring_osm.geojson
IMAGERY = data/imagery/red_bull_ring_s2cloudless.jpg
VIEWER_DATA = viewer/data/track.json

.PHONY: all setup fetch-dem fetch-obstructions fetch-imagery analyze viewer-data serve verify viewshed screenshots clean help

all: fetch-dem analyze viewer-data
	@echo ""
	@echo "Pipeline complete! Run 'make serve' to view."

help:
	@echo "Track Lab - Red Bull Ring terrain spike"
	@echo ""
	@echo "Targets:"
	@echo "  setup       - Install Python dependencies"
	@echo "  fetch-dem   - Download DEM tiles (~25 MB)"
	@echo "  analyze     - Analyze track profile"
	@echo "  viewer-data - Export data for 3D viewer"
	@echo "  fetch-obstructions - Refresh OSM buildings/grandstands/woods fixture"
	@echo "  fetch-imagery      - Re-bake Sentinel-2 cloudless terrain texture"
	@echo "  screenshots - Capture docs/after_*.png (needs 'make serve' running + Chrome)"
	@echo "  serve       - Start local server at http://localhost:8000"
	@echo "  verify      - Run offline verification tests"
	@echo "  viewshed    - Compute viewshed (usage: make viewshed SEAT=lat,lon)"
	@echo "  stats       - Print track statistics (CLI)"
	@echo "  clean       - Remove generated files"

setup:
	$(PIP) install -r requirements.txt

fetch-dem: $(DEM_TILE)

$(DEM_TILE): $(CENTRELINE)
	$(PYTHON) scripts/fetch_dem.py $(CENTRELINE) --buffer 0.02

analyze: $(ANALYSIS_JSON)

$(ANALYSIS_JSON): $(CENTRELINE) $(DEM_TILE)
	$(PYTHON) scripts/analyze_track.py $(CENTRELINE) $(DEM_TILE) -o $(ANALYSIS_JSON)

viewer-data: $(VIEWER_DATA)

$(VIEWER_DATA): $(ANALYSIS_JSON) $(DEM_TILE) $(OBSTRUCTIONS) $(IMAGERY) scripts/surface_model.py scripts/export_viewer_data.py
	$(PYTHON) scripts/export_viewer_data.py \
		--analysis $(ANALYSIS_JSON) \
		--dem $(DEM_TILE) \
		--output-dir viewer/data

fetch-obstructions:
	$(PYTHON) scripts/fetch_obstructions.py $(CENTRELINE) -o $(OBSTRUCTIONS)

fetch-imagery: $(ANALYSIS_JSON)
	$(PYTHON) scripts/fetch_imagery.py --analysis $(ANALYSIS_JSON)

screenshots:
	scripts/screenshot.sh docs/after_overview.png
	scripts/screenshot.sh docs/after_eyeview.png "seat=main_grandstand&view=eye"
	scripts/screenshot.sh docs/after_eyeview_turn1.png "seat=turn1_grandstand&view=eye&yaw=-25&pitch=-12"

serve: viewer-data
	$(PYTHON) scripts/serve.py 8000

stats: $(ANALYSIS_JSON)
	@$(PYTHON) -c "import json; d=json.load(open('$(ANALYSIS_JSON)')); \
		print('Track:', d['centreline_file']); \
		print('Length:', d['length_m'], 'm'); \
		print('Elevation:', d['min_elevation_m'], '-', d['max_elevation_m'], 'm'); \
		print('Range:', d['elevation_range_m'], 'm'); \
		print('Total change:', d['total_elevation_change_m'], 'm'); \
		print('Max uphill:', d['max_uphill_gradient_pct'], '%'); \
		print('Max downhill:', d['max_downhill_gradient_pct'], '%'); \
		print('Tightest corners:'); \
		[print('  R=' + str(c['radius_m']) + 'm @ ' + str(c['position_m']) + 'm') for c in d['corners'][:5]]"

# Verification - uses cached or fixture data, runs offline
verify: 
	@echo "Running verification tests..."
	@$(PYTHON) scripts/verify.py
	@echo ""
	@echo "All checks passed!"

# Viewshed calculation from command line
# Usage: make viewshed SEAT=lat,lon
#   or:  make viewshed SEAT=main_grandstand
viewshed: $(ANALYSIS_JSON)
	@if [ -z "$(SEAT)" ]; then \
		echo "Usage: make viewshed SEAT=lat,lon"; \
		echo "   or: make viewshed SEAT=main_grandstand"; \
		echo ""; \
		$(PYTHON) scripts/compute_viewshed.py --list-presets; \
	else \
		$(PYTHON) scripts/compute_viewshed.py \
			--dem $(DEM_TILE) \
			--centreline $(CENTRELINE) \
			--seat "$(SEAT)" \
			--analysis $(ANALYSIS_JSON) \
			$(if $(NO_OBSTRUCTIONS),--no-obstructions,); \
	fi

clean:
	rm -f $(ANALYSIS_JSON)
	rm -rf viewer/data
	@echo "Cleaned generated files (cache preserved)"

clean-all: clean
	rm -rf cache
	@echo "Cleaned everything including cache"

# ============================================================
# Japan Site Scout (Spike 4)
# ============================================================

.PHONY: site-scout site-scout-data site-scout-verify site-scout-screenshots

# Run the full site scouting pipeline (Kansai region)
site-scout:
	@echo "Running Kansai site scouting pipeline..."
	$(PYTHON) scripts/site_scout/kansai_scan.py
	@echo ""
	@echo "Generating viewer data for selected sites..."
	$(PYTHON) scripts/site_scout/generate_site_data.py
	$(PYTHON) scripts/site_scout/export_viewer_data.py
	@echo ""
	@echo "Site scouting complete! View sites at http://localhost:8000/site.html"

# Generate viewer data for committed sites (offline, no network)
site-scout-data:
	@echo "Exporting viewer data for committed sites..."
	$(PYTHON) scripts/site_scout/export_viewer_data.py
	@echo "Done."

# Verify site scout results
site-scout-verify:
	@echo "Running site scout verification..."
	$(PYTHON) scripts/site_scout/verify_sites.py
	@echo ""
	@echo "All site scout checks passed!"

# Take screenshots of Japan sites (requires 'make serve' running)
site-scout-screenshots:
	@echo "Taking screenshots of Japan sites..."
	scripts/site_screenshot.sh
