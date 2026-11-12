#!/usr/bin/env python3
"""
Compute viewshed from a seat position over the track.

For a given observer position (lat, lon, eye_height), computes which portions
of the track centreline are visible using line-of-sight over the DEM.

Returns visibility array, visible percentage, and which corners are visible.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio


# Default preset seats for Red Bull Ring
# Main grandstand is on the south side of the start/finish straight
PRESET_SEATS = {
    "main_grandstand": {
        "name": "Main Grandstand",
        "lon": 14.7630,
        "lat": 47.2194,
        "eye_height_m": 10.0,
        "description": "Primary grandstand overlooking start/finish straight"
    },
    "turn1_grandstand": {
        "name": "Turn 1 Grandstand", 
        "lon": 14.7615,
        "lat": 47.2190,
        "eye_height_m": 10.0,
        "description": "Grandstand at Turn 1 (Niki Lauda Kurve)"
    }
}

# A position with poor visibility (behind hills to the north)
POOR_VISIBILITY_SEAT = {
    "name": "Behind Hills (North)",
    "lon": 14.758,
    "lat": 47.229,
    "eye_height_m": 10.0,
    "description": "Position behind northern hills with limited track visibility"
}


def load_dem(dem_path: str) -> tuple:
    """Load DEM and return (data, bounds, transform)."""
    with rasterio.open(dem_path) as src:
        data = src.read(1)
        bounds = src.bounds
        transform = src.transform
        nodata = src.nodata
    
    if nodata is not None:
        data = np.where(data == nodata, np.nan, data)
    
    return data, bounds, transform


def sample_elevation(dem_data: np.ndarray, bounds, transform, lon: float, lat: float) -> float:
    """Sample elevation at a point, with bilinear interpolation."""
    if not (bounds.left <= lon <= bounds.right and bounds.bottom <= lat <= bounds.top):
        return np.nan
    
    col_f = (lon - bounds.left) / (bounds.right - bounds.left) * (dem_data.shape[1] - 1)
    row_f = (bounds.top - lat) / (bounds.top - bounds.bottom) * (dem_data.shape[0] - 1)
    
    col0, row0 = int(col_f), int(row_f)
    col1, row1 = min(col0 + 1, dem_data.shape[1] - 1), min(row0 + 1, dem_data.shape[0] - 1)
    
    dc, dr = col_f - col0, row_f - row0
    
    e00 = dem_data[row0, col0]
    e01 = dem_data[row0, col1]
    e10 = dem_data[row1, col0]
    e11 = dem_data[row1, col1]
    
    elev = (e00 * (1 - dc) * (1 - dr) + 
            e01 * dc * (1 - dr) +
            e10 * (1 - dc) * dr +
            e11 * dc * dr)
    
    return elev


def compute_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Compute approximate distance in meters between two points."""
    center_lat = (lat1 + lat2) / 2
    lat_scale = 111320
    lon_scale = 111320 * math.cos(math.radians(center_lat))
    
    dx = (lon2 - lon1) * lon_scale
    dy = (lat2 - lat1) * lat_scale
    
    return math.sqrt(dx * dx + dy * dy)


def check_line_of_sight(dem_data: np.ndarray, bounds, transform,
                        obs_lon: float, obs_lat: float, obs_elev: float,
                        target_lon: float, target_lat: float, target_elev: float,
                        num_samples: int = 50) -> bool:
    """
    Check if there's clear line of sight from observer to target.
    
    Returns True if visible (no terrain blocks the view).
    """
    target_elev_adjusted = target_elev + 0.5
    
    for i in range(1, num_samples):
        t = i / num_samples
        
        sample_lon = obs_lon + t * (target_lon - obs_lon)
        sample_lat = obs_lat + t * (target_lat - obs_lat)
        
        terrain_elev = sample_elevation(dem_data, bounds, transform, sample_lon, sample_lat)
        if np.isnan(terrain_elev):
            continue
        
        los_elev = obs_elev + t * (target_elev_adjusted - obs_elev)
        
        if terrain_elev > los_elev:
            return False
    
    return True


