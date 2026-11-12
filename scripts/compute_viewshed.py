#!/usr/bin/env python3
"""
Compute viewshed from a seat position over the track.

For a given observer position (lat, lon, eye_height), computes which portions
of the track centreline are visible using line-of-sight over the surface
model: DEM + OSM buildings, raked grandstands and tree canopy
(see surface_model.py). Pass --no-obstructions for bare terrain.

Returns visibility array, visible percentage, and which corners are visible.
"""

import argparse
import json
import math
from functools import lru_cache

import numpy as np

from surface_model import (
    DEFAULT_OBSTRUCTIONS, EYE_ABOVE_TIER_M, Terrain, build_obstructions,
    grandstand_seat, visibility_from,
)

TERRAIN_BUFFER_DEG = 0.03

# Named presets are OSM grandstands (way ids); seats sit in the back row,
# eye 1.2 m above the stand tier. Every other OSM grandstand is added as
# "gs_<osm id>".
PRESET_GRANDSTANDS = {
    "main_grandstand": {
        "osm_id": 312030921,
        "name": "Main Grandstand (Start/Ziel)",
        "description": "Start/Ziel Tribüne, opposite the pits on the start/finish straight",
    },
    "turn1_grandstand": {
        "osm_id": 312030917,
        "name": "Turn 1 Grandstand (Steiermark)",
        "description": "Steiermark Tribüne at Turn 1 (Niki Lauda Kurve)",
    },
}

# Ground-level test positions (eye height above ground).
BEHIND_BUILDING_SEAT = {
    "name": "Paddock behind pit building",
    "lon": 14.7652,
    "lat": 47.2209,
    "eye_height_m": 1.7,
    "description": "Standing in the paddock, the pit building is between you and the straight"
}
IN_WOOD_SEAT = {
    "name": "Woodland north-west of Turn 3",
    "lon": 14.7530,
    "lat": 47.2290,
    "eye_height_m": 1.7,
    "description": "Standing among trees above Turn 3 (Remus)"
}


def terrain_bbox(coords, buffer_deg=TERRAIN_BUFFER_DEG):
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons) - buffer_deg, min(lats) - buffer_deg,
            max(lons) + buffer_deg, max(lats) + buffer_deg)


@lru_cache(maxsize=4)
def _terrain(dem_path: str, bbox) -> Terrain:
    return Terrain.from_dem(dem_path, bbox)


def load_terrain(dem_path, coords) -> Terrain:
    return _terrain(str(dem_path), tuple(round(v, 6) for v in terrain_bbox(coords)))


_obstruction_cache = {}


