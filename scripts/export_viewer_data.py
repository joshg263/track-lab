#!/usr/bin/env python3
"""
Export terrain, track, obstruction and scene data for the 3D viewer.

Creates (in --output-dir):
- terrain.json + terrain.bin: DEM window (float32, row-major, north first)
- obstructions.bin: obstruction raster, uint8 layers: heights (x0.25 m), classes, stand ids
- scene.json: buildings, grandstands, trees, raster metadata, imagery, attribution
- track.json: centreline with elevation profile + preset seat viewsheds
- imagery.jpg: baked Sentinel-2 cloudless texture (copied from data/imagery)
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from compute_viewshed import (  # noqa: E402
    compute_viewshed, find_visible_corners, load_obstructions, load_terrain, preset_seats,
)
from surface_model import (  # noqa: E402
    CLASS_TREE, DEFAULT_OBSTRUCTIONS, EYE_ABOVE_TIER_M, GRANDSTAND_FRONT_HEIGHT_M,
    LOS_SAMPLES, METRES_PER_DEG_LAT, TARGET_HEIGHT_M, grandstand_height_at, metres_per_deg_lon,
)

PROJECT_DIR = Path(__file__).parent.parent
IMAGERY_META = PROJECT_DIR / "data" / "imagery" / "red_bull_ring_s2cloudless.json"
TREE_SPACING_M = 9.0
TREE_SEED = 20261112


class Frame:
    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.sx = metres_per_deg_lon(lat0)
        self.sy = METRES_PER_DEG_LAT

    def xy(self, lon, lat):
        return (lon - self.lon0) * self.sx, (lat - self.lat0) * self.sy


def export_terrain(dem_path, coords, output_dir: Path) -> dict:
    terrain = load_terrain(dem_path, coords)
    h = terrain.heights.astype(np.float32)
    h.astype("<f4").tofile(output_dir / "terrain.bin")
    meta = {
        "type": "heightmap",
        "file": "terrain.bin",
        "left": terrain.left, "top": terrain.top,
        "res_x": terrain.res_x, "res_y": terrain.res_y,
        "rows": terrain.rows, "cols": terrain.cols,
        "bbox": {"min_lon": terrain.left, "min_lat": terrain.bottom,
                 "max_lon": terrain.right, "max_lat": terrain.top},
        "min_elev": round(float(np.nanmin(h)), 1),
        "max_elev": round(float(np.nanmax(h)), 1),
    }
    with open(output_dir / "terrain.json", "w") as f:
        json.dump(meta, f)
    print(f"Terrain exported: {terrain.cols}x{terrain.rows} grid "
          f"({(output_dir / 'terrain.bin').stat().st_size / 1024:.0f} KB)")
    return meta


def densify_ring(ring, frame, step_m):
    pts = np.asarray(ring)
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        ax, ay = frame.xy(*a)
        bx, by = frame.xy(*b)
        n = max(1, int(np.ceil(np.hypot(bx - ax, by - ay) / step_m)))
        for t in np.arange(n) / n:
            out.append(a + t * (b - a))
    out.append(pts[-1])
    return out


def ring_xy(frame, ring, d_fn=None):
    pts = np.asarray(ring)[:-1]
    x, y = frame.xy(pts[:, 0], pts[:, 1])
    out = {"xy": np.round(np.column_stack([x, y]), 2).ravel().tolist()}
    if d_fn is not None:
        out["d"] = np.round(d_fn(pts[:, 0], pts[:, 1]), 2).tolist()
    return out


def polygons(geom):
    g = shape(geom)
    return list(g.geoms) if g.geom_type == "MultiPolygon" else [g]


def export_scene(obs, frame: Frame, output_dir: Path) -> dict:
    with open(output_dir / "obstructions.bin", "wb") as f:
        f.write(obs.layers_bytes())

    buildings = []
    for feat in obs.buildings:
        for poly in polygons(feat["geometry"]):
            buildings.append({"h": feat["properties"]["height_m"],
                              **ring_xy(frame, poly.exterior.coords)})

    grandstands = []
    for g in obs.grandstands:
        p = g["feature"]["properties"]
        for poly in polygons(g["feature"]["geometry"]):
            r = ring_xy(frame, densify_ring(poly.exterior.coords, frame, 2.0), obs.track_distance)
            r["top"] = np.round(grandstand_height_at(g, r["d"]), 2).tolist()
            grandstands.append({"osm_id": p["osm_id"], "name": p.get("name", ""),
                                "h": g["height_m"], "rake": round(g["rake"], 4),
                                "d_min": round(g["d_min"], 2), "d_max": round(g["d_max"], 2), **r})

    # Deterministic jittered lattice of trees inside canopy cells
    rng = np.random.default_rng(TREE_SEED)
    x0, y0 = frame.xy(obs.left, obs.top - obs.rows * obs.dlat)
    x1, y1 = frame.xy(obs.left + obs.cols * obs.dlon, obs.top)
    gx, gy = np.meshgrid(np.arange(x0, x1, TREE_SPACING_M), np.arange(y0, y1, TREE_SPACING_M))
    gx = gx.ravel() + rng.uniform(-3, 3, gx.size)
    gy = gy.ravel() + rng.uniform(-3, 3, gy.size)
    scale = rng.uniform(0.8, 1.15, gx.size)
    lon = frame.lon0 + gx / frame.sx
    lat = frame.lat0 + gy / frame.sy
    _, cls = obs.sample(lon, lat)
    keep = cls == CLASS_TREE
    canopy, _ = obs.sample(lon[keep], lat[keep])
    trees = np.column_stack([gx[keep], gy[keep], canopy * scale[keep]])
    trees = np.round(trees, 1).ravel().tolist()

    with open(IMAGERY_META) as f:
        imagery = json.load(f)
    shutil.copy(IMAGERY_META.parent / imagery["image"], output_dir / "imagery.jpg")

    with open(DEFAULT_OBSTRUCTIONS) as f:
        osm_meta = json.load(f)["metadata"]

    scene = {
        "frame": {"lon0": frame.lon0, "lat0": frame.lat0},
        "obstructions": {**obs.meta(), "file": "obstructions.bin"},
        "buildings": buildings,
        "grandstands": grandstands,
        "grandstand_front_height_m": GRANDSTAND_FRONT_HEIGHT_M,
        "trees": trees,
        "imagery": {"file": "imagery.jpg", "bbox": imagery["bbox"],
                    "attribution": imagery["attribution"]},
        "attribution": [
            "Elevation: Copernicus DEM GLO-30 (ESA / Copernicus)",
            f"Buildings, grandstands, woodland: {osm_meta['attribution']}",
            f"Imagery: {imagery['attribution']}",
        ],
    }
    with open(output_dir / "scene.json", "w") as f:
        json.dump(scene, f, separators=(",", ":"))
    print(f"Scene exported: {len(buildings)} buildings, {len(grandstands)} grandstands, "
          f"{len(trees) // 3} trees ({(output_dir / 'scene.json').stat().st_size / 1024:.0f} KB)")
    return scene


def export_track(analysis: dict, dem_path, obs, frame: Frame, output_dir: Path) -> dict:
    profile = analysis["profile"]
    coords = profile["coords"]
    points_3d = []
    for i, (lon, lat) in enumerate(coords):
        x, y = frame.xy(lon, lat)
        points_3d.append({
            "x": round(x, 2), "y": round(y, 2), "z": round(profile["elevations_m"][i], 2),
            "lon": lon, "lat": lat,
            "distance": round(profile["distances_m"][i], 1),
            "gradient": round(profile["gradients_pct"][i], 2),
            "curvature": round(profile["curvature"][i], 6),
        })

    track = {
        "type": "track_profile",
        "center": {"lon": frame.lon0, "lat": frame.lat0},
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
        "points": points_3d,
        "preset_seats": [],
        "track_width_m": 12.0,
        "viewshed_params": {"los_samples": LOS_SAMPLES, "target_height_m": TARGET_HEIGHT_M,
                            "eye_above_tier_m": EYE_ABOVE_TIER_M},
    }

    print("\nComputing viewsheds for preset seats (terrain only -> with obstructions)...")
    corners = analysis.get("corners", [])
    for seat_id, seat in preset_seats(obs).items():
        bare = compute_viewshed(dem_path, coords, seat["lon"], seat["lat"], seat["eye_height_m"])
        vs = compute_viewshed(dem_path, coords, seat["lon"], seat["lat"], seat["eye_height_m"], obs)
        corners_vis = find_visible_corners(vs["visibility"], vs["distances_m"], corners)
        x, y = frame.xy(seat["lon"], seat["lat"])
        track["preset_seats"].append({
            "id": seat_id,
            "name": seat["name"],
            "description": seat.get("description", ""),
            "lon": seat["lon"], "lat": seat["lat"],
            "x": round(x, 2), "y": round(y, 2),
            "grandstand": True,
            "tier_height_m": seat["tier_height_m"],
            "ground_elevation_m": vs["seat"]["ground_elevation_m"],
            "eye_height_m": seat["eye_height_m"],
            "eye_elevation_m": vs["seat"]["eye_elevation_m"],
            "visibility": vs["visibility"],
            "visible_pct": vs["visible_pct"],
            "visible_length_m": vs["visible_length_m"],
            "visibility_bare": bare["visibility"],
            "visible_pct_bare": bare["visible_pct"],
            "visible_corners": [c for c in corners_vis if c["visible"]],
        })
        print(f"  {seat['name']:32s} {bare['visible_pct']:5.1f}% -> {vs['visible_pct']:5.1f}%")

    with open(output_dir / "track.json", "w") as f:
        json.dump(track, f)
    print(f"\nTrack exported: {len(points_3d)} points "
          f"({(output_dir / 'track.json').stat().st_size / 1024:.0f} KB)")
    return track


def export_all(analysis_path, dem_path, output_dir, with_scene=True):
    with open(analysis_path) as f:
        analysis = json.load(f)
    coords = analysis["profile"]["coords"]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = Frame(sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))
    obs = load_obstructions(coords)

    print("Exporting terrain...")
    export_terrain(dem_path, coords, output_dir)
    if with_scene:
        print("\nExporting scene...")
        export_scene(obs, frame, output_dir)
    else:
        with open(output_dir / "obstructions.bin", "wb") as f:
            f.write(obs.layers_bytes())
        with open(output_dir / "scene.json", "w") as f:
            json.dump({"frame": {"lon0": frame.lon0, "lat0": frame.lat0},
                       "obstructions": {**obs.meta(), "file": "obstructions.bin"}}, f)
    print("\nExporting track...")
    return export_track(analysis, dem_path, obs, frame, output_dir)


def main():
    parser = argparse.ArgumentParser(description="Export data for 3D viewer")
    parser.add_argument("--analysis", required=True, help="Analysis JSON from analyze_track.py")
    parser.add_argument("--dem", required=True, help="DEM GeoTIFF file")
    parser.add_argument("--output-dir", default="viewer/data", help="Output directory")
    args = parser.parse_args()
    export_all(args.analysis, args.dem, args.output_dir)
    total = sum(p.stat().st_size for p in Path(args.output_dir).iterdir())
    print(f"\nTotal viewer data: {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