def compute_viewshed(dem_path: str, track_coords: list, seat_lon: float, 
                     seat_lat: float, eye_height_m: float = 10.0,
                     track_height_m: float = 0.5) -> dict:
    """
    Compute viewshed from a seat position.
    
    Args:
        dem_path: Path to DEM GeoTIFF
        track_coords: List of [lon, lat] coordinates along the track
        seat_lon, seat_lat: Seat position in WGS84
        eye_height_m: Height of observer's eyes above ground
        track_height_m: Height of track surface above ground (for visibility calc)
    
    Returns:
        Dictionary with visibility array, stats, and visible corners
    """
    dem_data, bounds, transform = load_dem(dem_path)
    
    seat_ground_elev = sample_elevation(dem_data, bounds, transform, seat_lon, seat_lat)
    if np.isnan(seat_ground_elev):
        raise ValueError(f"Seat position ({seat_lon}, {seat_lat}) is outside DEM bounds")
    
    seat_eye_elev = seat_ground_elev + eye_height_m
    
    visibility = []
    distances = [0.0]
    
    for i, (lon, lat) in enumerate(track_coords):
        target_ground_elev = sample_elevation(dem_data, bounds, transform, lon, lat)
        if np.isnan(target_ground_elev):
            visibility.append(False)
        else:
            target_elev = target_ground_elev + track_height_m
            visible = check_line_of_sight(
                dem_data, bounds, transform,
                seat_lon, seat_lat, seat_eye_elev,
                lon, lat, target_elev
            )
            visibility.append(visible)
        
        if i > 0:
            d = compute_distance_m(track_coords[i-1][0], track_coords[i-1][1], lon, lat)
            distances.append(distances[-1] + d)
    
    visible_length = sum(
        distances[i+1] - distances[i] 
        for i in range(len(visibility) - 1) 
        if visibility[i] and visibility[i+1]
    )
    total_length = distances[-1]
    visible_pct = (visible_length / total_length * 100) if total_length > 0 else 0
    
    return {
        "seat": {
            "lon": seat_lon,
            "lat": seat_lat,
            "ground_elevation_m": round(seat_ground_elev, 1),
            "eye_elevation_m": round(seat_eye_elev, 1),
            "eye_height_m": eye_height_m
        },
        "visibility": visibility,
        "distances_m": distances,
        "visible_length_m": round(visible_length, 1),
        "total_length_m": round(total_length, 1),
        "visible_pct": round(visible_pct, 1)
    }


def find_visible_corners(visibility: list, distances: list, corners: list) -> list:
    """
    Determine which corners are visible from the seat.
    
    Args:
        visibility: Boolean array of track point visibility
        distances: Cumulative distance array for track points
        corners: List of corner dicts with position_m and radius_m
    
    Returns:
        List of corner dicts with added 'visible' field
    """
    corners_with_visibility = []
    
    for corner in corners:
        pos = corner["position_m"]
        
        idx = 0
        for i, d in enumerate(distances):
            if d >= pos:
                idx = i
                break
        
        window = 5
        start_idx = max(0, idx - window)
        end_idx = min(len(visibility), idx + window + 1)
        
        visible_count = sum(visibility[start_idx:end_idx])
        total_count = end_idx - start_idx
        
        corner_visible = visible_count >= total_count * 0.5
        
        corners_with_visibility.append({
            **corner,
            "visible": corner_visible,
            "visibility_ratio": visible_count / total_count if total_count > 0 else 0
        })
    
    return corners_with_visibility


def check_start_finish_visible(visibility: list, distances: list, 
                                track_length_m: float,
                                start_finish_range_m: tuple = (0, 400)) -> bool:
    """
    Check if the start/finish straight is visible.
    
    The start/finish straight is typically the first ~300-400m of the track.
    """
    start_m, end_m = start_finish_range_m
    
    visible_in_range = 0
    total_in_range = 0
    
    for i, d in enumerate(distances):
        if start_m <= d <= end_m:
            total_in_range += 1
            if visibility[i]:
                visible_in_range += 1
    
    if total_in_range == 0:
        return False
    
    return (visible_in_range / total_in_range) >= 0.5


