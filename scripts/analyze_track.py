#!/usr/bin/env python3
"""
Analyze track centreline draped over DEM.

Computes:
- Total length (geodesic)
- Min/max elevation and total elevation change
- Steepest uphill and downhill gradients
- Tightest corner radii with positions
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod
from road_profile import (
    GRADIENT_WINDOW_M,
    ROAD_PROFILE_SMOOTH_M,
    grade_profile,
    mean_sample_spacing,
)

from corner_detection import (
    RADIUS_THRESHOLD_M,
    compute_curvature_lonlat,
    find_corners,
)


def load_centreline(geojson_path: str, target_spacing_m: float = 5.0) -> np.ndarray:
    """
    Load centreline coordinates from GeoJSON and interpolate to target spacing.
    Returns Nx2 array of [lon, lat].
    """
    with open(geojson_path) as f:
        gj = json.load(f)
    
    coords = None
    for feature in gj.get("features", [gj]):
        geom = feature.get("geometry", feature)
        if geom["type"] == "LineString":
            coords = np.array(geom["coordinates"])
            break
    
    if coords is None:
        raise ValueError("No LineString found in GeoJSON")
    
    center_lat = np.mean(coords[:, 1])
    lat_scale = 111320
    lon_scale = 111320 * np.cos(np.radians(center_lat))
    
    x = coords[:, 0] * lon_scale
    y = coords[:, 1] * lat_scale
    
    dists = np.zeros(len(coords))
    for i in range(1, len(coords)):
        dists[i] = dists[i-1] + np.sqrt((x[i]-x[i-1])**2 + (y[i]-y[i-1])**2)
    
    total_dist = dists[-1]
    n_points = max(len(coords), int(total_dist / target_spacing_m))
    
    new_dists = np.linspace(0, total_dist, n_points)
    new_lons = np.interp(new_dists, dists, coords[:, 0])
    new_lats = np.interp(new_dists, dists, coords[:, 1])
    
    print(f"  Interpolated {len(coords)} -> {n_points} points (≈{target_spacing_m:.0f}m spacing)")
    
    return np.column_stack([new_lons, new_lats])


def sample_elevations(coords: np.ndarray, dem_paths: list) -> np.ndarray:
    """
    Sample elevations from DEM tiles at each coordinate.
    Returns array of elevations in meters.
    """
    elevations = np.full(len(coords), np.nan)
    
    for dem_path in dem_paths:
        with rasterio.open(dem_path) as src:
            for i, (lon, lat) in enumerate(coords):
                if not np.isnan(elevations[i]):
                    continue
                try:
                    row, col = src.index(lon, lat)
                    if 0 <= row < src.height and 0 <= col < src.width:
                        elev = src.read(1, window=((row, row+1), (col, col+1)))[0, 0]
                        if elev != src.nodata:
                            elevations[i] = elev
                except (IndexError, ValueError):
                    continue
    
    if np.any(np.isnan(elevations)):
        n_missing = np.sum(np.isnan(elevations))
        print(f"Warning: {n_missing} points outside DEM coverage, interpolating...")
        mask = ~np.isnan(elevations)
        elevations = np.interp(
            np.arange(len(elevations)),
            np.where(mask)[0],
            elevations[mask]
        )
    
    return elevations


def compute_distances(coords: np.ndarray) -> tuple:
    """
    Compute geodesic distances between consecutive points.
    Returns (cumulative_distances, segment_distances).
    """
    geod = Geod(ellps="WGS84")
    
    segment_distances = []
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        _, _, dist = geod.inv(lon1, lat1, lon2, lat2)
        segment_distances.append(dist)
    
    cumulative = np.zeros(len(coords))
    cumulative[1:] = np.cumsum(segment_distances)
    
    return cumulative, np.array(segment_distances)


def analyze(
    geojson_path: str,
    dem_paths: list,
    smooth_sigma_m: float = ROAD_PROFILE_SMOOTH_M,
    gradient_window_m: float = GRADIENT_WINDOW_M,
) -> dict:
    """Run full analysis and return results dict."""
    print(f"Loading centreline from {geojson_path}")
    coords = load_centreline(geojson_path)
    print(f"  {len(coords)} points")
    
    print("Computing distances...")
    distances, segments = compute_distances(coords)
    total_length = distances[-1]
    
    closing_gap = np.linalg.norm([
        (coords[-1][0] - coords[0][0]) * 111320 * np.cos(np.radians(coords[0][1])),
        (coords[-1][1] - coords[0][1]) * 111320
    ])
    total_length_closed = total_length + closing_gap
    print(f"  Total length: {total_length:.1f}m (+ {closing_gap:.1f}m closing gap = {total_length_closed:.1f}m)")
    
    print("Sampling elevations from DEM...")
    elevations_raw = sample_elevations(coords, dem_paths)
    spacing_m = mean_sample_spacing(distances)
    elevations, gradients = grade_profile(
        distances,
        elevations_raw,
        spacing_m,
        smooth_sigma_m=smooth_sigma_m,
        window_m=gradient_window_m,
    )
    print(f"  Raw elevation range: {elevations_raw.min():.1f}m - {elevations_raw.max():.1f}m")
    print(
        f"  Graded profile (σ={smooth_sigma_m}m, {gradient_window_m:.0f}m gradient window): "
        f"{elevations.min():.1f}m - {elevations.max():.1f}m"
    )
    
    max_up_idx = np.argmax(gradients)
    max_down_idx = np.argmin(gradients)
    
    print("Computing curvature and finding corners...")
    curvature = compute_curvature_lonlat(coords, distances)
    corners = find_corners(curvature, distances)
    
    deltas = np.abs(np.diff(elevations))
    total_elevation_change = np.sum(deltas)
    
    corner_radii = [r for _, r in corners]
    min_corner_radius = min(corner_radii) if corner_radii else None
    
    results = {
        "centreline_file": str(geojson_path),
        "num_points": len(coords),
        "length_m": round(total_length_closed, 1),
        "min_elevation_m": round(float(elevations.min()), 1),
        "max_elevation_m": round(float(elevations.max()), 1),
        "elevation_range_m": round(float(elevations.max() - elevations.min()), 1),
        "total_elevation_change_m": round(float(total_elevation_change), 1),
        "max_uphill_gradient_pct": round(float(gradients[max_up_idx]), 2),
        "max_uphill_position_m": round(float(distances[max_up_idx]), 1),
        "max_downhill_gradient_pct": round(float(gradients[max_down_idx]), 2),
        "max_downhill_position_m": round(float(distances[max_down_idx]), 1),
        "road_profile_smooth_m": smooth_sigma_m,
        "gradient_window_m": gradient_window_m,
        "corner_count": len(corners),
        "min_corner_radius_m": round(min_corner_radius, 1) if min_corner_radius else None,
        "corners": [
            {"position_m": round(d, 1), "radius_m": round(r, 1)}
            for d, r in corners
        ],
        "profile": {
            "distances_m": distances.tolist(),
            "elevations_m": elevations.tolist(),
            "elevations_raw_m": elevations_raw.tolist(),
            "gradients_pct": gradients.tolist(),
            "curvature": curvature.tolist(),
            "coords": coords.tolist()
        }
    }
    
    return results


def print_results(results: dict):
    """Print human-readable summary."""
    print("\n" + "=" * 60)
    print("TRACK ANALYSIS RESULTS")
    print("=" * 60)
    
    print(f"\nLength:           {results['length_m']:.1f} m ({results['length_m']/1000:.3f} km)")
    print(f"Elevation range:  {results['min_elevation_m']:.1f}m - {results['max_elevation_m']:.1f}m")
    print(f"                  ({results['elevation_range_m']:.1f}m total range)")
    print(f"Total climb/drop: {results['total_elevation_change_m']:.1f}m (sum of all ups and downs)")
    
    print(f"\nSteepest uphill:   {results['max_uphill_gradient_pct']:+.2f}% at {results['max_uphill_position_m']:.0f}m")
    print(f"Steepest downhill: {results['max_downhill_gradient_pct']:+.2f}% at {results['max_downhill_position_m']:.0f}m")
    
    corner_count = results['corner_count']
    min_radius = results['min_corner_radius_m']
    print(f"\nCorners:          {corner_count} (radius threshold: {RADIUS_THRESHOLD_M:.0f}m)")
    if min_radius is not None:
        print(f"Min corner radius: {min_radius:.1f}m")
    else:
        print(f"Min corner radius: N/A (no corners found)")
    
    if results['corners']:
        print(f"\nCorner details (by position):")
        for i, corner in enumerate(results['corners'][:10], 1):
            print(f"  {i:2d}. R={corner['radius_m']:6.1f}m at {corner['position_m']:.0f}m")
    
    print(f"\nNote: Curvature computed with ±20m arc-length window.")
    print(f"      Corners: contiguous runs with R<{RADIUS_THRESHOLD_M:.0f}m, merged if gap<30m, dropped if <20m.")
    print(
        f"      Gradients from graded profile (σ={results['road_profile_smooth_m']}m, "
        f"{results['gradient_window_m']:.0f}m window)."
    )
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Analyze track over DEM")
    parser.add_argument("geojson", help="Centreline GeoJSON file")
    parser.add_argument("dem", nargs="+", help="DEM GeoTIFF file(s)")
    parser.add_argument("--output", "-o", help="Output JSON file")
    parser.add_argument(
        "--smooth-m",
        type=float,
        default=ROAD_PROFILE_SMOOTH_M,
        help=f"Road profile low-pass σ in metres (default: {ROAD_PROFILE_SMOOTH_M})",
    )
    parser.add_argument(
        "--gradient-window-m",
        type=float,
        default=GRADIENT_WINDOW_M,
        help=f"Rolling gradient window in metres (default: {GRADIENT_WINDOW_M})",
    )
    args = parser.parse_args()
    
    results = analyze(
        args.geojson,
        args.dem,
        smooth_sigma_m=args.smooth_m,
        gradient_window_m=args.gradient_window_m,
    )
    print_results(results)
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
