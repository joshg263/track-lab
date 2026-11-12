#!/usr/bin/env python3
"""
Surface model for line-of-sight: DEM + obstruction heights.

The obstruction raster (height above ground, metres) is built from the
committed OSM fixture (data/obstructions/*.geojson):
  - woodland / tree rows / trees: canopy height (default 15 m)
  - buildings: height tag, else levels x 3 m, else 8 m
  - grandstands: seating deck rising from GRANDSTAND_FRONT_HEIGHT_M at the
    edge nearest the track at a rake of at least GRANDSTAND_MIN_RAKE (rise per
    metre, ~24 deg) up to the stand height (default 12 m), then flat behind
  - the track corridor (TRACK_CLEARANCE_M either side of the centreline) is
    always clear, so canopy polygons that overlap the circuit cannot hide it
  - each grandstand cell also carries a stand id; a seat never has its view
    blocked by the stand it sits in (the rake is what lets you see over the
    rows in front, which a 5 m raster cannot resolve)

viewer/viewshed.js is a line-for-line port of the sampling and line-of-sight
code below. Change both together; `make verify` checks they agree.
"""

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import from_bounds
from scipy.spatial import cKDTree
from shapely.geometry import shape

PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_OBSTRUCTIONS = PROJECT_DIR / "data" / "obstructions" / "red_bull_ring_osm.geojson"

LOS_SAMPLES = 256
TARGET_HEIGHT_M = 1.0
EYE_ABOVE_TIER_M = 1.2
OBSTRUCTION_CELL_M = 5.0
HEIGHT_QUANTUM_M = 0.25
TRACK_CLEARANCE_M = 12.0
GRANDSTAND_FRONT_HEIGHT_M = 2.0
GRANDSTAND_MIN_RAKE = 0.45

CLASS_NONE, CLASS_TREE, CLASS_BUILDING, CLASS_GRANDSTAND = 0, 1, 2, 3
TREE_KINDS = ("wood", "tree_row", "tree")

METRES_PER_DEG_LAT = 111320.0


def metres_per_deg_lon(lat: float) -> float:
    return METRES_PER_DEG_LAT * math.cos(math.radians(lat))


class Terrain:
    """DEM grid with pixel-centre bilinear sampling (NaN outside)."""

    def __init__(self, heights: np.ndarray, left: float, top: float,
                 res_x: float, res_y: float):
        self.heights = np.asarray(heights, dtype=np.float64)
        self.rows, self.cols = self.heights.shape
        self.left, self.top = left, top
        self.res_x, self.res_y = res_x, res_y
        self.right = left + res_x * self.cols
        self.bottom = top - res_y * self.rows

    @classmethod
    def from_dem(cls, dem_path, bbox=None):
        with rasterio.open(dem_path) as src:
            if bbox is None:
                data = src.read(1).astype(np.float32)
                t = src.transform
            else:
                win = from_bounds(*bbox, src.transform).round_offsets().round_lengths()
                win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
                data = src.read(1, window=win).astype(np.float32)
                t = src.window_transform(win)
            if src.nodata is not None:
                data = np.where(data == src.nodata, np.nan, data)
        return cls(data, t.c, t.f, t.a, -t.e)

    def sample(self, lon, lat):
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        inside = (lon >= self.left) & (lon <= self.right) & (lat >= self.bottom) & (lat <= self.top)
        col = np.clip((lon - self.left) / self.res_x - 0.5, 0, self.cols - 1)
        row = np.clip((self.top - lat) / self.res_y - 0.5, 0, self.rows - 1)
        c0 = np.minimum(np.floor(col).astype(np.int64), self.cols - 2)
        r0 = np.minimum(np.floor(row).astype(np.int64), self.rows - 2)
        dc = col - c0
        dr = row - r0
        h = self.heights
        e = (h[r0, c0] * (1 - dc) * (1 - dr) +
             h[r0, c0 + 1] * dc * (1 - dr) +
             h[r0 + 1, c0] * (1 - dc) * dr +
             h[r0 + 1, c0 + 1] * dc * dr)
        return np.where(inside, e, np.nan)


