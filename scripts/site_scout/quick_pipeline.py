#!/usr/bin/env python3
"""
Quick site scouting pipeline using pre-researched candidates.

This is a faster version of the full pipeline that:
1. Uses pre-researched candidate coordinates
2. Downloads 30m DEM directly for each
3. Validates terrain metrics
4. Checks OSM exclusions
5. Ranks by viewshed and selects diverse final sites
"""

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

sys.path.insert(0, str(Path(__file__).parent.parent))
from site_scout.candidates import JAPAN_CANDIDATES
from site_scout.fetch_japan_dem import download_tile
from site_scout.find_bowls import compute_slope

PROJECT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = PROJECT_DIR / "cache" / "site_scout"
SITES_DIR = PROJECT_DIR / "data" / "sites"


def analyze_site_terrain(lat: float, lon: float, cache_dir: Path,
                          buffer_deg: float = 0.015) -> dict | None:
    """Download 30m DEM and analyze terrain metrics for a candidate site."""
    tile_lat = int(math.floor(lat))
    tile_lon = int(math.floor(lon))
    
    print(f"    Downloading 30m DEM tile N{tile_lat}_E{tile_lon}...")
    tile_path = download_tile(tile_lat, tile_lon, cache_dir / "dem_30m", "30m")
    if not tile_path:
        print(f"    No DEM coverage")
        return None
    
    with rasterio.open(tile_path) as src:
        win = from_bounds(
            lon - buffer_deg, lat - buffer_deg,
            lon + buffer_deg, lat + buffer_deg,
            src.transform
        )
        win = win.round_offsets().round_lengths()
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        
        if win.width < 10 or win.height < 10:
            print(f"    DEM window too small")
            return None
        
        dem = src.read(1, window=win).astype(np.float32)
        transform = src.window_transform(win)
        
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    valid = ~np.isnan(dem)
    if valid.sum() < 100:
        print(f"    Too much nodata")
        return None
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(lat))
    
    floor_elev = float(np.percentile(dem[valid], 15))
    rim_elev = float(np.percentile(dem[valid], 85))
    relief_m = rim_elev - floor_elev
    
    floor_mask = dem < floor_elev + relief_m * 0.3
    floor_cells = floor_mask.sum()
    floor_area_m2 = floor_cells * cell_size_m * cell_size_m
    diameter_m = 2 * math.sqrt(floor_area_m2 / math.pi)
    
    slope = compute_slope(dem, cell_size_m)
    floor_slopes = slope[floor_mask & valid]
    floor_flat_pct = float((floor_slopes < 5).sum() / max(len(floor_slopes), 1) * 100)
    
    rim_mask = (dem > floor_elev + relief_m * 0.5) & (dem < rim_elev + 20)
    rim_slopes = slope[rim_mask & valid]
    if len(rim_slopes) > 0:
        rim_slope_min = float(np.percentile(rim_slopes, 20))
        rim_slope_max = float(np.percentile(rim_slopes, 80))
    else:
        rim_slope_min, rim_slope_max = 0, 0
    
    good_rim = rim_mask & (slope >= 10) & (slope <= 30)
    good_rim_cells = np.argwhere(good_rim)
    
    best_vp = {"lat": lat, "lon": lon, "visibility_pct": 0, "elevation_m": 0}
    
    if len(good_rim_cells) > 0:
        sample_indices = np.linspace(0, len(good_rim_cells) - 1, 
                                      min(15, len(good_rim_cells))).astype(int)
        
        for idx in sample_indices:
            r, c = good_rim_cells[idx]
            vis_pct = compute_floor_visibility(dem, r, c, floor_mask)
            
            if vis_pct > best_vp["visibility_pct"]:
                vp_lat = transform.f - r * abs(transform.e)
                vp_lon = transform.c + c * transform.a
                best_vp = {
                    "lat": round(vp_lat, 5),
                    "lon": round(vp_lon, 5),
                    "visibility_pct": round(vis_pct, 1),
                    "elevation_m": round(float(dem[r, c]), 1),
                }
    
    center_r, center_c = dem.shape[0] // 2, dem.shape[1] // 2
    random_floor_vis = compute_floor_visibility(dem, center_r, center_c, floor_mask)
    
    return {
        "lat": lat,
        "lon": lon,
        "relief_m": round(relief_m, 1),
        "diameter_m": round(diameter_m, 0),
        "floor_flat_pct": round(floor_flat_pct, 1),
        "rim_slope_range": (round(rim_slope_min, 1), round(rim_slope_max, 1)),
        "best_viewpoint": best_vp,
        "random_floor_visibility_pct": round(random_floor_vis, 1),
        "dem_shape": dem.shape,
        "cell_size_m": round(cell_size_m, 2),
    }


