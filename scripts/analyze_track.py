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
from scipy.ndimage import gaussian_filter1d


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


def compute_gradients(distances: np.ndarray, elevations: np.ndarray, 
                      window_m: float = 100) -> np.ndarray:
    """
    Compute gradient (%) at each point using a rolling window.
    Positive = uphill, negative = downhill.
    
    Uses 100m window by default for stable gradient estimates
    that match how gradients are typically reported for race tracks.
    """
    n = len(distances)
    gradients = np.zeros(n)
    
    for i in range(n):
        d0 = max(0, distances[i] - window_m / 2)
        d1 = min(distances[-1], distances[i] + window_m / 2)
        
        i0 = np.searchsorted(distances, d0)
        i1 = np.searchsorted(distances, d1)
        i1 = min(i1, n - 1)
        
        if i1 > i0:
            delta_elev = elevations[i1] - elevations[i0]
            delta_dist = distances[i1] - distances[i0]
            if delta_dist > 0:
                gradients[i] = (delta_elev / delta_dist) * 100
    
    return gradients


def compute_curvature(coords: np.ndarray, distances: np.ndarray, 
                      half_window_m: float = 20.0) -> np.ndarray:
    """
    Compute curvature (1/radius in 1/m) at each point using arc-length windowing.
    
    Uses three-point radius calculation over a fixed arc-length window of ±half_window_m.
    This is more stable than point-by-point derivatives for noisy or densely sampled polylines.
    """
    n = len(coords)
    
    center_lat = np.mean(coords[:, 1])
    center_lon = np.mean(coords[:, 0])
    lat_scale = 111320  # meters per degree latitude
    lon_scale = 111320 * np.cos(np.radians(center_lat))
    
    x = (coords[:, 0] - center_lon) * lon_scale
    y = (coords[:, 1] - center_lat) * lat_scale
    
    curvature = np.zeros(n)
    
    for i in range(n):
        d_i = distances[i]
        d_back = d_i - half_window_m
        d_fwd = d_i + half_window_m
        
        i_back = np.searchsorted(distances, d_back, side='left')
        i_fwd = np.searchsorted(distances, d_fwd, side='right') - 1
        
        i_back = max(0, i_back)
        i_fwd = min(n - 1, i_fwd)
        
        if i_fwd <= i_back or i == i_back or i == i_fwd:
            curvature[i] = 0.0
            continue
        
        x1, y1 = x[i_back], y[i_back]
        x2, y2 = x[i], y[i]
        x3, y3 = x[i_fwd], y[i_fwd]
        
        a = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        b = np.sqrt((x3 - x2)**2 + (y3 - y2)**2)
        c = np.sqrt((x3 - x1)**2 + (y3 - y1)**2)
        
        if a < 1e-6 or b < 1e-6 or c < 1e-6:
            curvature[i] = 0.0
            continue
        
        s = (a + b + c) / 2
        area_sq = s * (s - a) * (s - b) * (s - c)
        
        if area_sq <= 0:
            curvature[i] = 0.0
        else:
            area = np.sqrt(area_sq)
            radius = (a * b * c) / (4 * area)
            curvature[i] = 1.0 / radius if radius > 1e-6 else 0.0
    
    return curvature


