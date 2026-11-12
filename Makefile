# Track Lab - Red Bull Ring terrain spike
# 
# Usage:
#   make setup       - Install Python dependencies
#   make fetch-dem   - Download DEM tiles for the track area
#   make analyze     - Analyze track profile over DEM
#   make viewer-data - Export data for 3D viewer
#   make serve       - Start local viewer server
#   make all         - Run full pipeline (fetch + analyze + viewer-data)
#   make verify      - Run offline verification tests
#   make clean       - Remove generated files

PYTHON ?= python3
PIP ?= pip3

CENTRELINE = data/centrelines/red_bull_ring.geojson
DEM_TILE = cache/dem/Copernicus_DSM_COG_10_N47_00_E014_00_DEM.tif
ANALYSIS_JSON = data/analysis.json
VIEWER_DATA = viewer/data/terrain.json viewer/data/track.json

.PHONY: all setup fetch-dem analyze viewer-data serve verify viewshed clean help

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

$(VIEWER_DATA): $(ANALYSIS_JSON) $(DEM_TILE)
	$(PYTHON) scripts/export_viewer_data.py \
		--analysis $(ANALYSIS_JSON) \
		--dem $(DEM_TILE) \
		--buffer 0.015 \
		--resolution 200 \
		--output-dir viewer/data

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
			--analysis $(ANALYSIS_JSON); \
	fi

clean:
	rm -f $(ANALYSIS_JSON)
	rm -rf viewer/data
	@echo "Cleaned generated files (cache preserved)"

clean-all: clean
	rm -rf cache
	@echo "Cleaned everything including cache"