class Obstructions:
    """Obstruction heights above ground on a lon/lat grid (nearest-cell sampling)."""

    def __init__(self, heights_q: np.ndarray, classes: np.ndarray, stand_ids: np.ndarray,
                 left: float, top: float, dlon: float, dlat: float,
                 grandstands=None, buildings=None, trees=None):
        self.heights_q = heights_q.astype(np.uint8)
        self.heights = self.heights_q.astype(np.float64) * HEIGHT_QUANTUM_M
        self.classes = classes.astype(np.uint8)
        self.stand_ids = stand_ids.astype(np.uint8)
        self.rows, self.cols = heights_q.shape
        self.left, self.top, self.dlon, self.dlat = left, top, dlon, dlat
        self.grandstands = grandstands or []
        self.buildings = buildings or []
        self.trees = trees or []

    def cell_index(self, lon, lat):
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        c = np.floor((lon - self.left) / self.dlon).astype(np.int64)
        r = np.floor((self.top - lat) / self.dlat).astype(np.int64)
        ok = (c >= 0) & (c < self.cols) & (r >= 0) & (r < self.rows)
        return np.where(ok, r, 0), np.where(ok, c, 0), ok

    def sample(self, lon, lat):
        r, c, ok = self.cell_index(lon, lat)
        return np.where(ok, self.heights[r, c], 0.0), np.where(ok, self.classes[r, c], CLASS_NONE)

    def stand_id(self, lon, lat):
        r, c, ok = self.cell_index(lon, lat)
        return np.where(ok, self.stand_ids[r, c], 0)

    def layers_bytes(self) -> bytes:
        """Viewer format: heights_q, classes, stand_ids (uint8, row-major, north first)."""
        return b"".join(np.ascontiguousarray(a).tobytes()
                        for a in (self.heights_q, self.classes, self.stand_ids))

    def meta(self) -> dict:
        return {"left": self.left, "top": self.top, "dlon": self.dlon, "dlat": self.dlat,
                "rows": self.rows, "cols": self.cols, "height_quantum_m": HEIGHT_QUANTUM_M,
                "layers": ["heights_q", "classes", "stand_ids"],
                "classes": {"none": CLASS_NONE, "tree": CLASS_TREE,
                            "building": CLASS_BUILDING, "grandstand": CLASS_GRANDSTAND}}


