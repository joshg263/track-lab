#!/usr/bin/env python3
"""
Export terrain and track data for the 3D viewer.

Creates:
- terrain.json: Heightmap grid + metadata
- track.json: Centreline with elevation profile
"""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds


def export_terrain(dem_paths: list, bbox: tuple, output_path: str, 
                   max_resolution: int = 200) -> dict:
    """
    Export terrain heightmap as JSON grid.
    bbox: (min_lon, min_lat, max_lon, max_lat)
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    
    with rasterio.open(dem_paths[0]) as src:
        window = from_bounds(min_lon, min_lat, max_lon, max_lat, src.transform)
        
        row_off = int(max(0, window.row_off))
        col_off = int(max(0, window.col_off))
        height = int(min(window.height, src.height - row_off))
        width = int(min(window.width, src.width - col_off))
        
        data = src.read(1, window=((row_off, row_off + height), 
                                    (col_off, col_off + width)))
        
        step = max(1, max(height, width) // max_resolution)
        data = data[::step, ::step]
        
        actual_transform = rasterio.transform.from_bounds(
            min_lon, min_lat, max_lon, max_lat, 
            data.shape[1], data.shape[0]
        )
    
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    lat_scale = 111320
    lon_scale = 111320 * np.cos(np.radians(center_lat))
    
    width_m = (max_lon - min_lon) * lon_scale
    height_m = (max_lat - min_lat) * lat_scale
    
    data = np.nan_to_num(data, nan=float(np.nanmean(data)))
    
    terrain = {
        "type": "heightmap",
        "bbox": {"min_lon": min_lon, "min_lat": min_lat, 
                 "max_lon": max_lon, "max_lat": max_lat},
        "center": {"lon": center_lon, "lat": center_lat},
        "size_m": {"width": round(width_m, 1), "height": round(height_m, 1)},
        "grid": {
            "rows": data.shape[0],
            "cols": data.shape[1],
            "min_elev": round(float(np.min(data)), 1),
            "max_elev": round(float(np.max(data)), 1),
        },
        "heights": data.tolist()
    }
    
    with open(output_path, "w") as f:
        json.dump(terrain, f)
    
    print(f"Terrain exported: {data.shape[1]}x{data.shape[0]} grid")
    print(f"  Size: {width_m:.0f}m x {height_m:.0f}m")
    print(f"  Elevation: {terrain['grid']['min_elev']}m - {terrain['grid']['max_elev']}m")
    print(f"  File: {output_path} ({Path(output_path).stat().st_size / 1024:.1f} KB)")
    
    return terrain


def export_track(analysis_json: str, output_path: str) -> dict:
    """Export track data for viewer."""
    with open(analysis_json) as f:
        analysis = json.load(f)
    
    profile = analysis["profile"]
    coords = profile["coords"]
    elevations = profile["elevations_m"]
    distances = profile["distances_m"]
    gradients = profile["gradients_pct"]
    curvature = profile["curvature"]
    
    center_lat = sum(c[1] for c in coords) / len(coords)
    center_lon = sum(c[0] for c in coords) / len(coords)
    lat_scale = 111320
    lon_scale = 111320 * np.cos(np.radians(center_lat))
    
    points_3d = []
    for i, (lon, lat) in enumerate(coords):
        x = (lon - center_lon) * lon_scale
        y = (lat - center_lat) * lat_scale
        z = elevations[i]
        points_3d.append({
            "x": round(x, 2),
            "y": round(y, 2),
            "z": round(z, 2),
            "distance": round(distances[i], 1),
            "gradient": round(gradients[i], 2),
            "curvature": round(curvature[i], 6)
        })
    
    track = {
        "type": "track_profile",
        "center": {"lon": center_lon, "lat": center_lat},
        "stats": {
            "length_m": analysis["length_m"],
            "min_elevation_m": analysis["min_elevation_m"],
            "max_elevation_m": analysis["max_elevation_m"],
            "elevation_range_m": analysis["elevation_range_m"],
            "total_elevation_change_m": analysis["total_elevation_change_m"],
            "max_uphill_pct": analysis["max_uphill_gradient_pct"],
            "max_downhill_pct": analysis["max_downhill_gradient_pct"],
        },
        "corners": analysis["corners"],
        "points": points_3d
    }
    
    with open(output_path, "w") as f:
        json.dump(track, f)
    
    print(f"Track exported: {len(points_3d)} points")
    print(f"  File: {output_path} ({Path(output_path).stat().st_size / 1024:.1f} KB)")
    
    return track


def main():
    parser = argparse.ArgumentParser(description="Export data for 3D viewer")
    parser.add_argument("--analysis", required=True, help="Analysis JSON from analyze_track.py")
    parser.add_argument("--dem", nargs="+", required=True, help="DEM GeoTIFF file(s)")
    parser.add_argument("--buffer", type=float, default=0.015, 
                        help="Buffer around track in degrees (default: 0.015 ≈ 1.5km)")
    parser.add_argument("--resolution", type=int, default=200,
                        help="Max terrain grid resolution (default: 200)")
    parser.add_argument("--output-dir", default="viewer/data", help="Output directory")
    args = parser.parse_args()
    
    with open(args.analysis) as f:
        analysis = json.load(f)
    
    coords = analysis["profile"]["coords"]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    bbox = (
        min(lons) - args.buffer,
        min(lats) - args.buffer,
        max(lons) + args.buffer,
        max(lats) + args.buffer
    )
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("Exporting terrain...")
    export_terrain(args.dem, bbox, str(output_dir / "terrain.json"), args.resolution)
    
    print("\nExporting track...")
    export_track(args.analysis, str(output_dir / "track.json"))


if __name__ == "__main__":
    main()
