#!/usr/bin/env python3
"""
Verification script for track-lab.

Runs offline-safe checks using a committed DEM fixture:
1. GeoJSON structure validity
2. Real analysis pipeline on fixture DEM
3. Calibration against published Red Bull Ring figures
4. Viewshed calculation tests (Spike 2)
5. Ribbon continuity test (Spike 2 v2)
6. Obstructions: OSM buildings, grandstands and canopy lower visibility (Spike 3)
7. JS viewer viewshed agrees with Python on every preset (Spike 3, needs node)

The fixture DEM (data/fixtures/red_bull_ring_dem.tif) is a cropped region
of the full Copernicus tile, small enough to commit (~30KB). Obstructions come
from the committed OSM fixture (data/obstructions/red_bull_ring_osm.geojson).
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
from compute_viewshed import (
    BEHIND_BUILDING_SEAT, IN_WOOD_SEAT, compute_viewshed, check_start_finish_visible,
    find_visible_corners, preset_seats,
)
from surface_model import DEFAULT_OBSTRUCTIONS, LOS_SAMPLES, build_obstructions

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
    
    # Corner count check (Red Bull Ring officially has 10 turns, allow 8-11)
    corners = analysis.get("corners", [])
    corner_count = len(corners)
    corner_ok = 8 <= corner_count <= 11
    status = "✓" if corner_ok else "✗"
    print(f"{status} Corners detected: {corner_count} (official: 10 turns, acceptable: 8-11)")
    if not corner_ok:
        all_ok = False
    
    return all_ok


def test_viewer_data():
    """Check viewer data files if they exist."""
    data = PROJECT_DIR / "viewer" / "data"
    needed = ["terrain.json", "terrain.bin", "track.json", "scene.json", "obstructions.bin", "imagery.jpg"]
    if not all((data / f).exists() for f in needed):
        print("- Viewer data not generated (run 'make viewer-data')")
        return True

    with open(data / "terrain.json") as f:
        terrain = json.load(f)
    n = terrain["rows"] * terrain["cols"]
    assert (data / "terrain.bin").stat().st_size == n * 4, "terrain.bin size mismatch"
    with open(data / "scene.json") as f:
        scene = json.load(f)
    o = scene["obstructions"]
    assert (data / "obstructions.bin").stat().st_size == 3 * o["rows"] * o["cols"], "obstructions.bin size mismatch"
    with open(data / "track.json") as f:
        track = json.load(f)
    assert len(track["points"]) > 10, "Track has too few points"

    total_mb = sum(p.stat().st_size for p in data.iterdir()) / 1024 / 1024
    ok = total_mb < 15
    print(f"{'✓' if ok else '✗'} Viewer data valid: terrain {terrain['rows']}x{terrain['cols']}, "
          f"{len(scene['buildings'])} buildings, {len(scene['grandstands'])} grandstands, "
          f"{len(scene['trees']) // 3} trees, {total_mb:.1f} MB total (budget 15 MB)")
    return ok


def test_viewshed(fixture_dem: Path, analysis: dict, obs) -> bool:
    """
    Test viewshed calculations (Spike 2 verification, now over the surface model).

    Checks:
    a. Main grandstand sees the start/finish straight
    b. Main grandstand scores higher than a poor position (NE corner)
    c. Position behind high ground sees less
    d. Results are deterministic on repeated runs

    Uses the interpolated centreline from analysis (same as viewer) for consistent results.
    """
    coords = analysis["profile"]["coords"]
    corners = analysis.get("corners", [])
    dem = str(fixture_dem)
    all_ok = True

    main_gs = preset_seats(obs)["main_grandstand"]
    main_result = compute_viewshed(dem, coords, main_gs["lon"], main_gs["lat"], main_gs["eye_height_m"], obs)
    main_visibility_pct = main_result["visible_pct"]

    sf_visible = check_start_finish_visible(
        main_result["visibility"], main_result["distances_m"], main_result["total_length_m"]
    )
    status = "✓" if sf_visible else "✗"
    print(f"{status} Main grandstand sees start/finish: {'Yes' if sf_visible else 'No'}")
    all_ok &= sf_visible

    poor_result = compute_viewshed(dem, coords, 14.770, 47.228, 10.0, obs)
    visibility_margin = main_visibility_pct - poor_result["visible_pct"]
    margin_ok = visibility_margin >= 20
    print(f"{'✓' if margin_ok else '✗'} Main grandstand visibility advantage:")
    print(f"    Main grandstand: {main_visibility_pct:.1f}%")
    print(f"    NE corner (poor): {poor_result['visible_pct']:.1f}%")
    print(f"    Margin: {visibility_margin:.1f}% (required: ≥20%)")
    all_ok &= margin_ok

    north_result = compute_viewshed(dem, coords, 14.758, 47.230, 10.0, obs)
    north_margin = main_visibility_pct - north_result["visible_pct"]
    north_ok = north_margin > 0
    print(f"{'✓' if north_ok else '✗'} Main grandstand better than north hills:")
    print(f"    Main grandstand: {main_visibility_pct:.1f}%")
    print(f"    North hills: {north_result['visible_pct']:.1f}%")
    print(f"    Margin: {north_margin:.1f}%")
    all_ok &= north_ok

    if corners:
        corners_vis = find_visible_corners(main_result["visibility"], main_result["distances_m"], corners)
        visible_corners = [c for c in corners_vis if c["visible"]]
        print(f"\n  Main grandstand sees {len(visible_corners)}/{len(corners)} corners")

    return all_ok


def test_obstructions(fixture_dem: Path, analysis: dict, obs) -> bool:
    """Obstructions lower visibility, and the whole pipeline is deterministic."""
    coords = analysis["profile"]["coords"]
    dem = str(fixture_dem)
    all_ok = True

    with open(DEFAULT_OBSTRUCTIONS) as f:
        counts = json.load(f)["metadata"]["counts"]
    print(f"  OSM fixture: {counts}")

    main_gs = preset_seats(obs)["main_grandstand"]
    bare = compute_viewshed(dem, coords, main_gs["lon"], main_gs["lat"], main_gs["eye_height_m"])
    with_obs = compute_viewshed(dem, coords, main_gs["lon"], main_gs["lat"], main_gs["eye_height_m"], obs)
    ok = with_obs["visible_pct"] < bare["visible_pct"]
    print(f"{'✓' if ok else '✗'} Main grandstand (eye {main_gs['eye_height_m']} m = {main_gs['tier_height_m']} m tier + 1.2 m):")
    print(f"    Terrain only:      {bare['visible_pct']:.1f}%")
    print(f"    With obstructions: {with_obs['visible_pct']:.1f}%")
    all_ok &= ok

    for seat in (BEHIND_BUILDING_SEAT, IN_WOOD_SEAT):
        b = compute_viewshed(dem, coords, seat["lon"], seat["lat"], seat["eye_height_m"])
        o = compute_viewshed(dem, coords, seat["lon"], seat["lat"], seat["eye_height_m"], obs)
        ok = o["visible_pct"] < b["visible_pct"]
        print(f"{'✓' if ok else '✗'} {seat['name']}: {b['visible_pct']:.1f}% -> {o['visible_pct']:.1f}% with obstructions")
        all_ok &= ok

    obs2 = build_obstructions(coords)
    raster_same = hashlib.sha256(obs.layers_bytes()).hexdigest() == hashlib.sha256(obs2.layers_bytes()).hexdigest()
    again = compute_viewshed(dem, coords, main_gs["lon"], main_gs["lat"], main_gs["eye_height_m"], obs2)
    seat_again = preset_seats(obs2)["main_grandstand"]
    deterministic = (raster_same and seat_again == main_gs and
                     again["visibility"] == with_obs["visibility"] and
                     again["visible_pct"] == with_obs["visible_pct"])
    print(f"{'✓' if deterministic else '✗'} Results deterministic (surface model rebuilt, seat and viewshed identical): "
          f"{'Yes' if deterministic else 'No'}")
    all_ok &= deterministic
    return all_ok


def test_js_parity(fixture_dem: Path, analysis_path: Path) -> bool:
    """Run viewer/viewshed.js under Node on fixture-exported data; compare with Python."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("SKIP_JS_PARITY"):
            print("! node not found, JS parity check skipped (SKIP_JS_PARITY set)")
            return True
        print("✗ node not found: install Node.js 18+ or set SKIP_JS_PARITY=1")
        return False

    from export_viewer_data import export_all
    with tempfile.TemporaryDirectory() as tmp:
        with contextlib.redirect_stdout(io.StringIO()):
            track = export_all(analysis_path, fixture_dem, tmp, with_scene=False)
        res = subprocess.run([node, str(SCRIPT_DIR / "js_viewshed.mjs"), tmp], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"✗ JS viewshed failed: {res.stderr.strip()}")
        return False
    js = json.loads(res.stdout)

    params_ok = js["params"]["los_samples"] == LOS_SAMPLES
    print(f"{'✓' if params_ok else '✗'} Same line-of-sight sampling: JS {js['params']['los_samples']} "
          f"vs Python {LOS_SAMPLES} samples per ray")

    js_by_id = {s["id"]: s for s in js["seats"]}
    worst = 0.0
    print(f"    {'Preset':32s} {'terrain py/js':>15s} {'+obstr. py/js':>15s}")
    for seat in track["preset_seats"]:
        j = js_by_id[seat["id"]]
        d1 = abs(j["visible_pct_bare"] - seat["visible_pct_bare"])
        d2 = abs(j["visible_pct"] - seat["visible_pct"])
        worst = max(worst, d1, d2)
        print(f"    {seat['name'][:32]:32s} {seat['visible_pct_bare']:6.1f}/{j['visible_pct_bare']:6.1f}"
              f"   {seat['visible_pct']:6.1f}/{j['visible_pct']:6.1f}")
    ok = params_ok and worst <= 2.0
    print(f"{'✓' if ok else '✗'} JS and Python agree on all {len(track['preset_seats'])} presets "
          f"(max diff {worst:.2f} pp, tolerance 2 pp)")
    return ok


