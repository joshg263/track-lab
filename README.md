# track-lab

AI-collaborative race-track design lab (exploration)

## Spike 1: Terrain + Centreline

This spike fetches real elevation data for the **Red Bull Ring** (Spielberg, Austria) and drapes the circuit centreline over it, displaying both in an interactive 3D viewer.

### Quick Start

```bash
# 1. Install dependencies
make setup

# 2. Fetch DEM + analyze + export viewer data
make all

# 3. View in browser
make serve
# Open http://localhost:8000
```

That's it! The viewer shows the track in its valley with orbit/zoom controls and adjustable vertical exaggeration.

### What's Included

- **Centreline GeoJSON** (`data/centrelines/red_bull_ring.geojson`) — editable track centerline
- **3D Viewer** — Three.js terrain mesh with draped track, corner markers, and stats panel
- **CLI Analysis** — `make stats` prints length, elevation, gradients, corner radii

### Data Sources & Attribution

| Data | Source | License |
|------|--------|---------|
| Elevation | [Copernicus DEM GLO-30](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model) via [AWS Open Data](https://registry.opendata.aws/copernicus-dem/) | Free & open (Copernicus) |
| Centreline | [bacinger/f1-circuits](https://github.com/bacinger/f1-circuits) | MIT |
| Original geometry | [OpenStreetMap](https://www.openstreetmap.org) | ODbL |

**No accounts, API keys, or payments required** — all data is freely downloadable from public sources.

### Swapping in a Different Centreline

1. Create a GeoJSON file with a `LineString` geometry in `[longitude, latitude]` order (WGS84)
2. Save it to `data/centrelines/your_track.geojson`
3. Run:
   ```bash
   python3 scripts/fetch_dem.py data/centrelines/your_track.geojson
   python3 scripts/analyze_track.py data/centrelines/your_track.geojson cache/dem/*.tif -o data/analysis.json
   python3 scripts/export_viewer_data.py --analysis data/analysis.json --dem cache/dem/*.tif --output-dir viewer/data
   make serve
   ```

### Calibration: Red Bull Ring

Computed values vs published circuit data:

| Metric | Computed | Official | Source |
|--------|----------|----------|--------|
| Length | 4,306 m | 4,326 m | [Wikipedia](https://en.wikipedia.org/wiki/Red_Bull_Ring) |
| Elevation range | 63 m | ~65 m (est.) | Circuit profiles |
| Max gradient | 12.6% | 12% | [redbullring.com](https://www.redbullring.com/en/) (T2 area) |

**Why the differences?**
- **Length (20m / 0.5% short):** OSM centreline follows approximate track center, not the official racing line measured at 1m from edge. Also, our centreline has 81 vertices interpolated to ~10m spacing — official measurements may use different reference points.
- **Gradients:** Published "12% at T2" matches our 12.6% max uphill within DEM resolution (~30m horizontal, ~1m vertical accuracy).
- **Corner radii:** Computed from curvature of the smoothed centreline. Tightest corner shows R≈15m which is plausible for T3 (the slowest corner).

### How Noise is Handled

1. **Elevation smoothing:** Gaussian filter (σ=3 points) on sampled DEM values removes single-point spikes
2. **Gradient window:** 100m rolling window for slope calculation prevents spurious peaks from DEM pixelation
3. **Curvature smoothing:** Path coordinates are Gaussian-filtered before curvature computation
4. **Point interpolation:** Original 81-point centreline interpolated to ~430 points (~10m spacing) for stable curvature

### Make Targets

```
make setup       # Install Python dependencies
make fetch-dem   # Download DEM tiles (~40 MB)
make analyze     # Analyze track profile
make viewer-data # Export data for 3D viewer
make serve       # Start local server at http://localhost:8000
make stats       # Print track statistics (CLI)
make verify      # Run offline verification tests
make clean       # Remove generated files (keeps cache)
```

### Directory Structure

```
track-lab/
├── data/
│   ├── centrelines/      # GeoJSON track centerlines (committed)
│   └── analysis.json     # Generated analysis output
├── cache/                # Downloaded DEM tiles (gitignored)
├── viewer/
│   ├── index.html        # 3D viewer (Three.js)
│   └── data/             # Generated terrain/track JSON (gitignored)
├── scripts/
│   ├── fetch_dem.py      # Download Copernicus DEM tiles
│   ├── analyze_track.py  # Compute track statistics
│   ├── export_viewer_data.py
│   ├── serve.py          # Simple HTTP server
│   └── verify.py         # Offline verification tests
├── Makefile
└── requirements.txt
```

### Requirements

- Python 3.9+
- ~50 MB disk for DEM cache (one 1°×1° tile)
- Modern browser with WebGL for viewer

---

*Part of an exploration into AI-collaborative race-track design tools.*
