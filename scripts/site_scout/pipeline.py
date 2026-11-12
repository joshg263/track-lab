#!/usr/bin/env python3
"""
Main site scouting pipeline for Japan F1-style circuit locations.

Stages:
1. Download GLO-90 DEM for Japan (coarse scan)
2. Find bowl-shaped depressions
3. Filter by exclusion zones (national parks, towns)
4. Refine top ~20 at full 30m resolution
5. Rank and select final 3-4 with geographic diversity
6. Generate site data for viewer
"""

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent.parent))
from site_scout.fetch_japan_dem import download_tile, tiles_for_bbox
from site_scout.find_bowls import find_bowls_in_tile, Bowl, compute_slope
from site_scout.check_exclusions import check_exclusions_at, EXCLUSION_BUFFER_M

PROJECT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = PROJECT_DIR / "cache"
SITES_DIR = PROJECT_DIR / "data" / "sites"


@dataclass
class RefinedSite:
    """A refined candidate site with full analysis."""
    slug: str
    name: str
    prefecture: str
    lat: float
    lon: float
    diameter_m: float
    relief_m: float
    floor_flat_pct: float
    rim_slope_range: tuple[float, float]
    best_viewpoint: dict
    nearest_town: dict
    why_it_fits: str


def compute_viewshed_from_point(dem: np.ndarray, transform, observer_row: int, observer_col: int,
                                 observer_height_m: float = 2.0, target_height_m: float = 1.0,
                                 samples: int = 256) -> float:
    """
    Compute visibility percentage from an observer point to all floor cells.
    
    Returns the percentage of floor cells visible from the observer.
    """
    rows, cols = dem.shape
    floor_elev = np.percentile(dem[~np.isnan(dem)], 20)
    floor_mask = dem < floor_elev + 10
    
    floor_cells = np.argwhere(floor_mask)
    if len(floor_cells) == 0:
        return 0.0
    
    observer_elev = dem[observer_row, observer_col] + observer_height_m
    visible_count = 0
    
    for target_row, target_col in floor_cells:
        target_elev = dem[target_row, target_col] + target_height_m
        
        dr = target_row - observer_row
        dc = target_col - observer_col
        dist = max(abs(dr), abs(dc))
        
        if dist == 0:
            continue
        
        blocked = False
        for i in range(1, min(dist, samples)):
            t = i / dist
            r = int(observer_row + dr * t)
            c = int(observer_col + dc * t)
            if 0 <= r < rows and 0 <= c < cols:
                los_elev = observer_elev + t * (target_elev - observer_elev)
                if dem[r, c] > los_elev:
                    blocked = True
                    break
        
        if not blocked:
            visible_count += 1
    
    return visible_count / len(floor_cells) * 100


def find_best_viewpoint(dem: np.ndarray, transform, cell_size_m: float) -> dict:
    """
    Find the best rim viewpoint with highest floor visibility.
    
    Samples viewpoints around the rim and computes viewshed for each.
    """
    rows, cols = dem.shape
    floor_elev = np.percentile(dem[~np.isnan(dem)], 20)
    rim_elev = np.percentile(dem[~np.isnan(dem)], 80)
    
    rim_mask = (dem > floor_elev + (rim_elev - floor_elev) * 0.6) & \
               (dem < floor_elev + (rim_elev - floor_elev) * 1.2)
    
    slope = compute_slope(dem, cell_size_m)
    good_rim = rim_mask & (slope >= 10) & (slope <= 30)
    
    rim_cells = np.argwhere(good_rim)
    if len(rim_cells) == 0:
        rim_cells = np.argwhere(rim_mask)
    
    if len(rim_cells) == 0:
        return {"lat": 0, "lon": 0, "visibility_pct": 0, "elevation_m": 0}
    
    sample_indices = np.linspace(0, len(rim_cells) - 1, min(20, len(rim_cells))).astype(int)
    
    best = {"visibility_pct": 0}
    for idx in sample_indices:
        r, c = rim_cells[idx]
        vis_pct = compute_viewshed_from_point(dem, transform, r, c)
        
        if vis_pct > best["visibility_pct"]:
            lat = transform.f - r * abs(transform.e)
            lon = transform.c + c * transform.a
            best = {
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "visibility_pct": round(vis_pct, 1),
                "elevation_m": round(float(dem[r, c]), 1),
                "row": int(r),
                "col": int(c),
            }
    
    return best