def test_ribbon_continuity() -> bool:
    """
    Test that every ribbon vertex has terrain support in the viewer grid.

    The viewer lifts the ribbon a constant RIBBON_OFFSET above the bilinear
    terrain height (inside a group scaled by the exaggeration), so it can only
    clip if a vertex falls outside the terrain grid.
    """
    from surface_model import Terrain
    data = PROJECT_DIR / "viewer" / "data"
    if not (data / "terrain.json").exists() or not (data / "track.json").exists():
        print("- Viewer data not generated, skipping ribbon test")
        return True

    with open(data / "terrain.json") as f:
        meta = json.load(f)
    with open(data / "track.json") as f:
        track = json.load(f)
    heights = np.fromfile(data / meta["file"], dtype="<f4").reshape(meta["rows"], meta["cols"])
    terrain = Terrain(heights, meta["left"], meta["top"], meta["res_x"], meta["res_y"])

    RIBBON_OFFSET = 5
    lons = np.array([p["lon"] for p in track["points"]])
    lats = np.array([p["lat"] for p in track["points"]])
    ground = terrain.sample(lons, lats)
    missing = int(np.isnan(ground).sum())
    all_ok = missing == 0
    for exag in [1.0, 1.5, 2.0]:
        print(f"{'✓' if all_ok else '✗'} Exaggeration {exag}×: ribbon above terrain at all {len(lons)} vertices "
              f"(min margin: {RIBBON_OFFSET * exag:.1f}m)")
    if missing:
        print(f"    WARNING: {missing} vertices outside the terrain grid")
    return all_ok


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
    viewer_ok = test_viewer_data()
    print()

    obs = build_obstructions(analysis["profile"]["coords"])

    print("6. Viewshed Calculation (Spike 2, with obstructions)")
    print("-" * 40)
    viewshed_ok = test_viewshed(fixture_dem, analysis, obs)
    print()

    print("7. Ribbon Continuity (Spike 2 v2)")
    print("-" * 40)
    ribbon_ok = test_ribbon_continuity()
    print()

    print("8. Obstructions: buildings, grandstands, canopy (Spike 3)")
    print("-" * 40)
    obstructions_ok = test_obstructions(fixture_dem, analysis, obs)
    print()

    print("9. JS vs Python viewshed parity (Spike 3)")
    print("-" * 40)
    with tempfile.TemporaryDirectory() as tmp:
        analysis_path = Path(tmp) / "analysis.json"
        with open(analysis_path, "w") as f:
            json.dump(analysis, f)
        parity_ok = test_js_parity(fixture_dem, analysis_path)
    print()

    print("=" * 60)
    checks = {
        "Calibration values outside tolerance": calibration_ok,
        "Viewer data invalid or over budget": viewer_ok,
        "Viewshed tests failed": viewshed_ok,
        "Ribbon continuity test failed": ribbon_ok,
        "Obstruction tests failed": obstructions_ok,
        "JS/Python parity failed": parity_ok,
    }
    if all(checks.values()):
        print("VERIFICATION PASSED")
        return 0
    print("VERIFICATION FAILED")
    for msg, ok in checks.items():
        if not ok:
            print(f"  - {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
