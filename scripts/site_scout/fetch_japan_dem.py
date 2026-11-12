#!/usr/bin/env python3
"""
Download GLO-90 DEM tiles for Japan from AWS Open Data.

Uses the public copernicus-dem-90m bucket for coarse scanning, then
falls back to copernicus-dem-30m for high-resolution refinement.
"""

import os
import urllib.request
from pathlib import Path

BUCKET_90M = "https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com"
BUCKET_30M = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"

JAPAN_TILES_HONSHU = [
    (lat, lon) for lat in range(33, 42) for lon in range(129, 142)
]
JAPAN_TILES_KYUSHU = [
    (lat, lon) for lat in range(31, 34) for lon in range(129, 132)
]
JAPAN_TILES_HOKKAIDO = [
    (lat, lon) for lat in range(41, 46) for lon in range(139, 146)
]
JAPAN_TILES = list(set(JAPAN_TILES_HONSHU + JAPAN_TILES_KYUSHU + JAPAN_TILES_HOKKAIDO))


def tile_name_90m(lat: int, lon: int) -> str:
    lat_hem = "N" if lat >= 0 else "S"
    lon_hem = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_30_{lat_hem}{abs(lat):02d}_00_{lon_hem}{abs(lon):03d}_00_DEM"


def tile_name_30m(lat: int, lon: int) -> str:
    lat_hem = "N" if lat >= 0 else "S"
    lon_hem = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{lat_hem}{abs(lat):02d}_00_{lon_hem}{abs(lon):03d}_00_DEM"


def download_tile(lat: int, lon: int, cache_dir: Path, resolution: str = "90m",
                  timeout: int = 120, retries: int = 3) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    if resolution == "90m":
        name = tile_name_90m(lat, lon)
        bucket = BUCKET_90M
    else:
        name = tile_name_30m(lat, lon)
        bucket = BUCKET_30M
    
    local_path = cache_dir / f"{name}.tif"
    if local_path.exists():
        return local_path
    
    url = f"{bucket}/{name}/{name}.tif"
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TrackLab/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
                local_path.write_bytes(data)
                return local_path
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < retries - 1:
                import time
                time.sleep(2 ** attempt)
        except Exception as e:
            if attempt < retries - 1:
                import time
                time.sleep(2 ** attempt)
    return None


def download_japan_dem(cache_dir: Path, resolution: str = "90m",
                       tiles: list = None) -> list[Path]:
    """Download all Japan DEM tiles, return list of successfully downloaded paths."""
    if tiles is None:
        tiles = JAPAN_TILES
    
    paths = []
    for lat, lon in tiles:
        path = download_tile(lat, lon, cache_dir, resolution)
        if path:
            paths.append(path)
            print(f"  Downloaded: {path.name}")
        else:
            print(f"  Skipped (no data): N{lat:02d}_E{lon:03d}")
    return paths


def tiles_for_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> list:
    import math
    tiles = []
    for lat in range(math.floor(min_lat), math.ceil(max_lat)):
        for lon in range(math.floor(min_lon), math.ceil(max_lon)):
            tiles.append((lat, lon))
    return tiles


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download Japan DEM tiles")
    parser.add_argument("--output", "-o", default="cache/japan_dem",
                        help="Output directory for tiles")
    parser.add_argument("--resolution", choices=["90m", "30m"], default="90m",
                        help="DEM resolution (default: 90m for coarse scan)")
    args = parser.parse_args()
    
    cache = Path(args.output)
    print(f"Downloading {args.resolution} DEM tiles for Japan...")
    paths = download_japan_dem(cache, args.resolution)
    print(f"\nDownloaded {len(paths)} tiles to {cache}")