def find_nearest_locality(lat: float, lon: float) -> dict:
    """Query OSM for nearest town/village name and distance."""
    import urllib.request
    import urllib.parse
    
    query = f"""
    [out:json][timeout:30];
    (
      node["place"~"village|town|city|hamlet"](around:30000,{lat},{lon});
    );
    out body;
    """
    
    mirrors = [
        "https://overpass-api.de/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    
    for mirror in mirrors:
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(mirror, data=data,
                                          headers={"User-Agent": "TrackLab/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                break
        except Exception:
            continue
    else:
        return {"name": "Unknown", "distance_m": 0}
    
    elements = result.get("elements", [])
    if not elements:
        return {"name": "Unknown", "distance_m": 0}
    
    best = None
    best_dist = float("inf")
    
    for elem in elements:
        elem_lat = elem["lat"]
        elem_lon = elem["lon"]
        dlat = (elem_lat - lat) * 111320
        dlon = (elem_lon - lon) * 111320 * math.cos(math.radians(lat))
        dist = math.sqrt(dlat**2 + dlon**2)
        
        if dist < best_dist:
            best_dist = dist
            best = elem
    
    if best:
        return {
            "name": best.get("tags", {}).get("name", "Unknown"),
            "distance_m": round(best_dist, 0),
            "place_type": best.get("tags", {}).get("place", "locality"),
        }
    
    return {"name": "Unknown", "distance_m": 0}


def get_prefecture(lat: float, lon: float) -> str:
    """Get the prefecture name for a location (simplified lookup)."""
    prefectures = [
        (35.5, 139.8, "Chiba"),
        (35.7, 139.7, "Tokyo"),
        (35.4, 139.6, "Kanagawa"),
        (35.9, 139.9, "Saitama"),
        (36.4, 139.1, "Gunma"),
        (36.6, 138.2, "Nagano"),
        (35.2, 136.9, "Aichi"),
        (34.7, 135.5, "Osaka"),
        (35.0, 135.8, "Kyoto"),
        (34.7, 135.2, "Hyogo"),
        (34.4, 132.5, "Hiroshima"),
        (33.6, 130.4, "Fukuoka"),
        (32.8, 130.7, "Kumamoto"),
        (31.6, 130.6, "Kagoshima"),
        (33.2, 131.6, "Oita"),
        (43.1, 141.3, "Hokkaido"),
        (38.3, 140.9, "Miyagi"),
        (39.7, 140.1, "Akita"),
        (40.8, 140.7, "Aomori"),
        (37.9, 139.0, "Niigata"),
        (36.1, 140.1, "Ibaraki"),
        (36.6, 139.9, "Tochigi"),
        (35.0, 138.4, "Shizuoka"),
        (35.4, 138.6, "Yamanashi"),
        (36.2, 137.3, "Toyama"),
        (36.6, 136.6, "Ishikawa"),
        (33.8, 132.8, "Ehime"),
        (33.6, 133.5, "Kochi"),
        (34.1, 131.5, "Yamaguchi"),
        (34.7, 133.9, "Okayama"),
        (35.5, 134.2, "Tottori"),
    ]
    
    best_pref = "Japan"
    best_dist = float("inf")
    
    for p_lat, p_lon, name in prefectures:
        dist = (lat - p_lat)**2 + (lon - p_lon)**2
        if dist < best_dist:
            best_dist = dist
            best_pref = name
    
    return best_pref


def generate_slug(name: str, prefecture: str) -> str:
    """Generate a URL-safe slug from locality name."""
    import re
    slug = name.lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r'[^a-z0-9_]', '', slug)
    if not slug:
        slug = prefecture.lower()[:8]
    return slug[:20]


def refine_site_at_30m(lat: float, lon: float, cache_dir: Path) -> dict | None:
    """
    Refine a candidate site using 30m resolution DEM.
    
    Downloads the required 30m tile and performs detailed analysis.
    """
    tile_lat = int(math.floor(lat))
    tile_lon = int(math.floor(lon))
    
    tile_path = download_tile(tile_lat, tile_lon, cache_dir / "dem_30m", "30m")
    if not tile_path:
        return None
    
    buffer_deg = 0.03
    
    with rasterio.open(tile_path) as src:
        win = from_bounds(
            lon - buffer_deg, lat - buffer_deg,
            lon + buffer_deg, lat + buffer_deg,
            src.transform
        )
        win = win.round_offsets().round_lengths()
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        
        dem = src.read(1, window=win).astype(np.float32)
        transform = src.window_transform(win)
        
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(lat))
    
    valid = ~np.isnan(dem)
    if valid.sum() < 100:
        return None
    
    floor_elev = float(np.percentile(dem[valid], 15))
    rim_elev = float(np.percentile(dem[valid], 85))
    relief_m = rim_elev - floor_elev
    
    floor_mask = dem < floor_elev + relief_m * 0.3
    floor_size_m = math.sqrt(floor_mask.sum()) * cell_size_m
    
    slope = compute_slope(dem, cell_size_m)
    floor_slopes = slope[floor_mask & valid]
    floor_flat_pct = float((floor_slopes < 5).sum() / max(len(floor_slopes), 1) * 100)
    
    rim_mask = (dem > floor_elev + relief_m * 0.5) & (dem < rim_elev + 20)
    rim_slopes = slope[rim_mask & valid]
    rim_slope_min = float(np.percentile(rim_slopes, 20)) if len(rim_slopes) > 0 else 0
    rim_slope_max = float(np.percentile(rim_slopes, 80)) if len(rim_slopes) > 0 else 0
    
    best_vp = find_best_viewpoint(dem, transform, cell_size_m)
    
    return {
        "lat": lat,
        "lon": lon,
        "relief_m": round(relief_m, 1),
        "diameter_m": round(floor_size_m * 2, 0),
        "floor_flat_pct": round(floor_flat_pct, 1),
        "rim_slope_range": (round(rim_slope_min, 1), round(rim_slope_max, 1)),
        "best_viewpoint": best_vp,
        "dem_shape": dem.shape,
        "transform": [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f],
    }


def select_diverse_sites(sites: list[dict], n: int = 4, min_distance_km: float = 100) -> list[dict]:
    """
    Select n sites with geographic diversity.
    
    Prefers sites spread across different regions.
    """
    if len(sites) <= n:
        return sites
    
    selected = [sites[0]]
    
    for _ in range(n - 1):
        best_site = None
        best_min_dist = 0
        
        for site in sites:
            if site in selected:
                continue
            
            min_dist = float("inf")
            for sel in selected:
                dlat = (site["lat"] - sel["lat"]) * 111.32
                dlon = (site["lon"] - sel["lon"]) * 111.32 * math.cos(math.radians(site["lat"]))
                dist = math.sqrt(dlat**2 + dlon**2)
                min_dist = min(min_dist, dist)
            
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_site = site
        
        if best_site and best_min_dist >= min_distance_km:
            selected.append(best_site)
        elif best_site:
            selected.append(best_site)
    
    return selected


def run_pipeline(skip_download: bool = False, max_candidates: int = 50,
                 final_sites: int = 4) -> list[RefinedSite]:
    """Run the full site scouting pipeline."""
    
    cache_dir = CACHE_DIR / "site_scout"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    bowls_cache = cache_dir / "japan_bowls.json"
    
    if not skip_download or not bowls_cache.exists():
        print("Stage 1: Downloading GLO-90 DEM for Japan...")
        from site_scout.fetch_japan_dem import download_japan_dem
        dem_dir = cache_dir / "dem_90m"
        dem_paths = download_japan_dem(dem_dir, resolution="90m")
        print(f"  Downloaded {len(dem_paths)} tiles")
        
        print("\nStage 2: Finding bowl-shaped depressions...")
        from site_scout.find_bowls import scan_japan_for_bowls
        bowls = scan_japan_for_bowls(str(dem_dir), str(bowls_cache))
        print(f"  Found {len(bowls)} candidate bowls")
    else:
        print("Loading cached bowl candidates...")
        with open(bowls_cache) as f:
            data = json.load(f)
        bowls = data["bowls"]
        print(f"  Loaded {len(bowls)} candidates")
    
    if isinstance(bowls, list) and len(bowls) > 0 and isinstance(bowls[0], dict):
        pass
    else:
        bowls = [
            {
                "id": b.id, "lat": b.lat, "lon": b.lon,
                "relief_m": b.relief_m, "diameter_m": b.diameter_m,
                "floor_flat_pct": b.floor_flat_pct,
                "rim_slope_range": list(b.rim_slope_range),
                "enclosure_pct": b.enclosure_pct,
            }
            for b in bowls
        ]
    
    print(f"\nStage 3: Filtering by exclusion zones (top {max_candidates})...")
    bowls_sorted = sorted(bowls, key=lambda b: -b.get("enclosure_pct", 0))[:max_candidates]
    
    exclusion_cache = cache_dir / "exclusion_results.json"
    if exclusion_cache.exists():
        print("  Loading cached exclusion results...")
        with open(exclusion_cache) as f:
            excl_data = json.load(f)
        passed = excl_data["passed"]
        excluded = excl_data["excluded"]
    else:
        from site_scout.check_exclusions import filter_sites_by_exclusions
        passed, excluded = filter_sites_by_exclusions(bowls_sorted, delay_s=0.5)
        with open(exclusion_cache, "w") as f:
            json.dump({"passed": passed, "excluded": excluded}, f, indent=2)
    
    print(f"  Passed: {len(passed)}, Excluded: {len(excluded)}")
    
    print(f"\nStage 4: Refining top ~20 at 30m resolution...")
    refined = []
    for i, site in enumerate(passed[:20]):
        print(f"  Refining site {i+1}/{min(20, len(passed))}: {site['lat']:.4f}, {site['lon']:.4f}")
        
        details = refine_site_at_30m(site["lat"], site["lon"], cache_dir)
        if details:
            site.update(details)
            refined.append(site)
            print(f"    Relief: {details['relief_m']:.0f}m, Floor flat: {details['floor_flat_pct']:.0f}%, "
                  f"Best VP visibility: {details['best_viewpoint']['visibility_pct']:.0f}%")
    
    print(f"\nStage 5: Selecting {final_sites} diverse final sites...")
    refined_sorted = sorted(refined, key=lambda s: -s["best_viewpoint"]["visibility_pct"])
    final = select_diverse_sites(refined_sorted, n=final_sites)
    
    print("\nStage 6: Generating site metadata...")
    results = []
    for site in final:
        import time
        time.sleep(0.5)
        
        locality = find_nearest_locality(site["lat"], site["lon"])
        prefecture = get_prefecture(site["lat"], site["lon"])
        slug = generate_slug(locality["name"], prefecture)
        
        if 2000 <= site.get("diameter_m", 0) <= 3000 and 50 <= site.get("relief_m", 0) <= 150:
            why = "Natural bowl with ideal dimensions"
        elif site["best_viewpoint"]["visibility_pct"] > 70:
            why = "Exceptional viewshed potential"
        elif site.get("floor_flat_pct", 0) > 60:
            why = "Very flat floor for track layout"
        else:
            why = "Good balance of size and enclosure"
        
        results.append(RefinedSite(
            slug=slug,
            name=locality["name"],
            prefecture=prefecture,
            lat=site["lat"],
            lon=site["lon"],
            diameter_m=site.get("diameter_m", 0),
            relief_m=site.get("relief_m", 0),
            floor_flat_pct=site.get("floor_flat_pct", 0),
            rim_slope_range=tuple(site.get("rim_slope_range", (0, 0))),
            best_viewpoint=site.get("best_viewpoint", {}),
            nearest_town=locality,
            why_it_fits=why,
        ))
        
        print(f"  {slug}: {locality['name']}, {prefecture} - {why}")
    
    results_cache = cache_dir / "final_sites.json"
    with open(results_cache, "w") as f:
        json.dump([
            {
                "slug": s.slug,
                "name": s.name,
                "prefecture": s.prefecture,
                "lat": s.lat,
                "lon": s.lon,
                "diameter_m": s.diameter_m,
                "relief_m": s.relief_m,
                "floor_flat_pct": s.floor_flat_pct,
                "rim_slope_range": list(s.rim_slope_range),
                "best_viewpoint": s.best_viewpoint,
                "nearest_town": s.nearest_town,
                "why_it_fits": s.why_it_fits,
            }
            for s in results
        ], f, indent=2)
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Japan site scouting pipeline")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download if cache exists")
    parser.add_argument("--max-candidates", type=int, default=50,
                        help="Max candidates to check for exclusions")
    parser.add_argument("--final-sites", type=int, default=4,
                        help="Number of final sites to select")
    args = parser.parse_args()
    
    sites = run_pipeline(
        skip_download=args.skip_download,
        max_candidates=args.max_candidates,
        final_sites=args.final_sites,
    )
    
    print(f"\n{'='*60}")
    print(f"Selected {len(sites)} sites for viewer:")
    for s in sites:
        print(f"  {s.slug}: {s.name}, {s.prefecture}")
        print(f"    Lat/Lon: {s.lat:.5f}, {s.lon:.5f}")
        print(f"    Diameter: {s.diameter_m:.0f}m, Relief: {s.relief_m:.0f}m")
        print(f"    Best viewpoint visibility: {s.best_viewpoint.get('visibility_pct', 0):.1f}%")
