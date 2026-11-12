#!/usr/bin/env python3
"""
Generate viewer data for selected Japan sites.

For each site, creates:
- data/sites/<slug>/dem.tif - Cropped DEM (30m resolution)
- data/sites/<slug>/imagery.jpg - Sentinel-2 cloudless texture
- data/sites/<slug>/site.json - Site metadata and viewpoint info
- data/sites/<slug>/osm.geojson - OSM context (optional, minimal)
"""

import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))
from site_scout.fetch_japan_dem import download_tile
from site_scout.find_bowls import compute_slope
from site_scout.pipeline import compute_viewshed_from_point, find_best_viewpoint

PROJECT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = PROJECT_DIR / "cache" / "site_scout"
SITES_DIR = PROJECT_DIR / "data" / "sites"

TILE_URL = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg"
ATTRIBUTION = ("Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH "
               "(Contains modified Copernicus Sentinel data 2016 & 2017), CC BY 4.0")


def crop_dem_for_site(lat: float, lon: float, slug: str, buffer_deg: float = 0.015) -> Path | None:
    """Crop 30m DEM for a site and save to data/sites/<slug>/dem.tif."""
    tile_lat = int(math.floor(lat))
    tile_lon = int(math.floor(lon))
    
    tile_path = download_tile(tile_lat, tile_lon, CACHE_DIR / "dem_30m", "30m")
    if not tile_path:
        print(f"    Could not download 30m tile for {lat:.4f}, {lon:.4f}")
        return None
    
    site_dir = SITES_DIR / slug
    site_dir.mkdir(parents=True, exist_ok=True)
    out_path = site_dir / "dem.tif"
    
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
        
        profile = src.profile.copy()
        profile.update(
            width=dem.shape[1],
            height=dem.shape[0],
            transform=transform,
            dtype='float32',
            compress='deflate',
        )
        
        with rasterio.open(out_path, 'w', **profile) as dst:
            dst.write(dem, 1)
    
    size_kb = out_path.stat().st_size / 1024
    print(f"    Wrote DEM: {dem.shape[1]}x{dem.shape[0]} ({size_kb:.0f} KB)")
    
    return out_path


def lonlat_to_pixel(lon, lat, z):
    """Convert lon/lat to tile pixel coordinates at zoom level z."""
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(z, x, y):
    """Fetch a single S2 cloudless tile."""
    url = TILE_URL.format(z=z, x=x, y=y)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TrackLab/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception as exc:
            time.sleep(2 ** attempt)
    return None