def main():
    parser = argparse.ArgumentParser(description="Compute viewshed from a seat position")
    parser.add_argument("--dem", required=True, help="DEM GeoTIFF file")
    parser.add_argument("--centreline", required=True, help="Track centreline GeoJSON")
    parser.add_argument("--seat", help="Seat position as 'lat,lon' or preset name (main_grandstand)")
    parser.add_argument("--eye-height", type=float, default=10.0, 
                        help="Eye height above ground in meters (default: 10)")
    parser.add_argument("--analysis", help="Analysis JSON for corner data")
    parser.add_argument("--output", "-o", help="Output JSON file")
    parser.add_argument("--list-presets", action="store_true", help="List available preset seats")
    args = parser.parse_args()
    
    if args.list_presets:
        print("Available preset seats:")
        for key, seat in PRESET_SEATS.items():
            print(f"  {key}: {seat['name']}")
            print(f"    Position: {seat['lat']}, {seat['lon']}")
            print(f"    {seat['description']}")
        return
    
    with open(args.centreline) as f:
        gj = json.load(f)
    
    coords = None
    for feature in gj.get("features", [gj]):
        geom = feature.get("geometry", feature)
        if geom["type"] == "LineString":
            coords = geom["coordinates"]
            break
    
    if coords is None:
        raise ValueError("No LineString found in GeoJSON")
    
    if args.seat:
        if args.seat in PRESET_SEATS:
            seat = PRESET_SEATS[args.seat]
            seat_lon, seat_lat = seat["lon"], seat["lat"]
            eye_height = seat.get("eye_height_m", args.eye_height)
            seat_name = seat["name"]
        else:
            try:
                lat_str, lon_str = args.seat.split(",")
                seat_lat, seat_lon = float(lat_str.strip()), float(lon_str.strip())
                eye_height = args.eye_height
                seat_name = f"Custom ({seat_lat:.5f}, {seat_lon:.5f})"
            except ValueError:
                raise ValueError(f"Invalid seat format: {args.seat}. Use 'lat,lon' or a preset name.")
    else:
        seat = PRESET_SEATS["main_grandstand"]
        seat_lon, seat_lat = seat["lon"], seat["lat"]
        eye_height = seat.get("eye_height_m", args.eye_height)
        seat_name = seat["name"]
    
    print(f"Computing viewshed from: {seat_name}")
    print(f"  Position: {seat_lat:.5f}, {seat_lon:.5f}")
    print(f"  Eye height: {eye_height}m")
    
    result = compute_viewshed(
        args.dem, coords, seat_lon, seat_lat, eye_height
    )
    result["seat"]["name"] = seat_name
    
    corners = []
    if args.analysis:
        with open(args.analysis) as f:
            analysis = json.load(f)
        corners = analysis.get("corners", [])
    
    if corners:
        corners_visibility = find_visible_corners(
            result["visibility"], result["distances_m"], corners
        )
        result["corners"] = corners_visibility
        
        visible_corners = [c for c in corners_visibility if c["visible"]]
        print(f"\nVisible corners: {len(visible_corners)}/{len(corners_visibility)}")
        for c in visible_corners[:5]:
            print(f"  R={c['radius_m']:.0f}m @ {c['position_m']:.0f}m")
    
    sf_visible = check_start_finish_visible(
        result["visibility"], result["distances_m"], result["total_length_m"]
    )
    result["start_finish_visible"] = sf_visible
    
    print(f"\nTrack visibility: {result['visible_pct']:.1f}%")
    print(f"  Visible: {result['visible_length_m']:.0f}m / {result['total_length_m']:.0f}m")
    print(f"  Start/finish visible: {'Yes' if sf_visible else 'No'}")
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {args.output}")
    
    return result


if __name__ == "__main__":
    main()
