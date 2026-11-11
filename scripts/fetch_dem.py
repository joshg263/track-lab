#!/usr/bin/env python3
"""
Fetch Copernicus DEM GLO-30 tiles from AWS Open Data.

Data source: https://registry.opendata.aws/copernicus-dem/
License: Copernicus DEM GLO-30 is free and open, see:
https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model

No account, API key, or payment required - it's a public S3 bucket.
"""

import argparse
import json
import math
import os
import urllib.request
from pathlib import Path

BUCKET_URL = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "dem"


def tile_name(lat: int, lon: int) -> str:
    """
    Generate Copernicus DEM tile name for given lat/lon.
    Tiles are 1x1 degree, named by their SW corner.
    Format: Copernicus_DSM_COG_10_NXX_00_EXXX_00_DEM
    """
    lat_hem = "N" if lat >= 0 else "S"
    lon_hem = "E" if lon >= 0 else "W"
    lat_str = f"{abs(lat):02d}"
    lon_str = f"{abs(lon):03d}"
    return f"Copernicus_DSM_COG_10_{lat_hem}{lat_str}_00_{lon_hem}{lon_str}_00_DEM"


def tile_url(lat: int, lon: int) -> str:
    """Get the full URL for a DEM tile."""
    name = tile_name(lat, lon)
    return f"{BUCKET_URL}/{name}/{name}.tif"


def tiles_for_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> list:
    """
    Return list of (lat, lon) tile corners needed to cover the bounding box.
    """
    tiles = []
    for lat in range(math.floor(min_lat), math.ceil(max_lat)):
        for lon in range(math.floor(min_lon), math.ceil(max_lon)):
            tiles.append((lat, lon))
    return tiles


def download_tile(lat: int, lon: int, force: bool = False) -> Path:
    """
    Download a single DEM tile to cache. Returns path to local file.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = CACHE_DIR / f"{tile_name(lat, lon)}.tif"
    
    if local_path.exists() and not force:
        print(f"  Using cached: {local_path.name}")
        return local_path
    
    url = tile_url(lat, lon)
    print(f"  Downloading: {url}")
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TrackLab/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
            local_path.write_bytes(data)
            print(f"  Saved: {local_path.name} ({len(data) / 1024 / 1024:.1f} MB)")
        return local_path
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Failed to download {url}: {e}")


def fetch_dem_for_geojson(geojson_path: str, buffer_deg: float = 0.02) -> list:
    """
    Fetch DEM tiles needed to cover a GeoJSON file plus buffer.
    Returns list of paths to downloaded tiles.
    """
    with open(geojson_path) as f:
        gj = json.load(f)
    
    all_coords = []
    for feature in gj.get("features", [gj]):
        geom = feature.get("geometry", feature)
        if geom["type"] == "LineString":
            all_coords.extend(geom["coordinates"])
        elif geom["type"] == "Point":
            all_coords.append(geom["coordinates"])
    
    if not all_coords:
        raise ValueError("No coordinates found in GeoJSON")
    
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    
    min_lon, max_lon = min(lons) - buffer_deg, max(lons) + buffer_deg
    min_lat, max_lat = min(lats) - buffer_deg, max(lats) + buffer_deg
    
    print(f"Bounding box: {min_lat:.4f},{min_lon:.4f} to {max_lat:.4f},{max_lon:.4f}")
    
    tiles = tiles_for_bbox(min_lon, min_lat, max_lon, max_lat)
    print(f"Need {len(tiles)} tile(s)")
    
    paths = []
    for lat, lon in tiles:
        path = download_tile(lat, lon)
        paths.append(path)
    
    return paths


def main():
    parser = argparse.ArgumentParser(description="Fetch Copernicus DEM tiles")
    parser.add_argument("geojson", help="GeoJSON file defining area of interest")
    parser.add_argument("--buffer", type=float, default=0.02, 
                        help="Buffer in degrees around GeoJSON bbox (default: 0.02 ≈ 2km)")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = parser.parse_args()
    
    paths = fetch_dem_for_geojson(args.geojson, args.buffer)
    print(f"\nDownloaded {len(paths)} tile(s):")
    for p in paths:
        print(f"  {p}")


if __name__ == "__main__":
    main()