def fetch_imagery_for_site(lat: float, lon: float, slug: str, buffer_deg: float = 0.015,
                            zoom: int = 15, quality: int = 75) -> Path | None:
    """Fetch and bake Sentinel-2 cloudless imagery for a site."""
    w = round(lon - buffer_deg, 4)
    e = round(lon + buffer_deg, 4)
    s = round(lat - buffer_deg, 4)
    n = round(lat + buffer_deg, 4)
    
    px0, py0 = lonlat_to_pixel(w, n, zoom)
    px1, py1 = lonlat_to_pixel(e, s, zoom)
    tx0, ty0 = int(px0 // 256), int(py0 // 256)
    tx1, ty1 = int(px1 // 256), int(py1 // 256)
    
    num_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    print(f"    Fetching {num_tiles} imagery tiles...")
    
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_tile(zoom, tx, ty)
            if tile:
                mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
            time.sleep(0.1)
    
    src = np.asarray(mosaic, dtype=np.float32)
    
    mid_lat = (s + n) / 2
    width_m = (e - w) * 111320 * math.cos(math.radians(mid_lat))
    height_m = (n - s) * 111320
    metres_per_pixel = 3.0
    out_w = int(round(width_m / metres_per_pixel))
    out_h = int(round(height_m / metres_per_pixel))
    
    lon_arr = w + (np.arange(out_w) + 0.5) / out_w * (e - w)
    lat_arr = n - (np.arange(out_h) + 0.5) / out_h * (n - s)
    sx, _ = lonlat_to_pixel(lon_arr, mid_lat, zoom)
    _, sy = lonlat_to_pixel(lon_arr[0], lat_arr, zoom)
    sx = sx - tx0 * 256 - 0.5
    sy = sy - ty0 * 256 - 0.5
    x0 = np.clip(np.floor(sx).astype(int), 0, src.shape[1] - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, src.shape[0] - 2)
    fx = (sx - x0)[None, :, None]
    fy = (sy - y0)[:, None, None]
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    out = (src[Y0, X0] * (1 - fx) * (1 - fy) + src[Y0, X0 + 1] * fx * (1 - fy) +
           src[Y0 + 1, X0] * (1 - fx) * fy + src[Y0 + 1, X0 + 1] * fx * fy)
    
    site_dir = SITES_DIR / slug
    site_dir.mkdir(parents=True, exist_ok=True)
    jpg_path = site_dir / "imagery.jpg"
    
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(
        jpg_path, quality=quality, optimize=True
    )
    
    meta_path = site_dir / "imagery.json"
    with open(meta_path, "w") as f:
        json.dump({
            "image": "imagery.jpg",
            "bbox": [w, s, e, n],
            "width": out_w,
            "height": out_h,
            "attribution": ATTRIBUTION,
        }, f, indent=2)
    
    size_kb = jpg_path.stat().st_size / 1024
    print(f"    Wrote imagery: {out_w}x{out_h} ({size_kb:.0f} KB)")
    
    return jpg_path


def compute_site_metrics(dem_path: Path, lat: float, lon: float) -> dict:
    """Compute detailed site metrics from the cropped DEM."""
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    valid = ~np.isnan(dem)
    if valid.sum() < 100:
        return {}
    
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
    rim_slope_min = float(np.percentile(rim_slopes, 20)) if len(rim_slopes) > 0 else 0
    rim_slope_max = float(np.percentile(rim_slopes, 80)) if len(rim_slopes) > 0 else 0
    
    best_vp = find_best_viewpoint(dem, transform, cell_size_m)
    
    rows, cols = dem.shape
    center_row = rows // 2
    center_col = cols // 2
    floor_vis = compute_viewshed_from_point(dem, transform, center_row, center_col)
    
    return {
        "floor_elev_m": round(floor_elev, 1),
        "rim_elev_m": round(rim_elev, 1),
        "relief_m": round(relief_m, 1),
        "diameter_m": round(diameter_m, 0),
        "floor_flat_pct": round(floor_flat_pct, 1),
        "rim_slope_range": [round(rim_slope_min, 1), round(rim_slope_max, 1)],
        "best_viewpoint": best_vp,
        "floor_center_visibility_pct": round(floor_vis, 1),
        "dem_rows": rows,
        "dem_cols": cols,
        "cell_size_m": round(cell_size_m, 2),
    }


def generate_site_json(slug: str, site_info: dict, metrics: dict) -> Path:
    """Generate site.json with all metadata for the viewer."""
    site_dir = SITES_DIR / slug
    site_dir.mkdir(parents=True, exist_ok=True)
    
    data = {
        "slug": slug,
        "name": site_info.get("name", slug),
        "prefecture": site_info.get("prefecture", "Japan"),
        "lat": site_info["lat"],
        "lon": site_info["lon"],
        "nearest_town": site_info.get("nearest_town", {}),
        "why_it_fits": site_info.get("why_it_fits", ""),
        "metrics": metrics,
        "files": {
            "dem": "dem.tif",
            "imagery": "imagery.jpg",
            "imagery_meta": "imagery.json",
        },
        "attribution": [
            "Elevation: Copernicus DEM GLO-30 (ESA / Copernicus)",
            ATTRIBUTION,
        ],
    }
    
    json_path = site_dir / "site.json"
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)
    
    return json_path


def generate_all_site_data(sites_json: Path = None) -> list[str]:
    """Generate viewer data for all selected sites."""
    if sites_json is None:
        sites_json = CACHE_DIR / "final_sites.json"
    
    with open(sites_json) as f:
        sites = json.load(f)
    
    slugs = []
    for site in sites:
        slug = site["slug"]
        print(f"\nGenerating data for {slug} ({site['name']}, {site['prefecture']})...")
        
        dem_path = crop_dem_for_site(site["lat"], site["lon"], slug)
        if not dem_path:
            print(f"  Skipping {slug} - no DEM")
            continue
        
        fetch_imagery_for_site(site["lat"], site["lon"], slug)
        
        metrics = compute_site_metrics(dem_path, site["lat"], site["lon"])
        
        generate_site_json(slug, site, metrics)
        
        site_dir = SITES_DIR / slug
        total_size = sum(f.stat().st_size for f in site_dir.iterdir())
        print(f"    Total size: {total_size / 1024:.0f} KB")
        
        if total_size > 2 * 1024 * 1024:
            print(f"    WARNING: Site exceeds 2 MB budget!")
        
        slugs.append(slug)
    
    return slugs


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate viewer data for Japan sites")
    parser.add_argument("--sites-json", type=Path, default=CACHE_DIR / "final_sites.json",
                        help="Path to final_sites.json")
    parser.add_argument("--site", help="Generate data for a single site slug only")
    args = parser.parse_args()
    
    if args.site:
        with open(args.sites_json) as f:
            sites = json.load(f)
        site = next((s for s in sites if s["slug"] == args.site), None)
        if site:
            print(f"Generating data for {args.site}...")
            dem_path = crop_dem_for_site(site["lat"], site["lon"], args.site)
            if dem_path:
                fetch_imagery_for_site(site["lat"], site["lon"], args.site)
                metrics = compute_site_metrics(dem_path, site["lat"], site["lon"])
                generate_site_json(args.site, site, metrics)
        else:
            print(f"Site {args.site} not found in {args.sites_json}")
    else:
        slugs = generate_all_site_data(args.sites_json)
        print(f"\n{'='*60}")
        print(f"Generated data for {len(slugs)} sites:")
        for slug in slugs:
            print(f"  - {slug}")