def compute_floor_visibility(dem: np.ndarray, obs_r: int, obs_c: int, 
                              floor_mask: np.ndarray, 
                              observer_height_m: float = 2.0) -> float:
    """Compute what percentage of floor cells are visible from observer."""
    rows, cols = dem.shape
    floor_cells = np.argwhere(floor_mask)
    
    if len(floor_cells) == 0:
        return 0.0
    
    observer_elev = dem[obs_r, obs_c] + observer_height_m
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


def check_site_exclusions(lat: float, lon: float) -> tuple[bool, list]:
    """Quick check for OSM exclusions near site."""
    import urllib.request
    import urllib.parse
    
    buffer_m = 1000
    
    query = f"""
    [out:json][timeout:20];
    (
      relation["boundary"="national_park"](around:{buffer_m},{lat},{lon});
      node["place"~"city|town"](around:{buffer_m},{lat},{lon});
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
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode())
                break
        except Exception:
            continue
    else:
        return False, []
    
    exclusions = []
    for elem in result.get("elements", []):
        tags = elem.get("tags", {})
        if tags.get("boundary") == "national_park":
            exclusions.append({
                "kind": "national_park",
                "name": tags.get("name", "National Park"),
            })
        elif tags.get("place") in ("city", "town"):
            exclusions.append({
                "kind": f"place_{tags['place']}",
                "name": tags.get("name", "Town"),
            })
    
    return len(exclusions) > 0, exclusions


def find_nearest_locality(lat: float, lon: float) -> dict:
    """Query OSM for nearest town/village name."""
    import urllib.request
    import urllib.parse
    
    query = f"""
    [out:json][timeout:20];
    node["place"~"village|town|hamlet"](around:20000,{lat},{lon});
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
            with urllib.request.urlopen(req, timeout=20) as resp:
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
        }
    
    return {"name": "Unknown", "distance_m": 0}


def select_diverse_sites(sites: list[dict], n: int = 4, 
                          min_distance_km: float = 80) -> list[dict]:
    """Select n sites maximizing geographic diversity."""
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
            
            score = min_dist + site.get("best_viewpoint", {}).get("visibility_pct", 0) * 0.5
            if score > best_min_dist:
                best_min_dist = score
                best_site = site
        
        if best_site:
            selected.append(best_site)
    
    return selected