def find_corners(curvature: np.ndarray, distances: np.ndarray, 
                 radius_threshold_m: float = 175.0, merge_gap_m: float = 30.0,
                 min_run_length_m: float = 20.0) -> list:
    """
    Find corners as contiguous runs where radius is below threshold.
    
    A corner is a contiguous run where radius < radius_threshold_m (~175m, midpoint of 150-200m).
    Runs separated by < merge_gap_m are merged. Runs shorter than min_run_length_m are dropped.
    Returns list of (distance_m, radius_m) where distance is the center of the run
    and radius is the minimum radius within that run.
    """
    n = len(curvature)
    with np.errstate(divide='ignore'):
        radius = np.where(curvature > 1e-10, 1.0 / curvature, np.inf)
    
    runs = []
    in_run = False
    run_start = 0
    
    for i in range(n):
        if radius[i] < radius_threshold_m:
            if not in_run:
                in_run = True
                run_start = i
        else:
            if in_run:
                runs.append((run_start, i - 1))
                in_run = False
    
    if in_run:
        runs.append((run_start, n - 1))
    
    if not runs:
        return []
    
    merged = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        gap = distances[start] - distances[prev_end]
        if gap < merge_gap_m:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    
    corners = []
    for start, end in merged:
        run_length = distances[end] - distances[start]
        if run_length < min_run_length_m:
            continue
        
        run_radii = radius[start:end+1]
        valid_radii = run_radii[run_radii < np.inf]
        if len(valid_radii) == 0:
            continue
        
        min_radius = float(np.min(valid_radii))
        center_dist = (distances[start] + distances[end]) / 2
        corners.append((center_dist, min_radius))
    
    corners.sort(key=lambda x: x[0])
    return corners


def smooth_elevations(elevations: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    """Apply Gaussian smoothing to reduce DEM noise in elevation profile."""
    return gaussian_filter1d(elevations, sigma)


def analyze(geojson_path: str, dem_paths: list, smooth_sigma: float = 2.0) -> dict:
    """Run full analysis and return results dict."""
    print(f"Loading centreline from {geojson_path}")
    coords = load_centreline(geojson_path)
    print(f"  {len(coords)} points")
    
    print("Sampling elevations from DEM...")
    elevations_raw = sample_elevations(coords, dem_paths)
    elevations = smooth_elevations(elevations_raw, smooth_sigma)
    print(f"  Raw elevation range: {elevations_raw.min():.1f}m - {elevations_raw.max():.1f}m")
    print(f"  Smoothed (σ={smooth_sigma}): {elevations.min():.1f}m - {elevations.max():.1f}m")
    
    print("Computing distances...")
    distances, segments = compute_distances(coords)
    total_length = distances[-1]
    
    closing_gap = np.linalg.norm([
        (coords[-1][0] - coords[0][0]) * 111320 * np.cos(np.radians(coords[0][1])),
        (coords[-1][1] - coords[0][1]) * 111320
    ])
    total_length_closed = total_length + closing_gap
    print(f"  Total length: {total_length:.1f}m (+ {closing_gap:.1f}m closing gap = {total_length_closed:.1f}m)")
    
    print("Computing gradients...")
    gradients = compute_gradients(distances, elevations)
    max_up_idx = np.argmax(gradients)
    max_down_idx = np.argmin(gradients)
    
    print("Computing curvature and finding corners...")
    curvature = compute_curvature(coords, distances)
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
        "smoothing_sigma": smooth_sigma,
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
    print(f"\nCorners:          {corner_count} (radius threshold: 175m)")
    if min_radius is not None:
        print(f"Min corner radius: {min_radius:.1f}m")
    else:
        print(f"Min corner radius: N/A (no corners found)")
    
    if results['corners']:
        print(f"\nCorner details (by position):")
        for i, corner in enumerate(results['corners'][:10], 1):
            print(f"  {i:2d}. R={corner['radius_m']:6.1f}m at {corner['position_m']:.0f}m")
    
    print(f"\nNote: Curvature computed with ±20m arc-length window.")
    print(f"      Corners: contiguous runs with R<175m, merged if gap<30m, dropped if <20m.")
    print(f"      Elevations smoothed with σ={results['smoothing_sigma']} to reduce DEM noise.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Analyze track over DEM")
    parser.add_argument("geojson", help="Centreline GeoJSON file")
    parser.add_argument("dem", nargs="+", help="DEM GeoTIFF file(s)")
    parser.add_argument("--output", "-o", help="Output JSON file")
    parser.add_argument("--smooth", type=float, default=2.0,
                        help="Gaussian smoothing sigma for elevations (default: 2.0)")
    args = parser.parse_args()
    
    results = analyze(args.geojson, args.dem, args.smooth)
    print_results(results)
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