class LocalFrame:
    """Equirectangular metres around a reference point."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.sx = metres_per_deg_lon(lat0)
        self.sy = METRES_PER_DEG_LAT

    def to_xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.sx, (np.asarray(lat) - self.lat0) * self.sy


def track_distance_fn(track_coords, frame: LocalFrame, spacing_m: float = 1.0):
    """Return f(lon, lat) -> distance (m) to the closed centreline."""
    pts = np.asarray(track_coords, dtype=np.float64)
    pts = np.vstack([pts, pts[:1]])
    x, y = frame.to_xy(pts[:, 0], pts[:, 1])
    dense = []
    for i in range(len(x) - 1):
        seg = math.hypot(x[i + 1] - x[i], y[i + 1] - y[i])
        n = max(1, int(math.ceil(seg / spacing_m)))
        t = np.arange(n) / n
        dense.append(np.column_stack([x[i] + t * (x[i + 1] - x[i]), y[i] + t * (y[i + 1] - y[i])]))
    tree = cKDTree(np.vstack(dense))

    def dist(lon, lat):
        qx, qy = frame.to_xy(lon, lat)
        d, _ = tree.query(np.column_stack([np.ravel(qx), np.ravel(qy)]))
        return d.reshape(np.shape(qx))
    return dist


def load_obstruction_features(path=DEFAULT_OBSTRUCTIONS):
    with open(path) as f:
        gj = json.load(f)
    return gj


def build_obstructions(track_coords, geojson_path=DEFAULT_OBSTRUCTIONS,
                       cell_m: float = OBSTRUCTION_CELL_M) -> Obstructions:
    gj = load_obstruction_features(geojson_path)
    w, s, e, n = gj["metadata"]["bbox"]
    mid_lat = (s + n) / 2
    dlat = cell_m / METRES_PER_DEG_LAT
    dlon = cell_m / metres_per_deg_lon(mid_lat)
    cols = int(math.ceil((e - w) / dlon))
    rows = int(math.ceil((n - s) / dlat))
    transform = Affine(dlon, 0, w, 0, -dlat, n)
    shp = (rows, cols)

    frame = LocalFrame(*np.mean(np.asarray(track_coords), axis=0))
    track_dist = track_distance_fn(track_coords, frame)

    feats = gj["features"]
    trees = [f for f in feats if f["properties"]["kind"] in TREE_KINDS]
    blds = [f for f in feats if f["properties"]["kind"] == "building"]
    stands = [f for f in feats if f["properties"]["kind"] == "grandstand"]

    heights = np.zeros(shp, dtype=np.float64)
    classes = np.zeros(shp, dtype=np.uint8)

    if trees:
        th = rasterize(((f["geometry"], f["properties"]["height_m"]) for f in trees),
                       out_shape=shp, transform=transform, fill=0, dtype="float32",
                       merge_alg=rasterio.enums.MergeAlg.replace)
        m = th > 0
        heights[m] = th[m]
        classes[m] = CLASS_TREE

    if blds:
        bh = rasterize(((f["geometry"], f["properties"]["height_m"]) for f in blds),
                       out_shape=shp, transform=transform, fill=0, dtype="float32")
        m = bh > 0
        heights[m] = bh[m]
        classes[m] = CLASS_BUILDING

    cc = np.arange(cols) + 0.5
    rr = np.arange(rows) + 0.5
    cell_lon = w + cc * dlon
    cell_lat = n - rr * dlat
    lon_grid, lat_grid = np.meshgrid(cell_lon, cell_lat)
    cell_track_d = track_dist(lon_grid, lat_grid)

    grandstands = []
    stand_ids = np.zeros(shp, dtype=np.uint8)
    if stands:
        ids = rasterize(((f["geometry"], i + 1) for i, f in enumerate(stands)),
                        out_shape=shp, transform=transform, fill=0, dtype="int32")
        for i, f in enumerate(stands):
            m = ids == i + 1
            if not m.any():
                continue
            h_top = f["properties"]["height_m"]
            d = cell_track_d[m]
            d_min, d_max = float(d.min()), float(d.max())
            stand = {"feature": f, "d_min": d_min, "d_max": d_max, "height_m": h_top,
                     "rake": stand_rake(h_top, d_min, d_max), "cells": m,
                     "stand_id": len(grandstands) + 1}
            heights[m] = grandstand_height_at(stand, d)
            classes[m] = CLASS_GRANDSTAND
            stand_ids[m] = stand["stand_id"]
            grandstands.append(stand)

    clear = cell_track_d < TRACK_CLEARANCE_M
    heights[clear] = 0
    classes[clear] = CLASS_NONE
    stand_ids[clear | (classes != CLASS_GRANDSTAND)] = 0

    hq = np.clip(np.round(heights / HEIGHT_QUANTUM_M), 0, 255).astype(np.uint8)
    obs = Obstructions(hq, classes, stand_ids, w, n, dlon, dlat, grandstands, blds, trees)
    obs.track_distance = track_dist
    obs.frame = frame
    obs.cell_lon, obs.cell_lat = cell_lon, cell_lat
    return obs


def stand_rake(height_m: float, d_min: float, d_max: float) -> float:
    span = max(d_max - d_min, 1.0)
    return max(GRANDSTAND_MIN_RAKE, (height_m - GRANDSTAND_FRONT_HEIGHT_M) / span)


def grandstand_height_at(stand: dict, d_track) -> np.ndarray:
    d = np.maximum(np.asarray(d_track, dtype=np.float64) - stand["d_min"], 0)
    return np.minimum(stand["height_m"], GRANDSTAND_FRONT_HEIGHT_M + stand["rake"] * d)


def grandstand_seat(obs: Obstructions, stand: dict) -> dict:
    """Top row of the seating deck, mid-stand: the top-row cell nearest the centroid."""
    m = stand["cells"]
    rows, cols = np.nonzero(m)
    lons = obs.cell_lon[cols]
    lats = obs.cell_lat[rows]
    d = obs.track_distance(lons, lats)
    d_top = stand["d_min"] + (stand["height_m"] - GRANDSTAND_FRONT_HEIGHT_M) / stand["rake"]
    rear = (d >= d_top - OBSTRUCTION_CELL_M) & (d < d_top)
    if not rear.any():
        rear = d >= d.max() - OBSTRUCTION_CELL_M
    centroid = shape(stand["feature"]["geometry"]).centroid
    fx, fy = obs.frame.to_xy(lons, lats)
    cx, cy = obs.frame.to_xy(centroid.x, centroid.y)
    dist_c = np.hypot(fx - cx, fy - cy) + np.where(rear, 0, 1e9)
    k = int(np.argmin(dist_c))
    lon, lat = float(lons[k]), float(lats[k])
    tier_h, _ = obs.sample(lon, lat)
    return {"lon": round(lon, 7), "lat": round(lat, 7), "tier_height_m": float(tier_h),
            "eye_height_m": round(float(tier_h) + EYE_ABOVE_TIER_M, 2)}


def visibility_from(terrain: Terrain, obstructions, coords, seat_lon: float, seat_lat: float,
                    eye_elev: float, samples: int = LOS_SAMPLES):
    """Line of sight from the eye to every track point. Returns list[bool].

    Obstructions belonging to the stand the seat is in are ignored.
    """
    pts = np.asarray(coords, dtype=np.float64)
    tlon, tlat = pts[:, 0], pts[:, 1]
    target_elev = terrain.sample(tlon, tlat) + TARGET_HEIGHT_M

    t = (np.arange(1, samples) / samples)[None, :]
    slon = seat_lon + t * (tlon[:, None] - seat_lon)
    slat = seat_lat + t * (tlat[:, None] - seat_lat)
    surface = terrain.sample(slon, slat)
    if obstructions is not None:
        own = int(obstructions.stand_id(seat_lon, seat_lat))
        oh, _ = obstructions.sample(slon, slat)
        if own:
            oh = np.where(obstructions.stand_id(slon, slat) == own, 0.0, oh)
        surface = surface + oh
    los = eye_elev + t * (target_elev[:, None] - eye_elev)
    blocked = np.any(np.where(np.isnan(surface), False, surface > los), axis=1)
    return [bool(v) for v in (~blocked & ~np.isnan(target_elev))]
