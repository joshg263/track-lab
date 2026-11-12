#!/usr/bin/env python3
"""
Find bowl-shaped depressions in DEM for potential F1 circuit sites.

Uses multi-scale relief analysis to identify enclosed valleys and natural
amphitheaters suitable for spectator viewing.

Algorithm:
1. Compute topographic position index (TPI) at multiple scales
2. Find local minima (depressions) in smoothed DEM
3. Measure bowl properties: diameter, relief, rim slopes, floor flatness
4. Filter by site criteria and rank by enclosure/viewshed potential
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.ndimage import label, find_objects


@dataclass
class Bowl:
    """A candidate bowl-shaped depression."""
    id: int
    lat: float
    lon: float
    floor_elev_m: float
    rim_elev_m: float
    relief_m: float
    diameter_m: float
    floor_flat_pct: float
    rim_slope_range: tuple[float, float]
    enclosure_pct: float
    pixel_bbox: tuple[int, int, int, int]


def compute_tpi(dem: np.ndarray, radius_pixels: int) -> np.ndarray:
    """Compute Topographic Position Index: elevation relative to neighborhood mean."""
    kernel_size = 2 * radius_pixels + 1
    kernel = np.ones((kernel_size, kernel_size))
    kernel[radius_pixels, radius_pixels] = 0
    kernel = kernel / kernel.sum()
    
    neighborhood_mean = ndimage.convolve(dem, kernel, mode='reflect')
    return dem - neighborhood_mean


def compute_slope(dem: np.ndarray, cell_size_m: float) -> np.ndarray:
    """Compute slope in degrees from DEM."""
    dy, dx = np.gradient(dem, cell_size_m)
    slope_rad = np.arctan(np.sqrt(dx**2 + dy**2))
    return np.degrees(slope_rad)


def find_depressions(dem: np.ndarray, min_depth_m: float = 30,
                     smoothing_sigma: float = 3) -> tuple[np.ndarray, int]:
    """
    Find distinct depression regions using multi-scale TPI.
    Returns labeled array and number of features.
    """
    dem_smooth = ndimage.gaussian_filter(dem, sigma=smoothing_sigma)
    
    tpi_small = compute_tpi(dem_smooth, radius_pixels=5)
    tpi_medium = compute_tpi(dem_smooth, radius_pixels=15)
    tpi_large = compute_tpi(dem_smooth, radius_pixels=30)
    
    depression_score = (tpi_small < -10) & (tpi_medium < -20) & (tpi_large < -min_depth_m / 2)
    
    depression_score = ndimage.binary_opening(depression_score, iterations=2)
    depression_score = ndimage.binary_closing(depression_score, iterations=3)
    
    labeled, num_features = label(depression_score)
    return labeled, num_features


def analyze_bowl(dem: np.ndarray, labeled: np.ndarray, bowl_id: int,
                 cell_size_m: float, lat_offset: float, lon_offset: float,
                 lat_scale: float, lon_scale: float) -> Bowl | None:
    """Analyze a single labeled depression region."""
    mask = labeled == bowl_id
    rows, cols = np.where(mask)
    
    if len(rows) < 10:
        return None
    
    r_min, r_max = rows.min(), rows.max()
    c_min, c_max = cols.min(), cols.max()
    
    height_pixels = r_max - r_min
    width_pixels = c_max - c_min
    
    diameter_y = height_pixels * cell_size_m
    diameter_x = width_pixels * cell_size_m
    diameter_m = (diameter_x + diameter_y) / 2
    
    if diameter_m < 1500 or diameter_m > 4000:
        return None
    
    floor_elevs = dem[mask]
    floor_elev_m = float(np.percentile(floor_elevs, 10))
    
    rim_band = ndimage.binary_dilation(mask, iterations=10) & ~mask
    if rim_band.sum() < 10:
        return None
    
    rim_elevs = dem[rim_band]
    rim_elev_m = float(np.percentile(rim_elevs, 75))
    
    relief_m = rim_elev_m - floor_elev_m
    if relief_m < 40 or relief_m > 200:
        return None
    
    slope = compute_slope(dem, cell_size_m)
    floor_expanded = ndimage.binary_dilation(mask, iterations=3)
    floor_slopes = slope[floor_expanded & mask]
    floor_flat_pct = float((floor_slopes < 5).sum() / max(len(floor_slopes), 1) * 100)
    
    rim_slopes = slope[rim_band]
    rim_slope_min = float(np.percentile(rim_slopes, 25))
    rim_slope_max = float(np.percentile(rim_slopes, 75))
    
    good_rim = (rim_slopes >= 8) & (rim_slopes <= 35)
    enclosure_pct = float(good_rim.sum() / max(len(rim_slopes), 1) * 100)
    
    center_row = (r_min + r_max) / 2
    center_col = (c_min + c_max) / 2
    lat = lat_offset - center_row * lat_scale
    lon = lon_offset + center_col * lon_scale
    
    return Bowl(
        id=bowl_id,
        lat=round(lat, 5),
        lon=round(lon, 5),
        floor_elev_m=round(floor_elev_m, 1),
        rim_elev_m=round(rim_elev_m, 1),
        relief_m=round(relief_m, 1),
        diameter_m=round(diameter_m, 0),
        floor_flat_pct=round(floor_flat_pct, 1),
        rim_slope_range=(round(rim_slope_min, 1), round(rim_slope_max, 1)),
        enclosure_pct=round(enclosure_pct, 1),
        pixel_bbox=(r_min, r_max, c_min, c_max),
    )


def find_bowls_in_tile(dem_path: str, min_relief_m: float = 50,
                       max_relief_m: float = 150) -> list[Bowl]:
    """Find all candidate bowls in a single DEM tile."""
    import rasterio
    
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    nodata_pct = np.isnan(dem).sum() / dem.size * 100
    if nodata_pct > 50:
        return []
    
    dem = np.nan_to_num(dem, nan=0)
    
    cell_size_m = abs(transform.a) * 111320 * math.cos(math.radians(transform.f - dem.shape[0] * abs(transform.e) / 2))
    
    labeled, num_features = find_depressions(dem, min_depth_m=min_relief_m * 0.6)
    
    bowls = []
    for i in range(1, num_features + 1):
        bowl = analyze_bowl(
            dem, labeled, i, cell_size_m,
            lat_offset=transform.f,
            lon_offset=transform.c,
            lat_scale=abs(transform.e),
            lon_scale=transform.a,
        )
        if bowl and min_relief_m <= bowl.relief_m <= max_relief_m:
            bowls.append(bowl)
    
    return bowls


def scan_japan_for_bowls(dem_dir: str, output_json: str = None) -> list[Bowl]:
    """Scan all Japan DEM tiles for candidate bowls."""
    import json
    from pathlib import Path
    
    dem_dir = Path(dem_dir)
    tiles = sorted(dem_dir.glob("*.tif"))
    
    all_bowls = []
    bowl_id_offset = 0
    
    for tile_path in tiles:
        print(f"  Scanning {tile_path.name}...")
        bowls = find_bowls_in_tile(str(tile_path))
        
        for bowl in bowls:
            bowl.id += bowl_id_offset
            all_bowls.append(bowl)
        
        bowl_id_offset += len(bowls) + 100
        
        if bowls:
            print(f"    Found {len(bowls)} candidates")
    
    all_bowls.sort(key=lambda b: -b.enclosure_pct)
    
    if output_json:
        data = [
            {
                "id": b.id,
                "lat": b.lat,
                "lon": b.lon,
                "floor_elev_m": b.floor_elev_m,
                "rim_elev_m": b.rim_elev_m,
                "relief_m": b.relief_m,
                "diameter_m": b.diameter_m,
                "floor_flat_pct": b.floor_flat_pct,
                "rim_slope_range": list(b.rim_slope_range),
                "enclosure_pct": b.enclosure_pct,
            }
            for b in all_bowls
        ]
        with open(output_json, "w") as f:
            json.dump({"bowls": data, "count": len(data)}, f, indent=2)
    
    return all_bowls


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Find bowl-shaped depressions in Japan DEM")
    parser.add_argument("--dem-dir", default="cache/japan_dem",
                        help="Directory with DEM tiles")
    parser.add_argument("--output", "-o", default="cache/japan_bowls.json",
                        help="Output JSON file")
    args = parser.parse_args()
    
    print("Scanning Japan for potential circuit sites...")
    bowls = scan_japan_for_bowls(args.dem_dir, args.output)
    print(f"\nFound {len(bowls)} candidate bowls")
    
    print("\nTop 10 by enclosure:")
    for b in bowls[:10]:
        print(f"  {b.lat:.4f}, {b.lon:.4f}: {b.diameter_m:.0f}m diameter, "
              f"{b.relief_m:.0f}m relief, {b.enclosure_pct:.0f}% enclosed")