def load_obstructions(coords, geojson_path=DEFAULT_OBSTRUCTIONS):
    key = (str(geojson_path), len(coords), tuple(coords[0]), tuple(coords[len(coords) // 2]))
    if key not in _obstruction_cache:
        _obstruction_cache[key] = build_obstructions(coords, geojson_path)
    return _obstruction_cache[key]


def preset_seats(obs) -> dict:
    """All grandstand presets, keyed by id, named ones first."""
    by_osm = {g["feature"]["properties"]["osm_id"]: g for g in obs.grandstands}
    seats = {}
    used = set()
    for seat_id, info in PRESET_GRANDSTANDS.items():
        g = by_osm.get(info["osm_id"])
        if g is None:
            continue
        seats[seat_id] = {**info, **grandstand_seat(obs, g), "grandstand": True}
        used.add(info["osm_id"])
    names = {}
    for g in sorted(obs.grandstands, key=lambda g: g["feature"]["properties"]["osm_id"]):
        p = g["feature"]["properties"]
        if p["osm_id"] in used:
            continue
        name = p.get("name", f"Grandstand {p['osm_id']}")
        names[name] = names.get(name, 0) + 1
        if names[name] > 1:
            name = f"{name} #{names[name]}"
        seats[f"gs_{p['osm_id']}"] = {"osm_id": p["osm_id"], "name": name,
                                      "description": "OSM grandstand",
                                      **grandstand_seat(obs, g), "grandstand": True}
    return seats


def compute_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Compute approximate distance in meters between two points."""
    center_lat = (lat1 + lat2) / 2
    lat_scale = 111320
    lon_scale = 111320 * math.cos(math.radians(center_lat))

    dx = (lon2 - lon1) * lon_scale
    dy = (lat2 - lat1) * lat_scale

    return math.sqrt(dx * dx + dy * dy)


def compute_viewshed(dem_path: str, track_coords: list, seat_lon: float,
                     seat_lat: float, eye_height_m: float = 10.0,
                     obstructions=None) -> dict:
    """
    Compute viewshed from a seat position.

    Args:
        dem_path: Path to DEM GeoTIFF
        track_coords: List of [lon, lat] coordinates along the track
        seat_lon, seat_lat: Seat position in WGS84
        eye_height_m: Eye height above DEM ground (for grandstand seats this
            is tier height + 1.2 m, and stays the same with obstructions off)
        obstructions: surface_model.Obstructions, or None for bare terrain

    Returns:
        Dictionary with visibility array, stats, and visible corners
    """
    terrain = load_terrain(dem_path, track_coords)

    seat_ground_elev = float(terrain.sample(seat_lon, seat_lat))
    if np.isnan(seat_ground_elev):
        raise ValueError(f"Seat position ({seat_lon}, {seat_lat}) is outside DEM bounds")

    seat_eye_elev = seat_ground_elev + eye_height_m
    visibility = visibility_from(terrain, obstructions, track_coords,
                                 seat_lon, seat_lat, seat_eye_elev)

    distances = [0.0]
    for i in range(1, len(track_coords)):
        d = compute_distance_m(track_coords[i - 1][0], track_coords[i - 1][1],
                               track_coords[i][0], track_coords[i][1])
        distances.append(distances[-1] + d)

    visible_length = sum(
        distances[i + 1] - distances[i]
        for i in range(len(visibility) - 1)
        if visibility[i] and visibility[i + 1]
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
        "obstructions": obstructions is not None,
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
                        help="Eye height above ground in meters for custom seats (default: 10)")
    parser.add_argument("--analysis", help="Analysis JSON for corner data (and interpolated centreline)")
    parser.add_argument("--obstructions", default=str(DEFAULT_OBSTRUCTIONS),
                        help="Obstruction GeoJSON (default: committed OSM fixture)")
    parser.add_argument("--no-obstructions", action="store_true", help="Bare terrain only")
    parser.add_argument("--output", "-o", help="Output JSON file")
    parser.add_argument("--list-presets", action="store_true", help="List available preset seats")
    args = parser.parse_args()

    corners = []
    coords = None
    if args.analysis:
        with open(args.analysis) as f:
            analysis = json.load(f)
        corners = analysis.get("corners", [])
        coords = analysis["profile"]["coords"]
    if coords is None:
        with open(args.centreline) as f:
            gj = json.load(f)
        for feature in gj.get("features", [gj]):
            geom = feature.get("geometry", feature)
            if geom["type"] == "LineString":
                coords = geom["coordinates"]
                break
    if coords is None:
        raise ValueError("No LineString found in GeoJSON")

    obs = load_obstructions(coords, args.obstructions)
    presets = preset_seats(obs)

    if args.list_presets:
        print("Available preset seats:")
        for key, seat in presets.items():
            print(f"  {key}: {seat['name']}")
            print(f"    Position: {seat['lat']}, {seat['lon']}  eye {seat['eye_height_m']} m")
        return

    seat_key = args.seat or "main_grandstand"
    if seat_key in presets:
        seat = presets[seat_key]
        seat_lon, seat_lat = seat["lon"], seat["lat"]
        eye_height = seat["eye_height_m"]
        seat_name = seat["name"]
    else:
        try:
            lat_str, lon_str = seat_key.split(",")
            seat_lat, seat_lon = float(lat_str.strip()), float(lon_str.strip())
        except ValueError:
            raise ValueError(f"Invalid seat format: {seat_key}. Use 'lat,lon' or a preset name.")
        tier, cls = obs.sample(seat_lon, seat_lat)
        if int(cls) == 3:
            eye_height = float(tier) + EYE_ABOVE_TIER_M
        else:
            eye_height = args.eye_height
        seat_name = f"Custom ({seat_lat:.5f}, {seat_lon:.5f})"

    print(f"Computing viewshed from: {seat_name}")
    print(f"  Position: {seat_lat:.5f}, {seat_lon:.5f}")
    print(f"  Eye height: {eye_height:.1f}m above ground")

    bare = compute_viewshed(args.dem, coords, seat_lon, seat_lat, eye_height)
    result = compute_viewshed(args.dem, coords, seat_lon, seat_lat, eye_height,
                              None if args.no_obstructions else obs)
    result["seat"]["name"] = seat_name

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

    suffix = "" if args.no_obstructions else " (terrain only: %.1f%%)" % bare["visible_pct"]
    print(f"\nTrack visibility: {result['visible_pct']:.1f}%{suffix}")
    print(f"  Visible: {result['visible_length_m']:.0f}m / {result['total_length_m']:.0f}m")
    print(f"  Start/finish visible: {'Yes' if sf_visible else 'No'}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {args.output}")

    return result


if __name__ == "__main__":
    main()