def run_quick_pipeline(final_count: int = 4) -> list[dict]:
    """Run the quick pipeline on pre-researched candidates."""
    cache_dir = CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    print("Stage 1: Analyzing pre-researched candidates...")
    analyzed = []
    
    for i, candidate in enumerate(JAPAN_CANDIDATES):
        print(f"\n  [{i+1}/{len(JAPAN_CANDIDATES)}] {candidate['name']}, {candidate['prefecture']}")
        
        metrics = analyze_site_terrain(candidate["lat"], candidate["lon"], cache_dir)
        if not metrics:
            print(f"    SKIP: Could not analyze terrain")
            continue
        
        if metrics["relief_m"] < 40 or metrics["relief_m"] > 180:
            print(f"    SKIP: Relief {metrics['relief_m']:.0f}m outside range (target 50-150m)")
            continue
        
        if metrics["diameter_m"] < 1500 or metrics["diameter_m"] > 4000:
            print(f"    SKIP: Diameter {metrics['diameter_m']:.0f}m outside range")
            continue
        
        site = {**candidate, **metrics}
        analyzed.append(site)
        
        print(f"    OK: Relief {metrics['relief_m']:.0f}m, Diameter {metrics['diameter_m']:.0f}m, "
              f"Floor flat {metrics['floor_flat_pct']:.0f}%, Best VP vis {metrics['best_viewpoint']['visibility_pct']:.0f}%")
    
    print(f"\n\nStage 2: Checking OSM exclusions for {len(analyzed)} candidates...")
    passed = []
    excluded_sites = []
    
    for site in analyzed:
        print(f"  Checking {site['name']}...")
        is_excluded, reasons = check_site_exclusions(site["lat"], site["lon"])
        time.sleep(0.5)
        
        if is_excluded:
            print(f"    EXCLUDED: {', '.join(r['kind'] for r in reasons)}")
            site["exclusion_reasons"] = reasons
            excluded_sites.append(site)
        else:
            print(f"    PASSED")
            site["exclusion_reasons"] = []
            passed.append(site)
    
    print(f"\n\nStage 3: Selecting {final_count} diverse sites from {len(passed)} passed...")
    passed.sort(key=lambda s: -s["best_viewpoint"]["visibility_pct"])
    final = select_diverse_sites(passed, n=final_count)
    
    print(f"\n\nStage 4: Getting locality names...")
    for site in final:
        locality = find_nearest_locality(site["lat"], site["lon"])
        site["nearest_town"] = locality
        time.sleep(0.3)
        
        if 2000 <= site["diameter_m"] <= 3000 and 50 <= site["relief_m"] <= 150:
            site["why_it_fits"] = "Natural bowl with ideal dimensions"
        elif site["best_viewpoint"]["visibility_pct"] > 60:
            site["why_it_fits"] = "Exceptional viewshed potential"
        elif site["floor_flat_pct"] > 50:
            site["why_it_fits"] = "Very flat floor for track layout"
        else:
            site["why_it_fits"] = "Good balance of size and enclosure"
        
        import re
        slug_base = site["name"].lower().replace(" ", "_")
        site["slug"] = re.sub(r'[^a-z0-9_]', '', slug_base)[:20]
    
    results_path = cache_dir / "final_sites.json"
    with open(results_path, "w") as f:
        json.dump(final, f, indent=2)
    
    excluded_path = cache_dir / "excluded_sites.json"
    with open(excluded_path, "w") as f:
        json.dump(excluded_sites, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Final {len(final)} sites selected:")
    for s in final:
        print(f"\n  {s['slug']}: {s['name']}, {s['prefecture']}")
        print(f"    Lat/Lon: {s['lat']:.4f}, {s['lon']:.4f}")
        print(f"    Diameter: {s['diameter_m']:.0f}m, Relief: {s['relief_m']:.0f}m")
        print(f"    Floor flat: {s['floor_flat_pct']:.0f}%, Rim slopes: {s['rim_slope_range']}")
        print(f"    Best viewpoint visibility: {s['best_viewpoint']['visibility_pct']:.1f}%")
        print(f"    Why: {s['why_it_fits']}")
    
    print(f"\n\nExcluded {len(excluded_sites)} sites:")
    for s in excluded_sites:
        reasons = ", ".join(r.get("name", r["kind"]) for r in s["exclusion_reasons"])
        print(f"  {s['name']}: {reasons}")
    
    return final


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Quick Japan site scouting pipeline")
    parser.add_argument("--final-count", type=int, default=4,
                        help="Number of final sites to select")
    args = parser.parse_args()
    
    run_quick_pipeline(final_count=args.final_count)
