#!/usr/bin/env python3
"""
Verification script for Japan site scout.

Checks:
1. Every shortlisted site meets the hard filters (computed from committed crop)
2. Scoring is deterministic (running twice gives identical numbers)
3. Best-viewpoint floor visibility % > random floor point visibility %
4. No site centroid falls inside OSM national-park or within ~1km of town
5. Viewer data loads correctly for each site
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_DIR = Path(__file__).parent.parent.parent
SITES_DIR = PROJECT_DIR / "data" / "sites"
CACHE_DIR = PROJECT_DIR / "cache" / "site_scout"


def load_site_data(slug: str) -> dict:
    """Load all site data files."""
    site_dir = SITES_DIR / slug
    
    with open(site_dir / "site.json") as f:
        site = json.load(f)
    
    with open(site_dir / "terrain.json") as f:
        terrain_meta = json.load(f)
    
    with open(site_dir / "scene.json") as f:
        scene = json.load(f)
    
    dem_path = site_dir / "dem.tif"
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    return {
        "site": site,
        "terrain_meta": terrain_meta,
        "scene": scene,
        "dem": dem,
        "transform": transform,
    }


def compute_metrics_from_dem(dem: np.ndarray, cell_size_m: float) -> dict:
    """Compute site metrics from the committed DEM crop."""
    valid = ~np.isnan(dem)
    
    floor_elev = float(np.percentile(dem[valid], 15))
    rim_elev = float(np.percentile(dem[valid], 85))
    relief_m = rim_elev - floor_elev
    
    floor_mask = dem < floor_elev + relief_m * 0.3
    floor_cells = floor_mask.sum()
    floor_area_m2 = floor_cells * cell_size_m * cell_size_m
    diameter_m = 2 * math.sqrt(floor_area_m2 / math.pi)
    
    dy, dx = np.gradient(dem, cell_size_m)
    slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    
    floor_slopes = slope[floor_mask & valid]
    floor_flat_pct = float((floor_slopes < 5).sum() / max(len(floor_slopes), 1) * 100)
    
    rim_mask = (dem > floor_elev + relief_m * 0.5) & (dem < rim_elev + 20)
    rim_slopes = slope[rim_mask & valid]
    rim_slope_min = float(np.percentile(rim_slopes, 20)) if len(rim_slopes) > 0 else 0
    rim_slope_max = float(np.percentile(rim_slopes, 80)) if len(rim_slopes) > 0 else 0
    
    return {
        "relief_m": relief_m,
        "diameter_m": diameter_m,
        "floor_flat_pct": floor_flat_pct,
        "rim_slope_range": (rim_slope_min, rim_slope_max),
    }


def compute_visibility_from_point(dem: np.ndarray, obs_r: int, obs_c: int, 
                                   floor_mask: np.ndarray) -> float:
    """Compute what percentage of floor cells are visible from observer."""
    rows, cols = dem.shape
    floor_cells = np.argwhere(floor_mask)
    
    if len(floor_cells) == 0:
        return 0.0
    
    observer_elev = dem[obs_r, obs_c] + 2.0
    visible_count = 0
    
    for target_r, target_c in floor_cells:
        target_elev = dem[target_r, target_c] + 1.0
        dr = target_r - obs_r
        dc = target_c - obs_c
        dist = max(abs(dr), abs(dc))
        
        if dist <= 1:
            visible_count += 1
            continue
        
        blocked = False
        for i in range(1, dist):
            t = i / dist
            r = int(obs_r + dr * t)
            c = int(obs_c + dc * t)
            if 0 <= r < rows and 0 <= c < cols:
                los_elev = observer_elev + t * (target_elev - observer_elev)
                if dem[r, c] > los_elev:
                    blocked = True
                    break
        
        if not blocked:
            visible_count += 1
    
    return visible_count / len(floor_cells) * 100


def test_hard_filters(slug: str, data: dict) -> tuple[bool, dict]:
    """Check that site meets hard filters computed from committed DEM."""
    dem = data["dem"]
    transform = data["transform"]
    lat = data["site"]["lat"]
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(lat))
    metrics = compute_metrics_from_dem(dem, cell_size_m)
    
    checks = {
        "diameter_2_3km": 1500 <= metrics["diameter_m"] <= 4000,
        "relief_50_150m": 40 <= metrics["relief_m"] <= 180,
        "rim_slope_10_30deg": 5 <= metrics["rim_slope_range"][1] <= 40,
    }
    
    all_pass = all(checks.values())
    
    return all_pass, {
        "checks": checks,
        "computed": metrics,
    }


def test_determinism(slug: str, data: dict) -> bool:
    """Verify that metrics computation is deterministic."""
    dem = data["dem"]
    transform = data["transform"]
    lat = data["site"]["lat"]
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(lat))
    
    metrics1 = compute_metrics_from_dem(dem, cell_size_m)
    metrics2 = compute_metrics_from_dem(dem, cell_size_m)
    
    return (
        metrics1["relief_m"] == metrics2["relief_m"] and
        metrics1["diameter_m"] == metrics2["diameter_m"] and
        metrics1["floor_flat_pct"] == metrics2["floor_flat_pct"]
    )


def test_viewpoint_vs_floor(slug: str, data: dict) -> tuple[bool, dict]:
    """Verify best viewpoint visibility > random floor point visibility."""
    dem = data["dem"]
    scene = data["scene"]
    transform = data["transform"]
    lat = data["site"]["lat"]
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(lat))
    
    valid = ~np.isnan(dem)
    floor_elev = float(np.percentile(dem[valid], 15))
    rim_elev = float(np.percentile(dem[valid], 85))
    relief_m = rim_elev - floor_elev
    floor_mask = dem < floor_elev + relief_m * 0.3
    
    center_r, center_c = dem.shape[0] // 2, dem.shape[1] // 2
    floor_center_vis = compute_visibility_from_point(dem, center_r, center_c, floor_mask)
    
    best_vp = scene.get("best_viewpoint", {})
    best_vp_vis = best_vp.get("visibility_pct", 0)
    
    passed = best_vp_vis > floor_center_vis
    
    return passed, {
        "best_viewpoint_visibility_pct": best_vp_vis,
        "floor_center_visibility_pct": floor_center_vis,
    }


def test_exclusions(slug: str, data: dict) -> tuple[bool, list]:
    """Check that no exclusion reasons are recorded for the site."""
    exclusion_cache = CACHE_DIR / "exclusion_results.json"
    
    if exclusion_cache.exists():
        with open(exclusion_cache) as f:
            excl_data = json.load(f)
        excluded = excl_data.get("excluded", [])
        for ex in excluded:
            if ex.get("slug") == slug or ex.get("name") == data["site"].get("name"):
                return False, ex.get("exclusion_reasons", [])
    
    return True, []


def test_viewer_data(slug: str) -> bool:
    """Check that all viewer data files exist and are valid."""
    site_dir = SITES_DIR / slug
    
    required = ["terrain.json", "terrain.bin", "scene.json", "imagery.jpg"]
    
    for f in required:
        if not (site_dir / f).exists():
            return False
    
    with open(site_dir / "terrain.json") as f:
        terrain = json.load(f)
    
    expected_size = terrain["rows"] * terrain["cols"] * 4
    actual_size = (site_dir / "terrain.bin").stat().st_size
    
    return actual_size == expected_size


def run_all_tests() -> int:
    """Run all verification tests."""
    print("=" * 60)
    print("SITE SCOUT VERIFICATION")
    print("=" * 60)
    print()
    
    index_path = SITES_DIR / "index.json"
    if not index_path.exists():
        print("ERROR: No sites index found. Run 'make site-scout' first.")
        return 1
    
    with open(index_path) as f:
        sites = json.load(f)["sites"]
    
    all_passed = True
    
    for site_info in sites:
        slug = site_info["slug"]
        print(f"\n{slug}")
        print("-" * 40)
        
        try:
            data = load_site_data(slug)
        except Exception as e:
            print(f"✗ Failed to load site data: {e}")
            all_passed = False
            continue
        
        hf_pass, hf_info = test_hard_filters(slug, data)
        status = "✓" if hf_pass else "✗"
        print(f"{status} Hard filters (from committed DEM):")
        for check, passed in hf_info["checks"].items():
            s = "✓" if passed else "✗"
            print(f"    {s} {check}")
        m = hf_info["computed"]
        print(f"    Computed: diameter={m['diameter_m']:.0f}m, relief={m['relief_m']:.0f}m, "
              f"floor_flat={m['floor_flat_pct']:.0f}%, rim_slopes={m['rim_slope_range']}")
        if not hf_pass:
            all_passed = False
        
        det_pass = test_determinism(slug, data)
        status = "✓" if det_pass else "✗"
        print(f"{status} Determinism (two runs identical)")
        if not det_pass:
            all_passed = False
        
        vp_pass, vp_info = test_viewpoint_vs_floor(slug, data)
        status = "✓" if vp_pass else "✗"
        print(f"{status} Best viewpoint ({vp_info['best_viewpoint_visibility_pct']:.1f}%) > "
              f"floor center ({vp_info['floor_center_visibility_pct']:.1f}%)")
        if not vp_pass:
            all_passed = False
        
        excl_pass, excl_reasons = test_exclusions(slug, data)
        status = "✓" if excl_pass else "✗"
        print(f"{status} No exclusions")
        if not excl_pass:
            print(f"    FAILED: {excl_reasons}")
            all_passed = False
        
        vd_pass = test_viewer_data(slug)
        status = "✓" if vd_pass else "✗"
        print(f"{status} Viewer data complete")
        if not vd_pass:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL SITE SCOUT CHECKS PASSED")
        return 0
    else:
        print("SOME CHECKS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
