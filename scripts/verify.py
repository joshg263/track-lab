#!/usr/bin/env python3
"""
Verification script for track-lab.

Runs offline-safe checks using a committed DEM fixture:
1. GeoJSON structure validity
2. Real analysis pipeline on fixture DEM
3. Calibration against published Red Bull Ring figures

The fixture DEM (data/fixtures/red_bull_ring_dem.tif) is a cropped region
of the full Copernicus tile, small enough to commit (~30KB).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent

# Official figures from FIA Media Kit for 2026 Austrian Grand Prix
# Source: https://www.fia.com/sites/default/files/media_kit_-_2026_austrian_grand_prix.pdf
PUBLISHED_RED_BULL_RING = {
    "length_m": 4326,
    "length_tolerance_m": 150,  # Accept ±150m (centreline vs racing line)
    "elevation_change_m": 65,   # "Change in elevation: 65 m"
    "elevation_tolerance_m": 10,
    "max_uphill_pct": 12.0,     # "Maximum uphill slope: 12 %"
    "max_downhill_pct": 9.3,    # "Maximum downhill slope: 9.3 %"
    "gradient_tolerance_pct": 3.0,
    "altitude_m": 677,          # "Sea level: 677 m"
    "source": "FIA Official Media Kit, 2026 Austrian Grand Prix",
    "source_url": "https://www.fia.com/sites/default/files/media_kit_-_2026_austrian_grand_prix.pdf"
}


def test_geojson_valid():
    """Check GeoJSON centreline is valid."""
    path = PROJECT_DIR / "data" / "centrelines" / "red_bull_ring.geojson"
    assert path.exists(), f"Centreline not found: {path}"
    
    with open(path) as f:
        gj = json.load(f)
    
    assert gj.get("type") == "FeatureCollection", "Not a FeatureCollection"
    assert len(gj.get("features", [])) > 0, "No features"
    
    feature = gj["features"][0]
    assert feature["geometry"]["type"] == "LineString", "Not a LineString"
    
    coords = feature["geometry"]["coordinates"]
    assert len(coords) > 10, f"Too few points: {len(coords)}"
    
    for i, coord in enumerate(coords):
        assert len(coord) >= 2, f"Invalid coord at {i}: {coord}"
        lon, lat = coord[0], coord[1]
        assert -180 <= lon <= 180, f"Invalid lon at {i}: {lon}"
        assert -90 <= lat <= 90, f"Invalid lat at {i}: {lat}"
    
    # Check it's roughly in Austria (Red Bull Ring area)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    assert 14.5 < min(lons) < 15, f"Longitude out of range for Austria: {min(lons)}"
    assert 47 < min(lats) < 48, f"Latitude out of range for Austria: {min(lats)}"
    
    print(f"✓ GeoJSON valid: {len(coords)} points in Austria region")
    return True


def test_fixture_exists():
    """Check DEM fixture exists."""
    fixture_path = PROJECT_DIR / "data" / "fixtures" / "red_bull_ring_dem.tif"
    assert fixture_path.exists(), f"DEM fixture not found: {fixture_path}"
    
    size_kb = fixture_path.stat().st_size / 1024
    print(f"✓ DEM fixture exists: {size_kb:.1f} KB")
    return fixture_path


def run_analysis(fixture_dem: Path) -> dict:
    """Run the actual analysis pipeline on the fixture DEM."""
    centreline = PROJECT_DIR / "data" / "centrelines" / "red_bull_ring.geojson"
    output = PROJECT_DIR / "data" / "fixtures" / "analysis_test.json"
    
    print("  Running analysis pipeline on fixture DEM...")
    result = subprocess.run([
        sys.executable,
        str(SCRIPT_DIR / "analyze_track.py"),
        str(centreline),
        str(fixture_dem),
        "-o", str(output),
        "--smooth", "3"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"Analysis failed: {result.returncode}")
    
    with open(output) as f:
        data = json.load(f)
    
    # Clean up test output
    output.unlink(missing_ok=True)
    
    required = [
        "length_m", "min_elevation_m", "max_elevation_m", 
        "elevation_range_m", "total_elevation_change_m",
        "max_uphill_gradient_pct", "max_downhill_gradient_pct",
        "corners"
    ]
    
    for field in required:
        assert field in data, f"Missing field: {field}"
    
    print(f"✓ Analysis completed: {data['num_points']} points analyzed")
    return data


def test_calibration(analysis: dict) -> bool:
    """Compare computed values against published figures."""
    pub = PUBLISHED_RED_BULL_RING
    all_ok = True
    
    print(f"\nSource: {pub['source']}")
    print(f"URL: {pub['source_url']}\n")
    
    # Length check
    length_diff = abs(analysis["length_m"] - pub["length_m"])
    length_ok = length_diff <= pub["length_tolerance_m"]
    status = "✓" if length_ok else "✗"
    print(f"{status} Length: {analysis['length_m']:.0f}m (official: {pub['length_m']}m, diff: {length_diff:.0f}m)")
    if not length_ok:
        all_ok = False
    
    # Elevation range check
    elev_range = analysis["elevation_range_m"]
    elev_diff = abs(elev_range - pub["elevation_change_m"])
    elev_ok = elev_diff <= pub["elevation_tolerance_m"]
    status = "✓" if elev_ok else "✗"
    print(f"{status} Elevation change: {elev_range:.0f}m (official: {pub['elevation_change_m']}m, diff: {elev_diff:.0f}m)")
    if not elev_ok:
        all_ok = False
    
    # Altitude check (min elevation should be near sea level figure)
    min_elev = analysis["min_elevation_m"]
    altitude_diff = abs(min_elev - pub["altitude_m"])
    altitude_ok = altitude_diff <= 20  # Within 20m of official altitude
    status = "✓" if altitude_ok else "✗"
    print(f"{status} Min altitude: {min_elev:.0f}m (official base: {pub['altitude_m']}m, diff: {altitude_diff:.0f}m)")
    if not altitude_ok:
        all_ok = False
    
    # Max uphill gradient check
    max_up = analysis["max_uphill_gradient_pct"]
    up_diff = abs(max_up - pub["max_uphill_pct"])
    up_ok = up_diff <= pub["gradient_tolerance_pct"]
    status = "✓" if up_ok else "✗"
    print(f"{status} Max uphill: {max_up:.1f}% (official: {pub['max_uphill_pct']}%, diff: {up_diff:.1f}%)")
    if not up_ok:
        all_ok = False
    
    # Max downhill gradient check
    max_down = abs(analysis["max_downhill_gradient_pct"])
    down_diff = abs(max_down - pub["max_downhill_pct"])
    down_ok = down_diff <= pub["gradient_tolerance_pct"]
    status = "✓" if down_ok else "✗"
    print(f"{status} Max downhill: {max_down:.1f}% (official: {pub['max_downhill_pct']}%, diff: {down_diff:.1f}%)")
    if not down_ok:
        all_ok = False
    
    # Corner count sanity (not an official figure, just plausibility)
    corners = analysis.get("corners", [])
    corner_ok = len(corners) >= 5
    status = "✓" if corner_ok else "!"
    print(f"{status} Corners detected: {len(corners)} (sanity check: ≥5)")
    
    return all_ok


def test_viewer_data():
    """Check viewer data files if they exist."""
    terrain_path = PROJECT_DIR / "viewer" / "data" / "terrain.json"
    track_path = PROJECT_DIR / "viewer" / "data" / "track.json"
    
    if not terrain_path.exists() or not track_path.exists():
        print("- Viewer data not generated (run 'make viewer-data')")
        return True
    
    with open(terrain_path) as f:
        terrain = json.load(f)
    
    assert "heights" in terrain, "Terrain missing heights"
    assert len(terrain["heights"]) > 10, "Terrain grid too small"
    
    with open(track_path) as f:
        track = json.load(f)
    
    assert "points" in track, "Track missing points"
    assert len(track["points"]) > 10, "Track has too few points"
    
    print(f"✓ Viewer data valid: terrain {terrain['grid']['rows']}x{terrain['grid']['cols']}, track {len(track['points'])} points")
    return True


def main():
    print("=" * 60)
    print("TRACK-LAB VERIFICATION")
    print("=" * 60)
    print()
    
    print("1. GeoJSON Centreline")
    print("-" * 40)
    test_geojson_valid()
    print()
    
    print("2. DEM Fixture")
    print("-" * 40)
    fixture_dem = test_fixture_exists()
    print()
    
    print("3. Analysis Pipeline (on fixture)")
    print("-" * 40)
    analysis = run_analysis(fixture_dem)
    print()
    
    print("4. Calibration vs Official Data")
    print("-" * 40)
    calibration_ok = test_calibration(analysis)
    print()
    
    print("5. Viewer Data (optional)")
    print("-" * 40)
    test_viewer_data()
    print()
    
    print("=" * 60)
    if calibration_ok:
        print("VERIFICATION PASSED")
        return 0
    else:
        print("VERIFICATION FAILED")
        print("Computed values outside tolerance of official figures.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
