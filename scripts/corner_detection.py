"""
Shared corner detection for centreline and layout analysis.

Resample at 5 m, compute three-point radius over ±20 m arc length,
count corners as runs with radius < 175 m (merge gaps < 30 m, drop runs < 20 m).
"""

from __future__ import annotations

import numpy as np

RESAMPLE_SPACING_M = 5.0
HALF_WINDOW_M = 20.0
RADIUS_THRESHOLD_M = 175.0
MERGE_GAP_M = 30.0
MIN_RUN_LENGTH_M = 20.0


def resample_polyline_xy(
    xy: np.ndarray,
    distances: np.ndarray,
    spacing_m: float = RESAMPLE_SPACING_M,
    closed: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Resample a polyline to roughly uniform arc-length spacing."""
    if len(xy) < 2:
        return xy.copy(), distances.copy()

    seg_len = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    if closed and len(xy) > 1:
        seg_len = np.append(seg_len, np.linalg.norm(xy[0] - xy[-1]))

    cumulative = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = cumulative[-1]
    closing = float(np.linalg.norm(xy[0] - xy[-1])) if closed and len(xy) > 1 else 0.0
    if total < spacing_m:
        return xy.copy(), distances.copy()

    new_d = np.arange(0.0, total, spacing_m)
    if new_d[-1] < total - 1e-6:
        new_d = np.append(new_d, total)

    if closed and len(xy) > 1:
        xp_x = np.append(xy[:, 0], xy[0, 0])
        xp_y = np.append(xy[:, 1], xy[0, 1])
    else:
        xp_x = xy[:, 0]
        xp_y = xy[:, 1]

    if len(xp_x) != len(cumulative):
        raise ValueError(
            f"resample_polyline_xy: {len(xp_x)} vertices vs {len(cumulative)} distance knots"
        )

    new_x = np.interp(new_d, cumulative, xp_x)
    new_y = np.interp(new_d, cumulative, xp_y)
    return np.column_stack([new_x, new_y]), new_d


def compute_curvature_xy(
    x: np.ndarray,
    y: np.ndarray,
    distances: np.ndarray,
    half_window_m: float = HALF_WINDOW_M,
    loop_length: float | None = None,
) -> np.ndarray:
    """Curvature (1/m) from three-point radius over ±half_window_m along arc length."""
    n = len(x)
    curvature = np.zeros(n)
    total = loop_length if loop_length is not None else (distances[-1] if n else 0.0)

    def sample_xy(d: float) -> tuple[float, float]:
        if loop_length is not None and loop_length > 0:
            d = d % loop_length
        idx = int(np.searchsorted(distances, d, side="right") - 1)
        idx = max(0, min(idx, n - 1))
        idx1 = min(idx + 1, n - 1)
        d0, d1 = distances[idx], distances[idx1]
        if idx1 == idx or d1 <= d0 + 1e-9:
            t = 0.0
        else:
            t = (d - d0) / (d1 - d0)
        return (
            x[idx] + t * (x[idx1] - x[idx]),
            y[idx] + t * (y[idx1] - y[idx]),
        )

    for i in range(n):
        d_i = distances[i]
        d_back = d_i - half_window_m
        d_fwd = d_i + half_window_m

        if loop_length is not None and loop_length > 0:
            d_back %= loop_length
            d_fwd %= loop_length
        else:
            d_back = max(d_back, distances[0])
            d_fwd = min(d_fwd, distances[-1])

        if abs(d_fwd - d_back) < 1e-6:
            continue

        x1, y1 = sample_xy(d_back)
        x2, y2 = x[i], y[i]
        x3, y3 = sample_xy(d_fwd)

        a = np.hypot(x2 - x1, y2 - y1)
        b = np.hypot(x3 - x2, y3 - y2)
        c = np.hypot(x3 - x1, y3 - y1)

        if a < 1e-6 or b < 1e-6 or c < 1e-6:
            continue

        s = (a + b + c) / 2
        area_sq = s * (s - a) * (s - b) * (s - c)
        if area_sq <= 0:
            continue

        area = np.sqrt(area_sq)
        radius = (a * b * c) / (4 * area)
        if radius > 1e-6:
            curvature[i] = 1.0 / radius

    return curvature


def compute_curvature_lonlat(
    coords: np.ndarray,
    distances: np.ndarray,
    half_window_m: float = HALF_WINDOW_M,
) -> np.ndarray:
    """Project lon/lat to local metres, then use arc-length windowed curvature."""
    center_lat = np.mean(coords[:, 1])
    center_lon = np.mean(coords[:, 0])
    lat_scale = 111320.0
    lon_scale = 111320.0 * np.cos(np.radians(center_lat))
    x = (coords[:, 0] - center_lon) * lon_scale
    y = (coords[:, 1] - center_lat) * lat_scale
    return compute_curvature_xy(x, y, distances, half_window_m=half_window_m)


def radii_from_curvature(curvature: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return np.where(curvature > 1e-10, 1.0 / curvature, np.inf)


def find_corners(
    curvature: np.ndarray,
    distances: np.ndarray,
    radius_threshold_m: float = RADIUS_THRESHOLD_M,
    merge_gap_m: float = MERGE_GAP_M,
    min_run_length_m: float = MIN_RUN_LENGTH_M,
) -> list[tuple[float, float]]:
    """
    Contiguous runs with radius below threshold.
    Returns (center_distance_m, min_radius_m) sorted by distance.
    """
    n = len(curvature)
    radius = radii_from_curvature(curvature)

    runs: list[tuple[int, int]] = []
    in_run = False
    run_start = 0

    for i in range(n):
        if radius[i] < radius_threshold_m:
            if not in_run:
                in_run = True
                run_start = i
        elif in_run:
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

    corners: list[tuple[float, float]] = []
    for start, end in merged:
        run_length = distances[end] - distances[start]
        if run_length < min_run_length_m:
            continue

        run_radii = radius[start : end + 1]
        valid = run_radii[run_radii < np.inf]
        if len(valid) == 0:
            continue

        min_radius = float(np.min(valid))
        center_dist = float((distances[start] + distances[end]) / 2)
        corners.append((center_dist, min_radius))

    corners.sort(key=lambda x: x[0])
    return corners


def find_corners_dict(
    curvature: np.ndarray,
    distances: np.ndarray,
    **kwargs,
) -> list[dict]:
    """Corner list as dicts with position_m and radius_m (layout checker / viewer)."""
    return [
        {"position_m": round(d, 1), "radius_m": round(r, 1)}
        for d, r in find_corners(curvature, distances, **kwargs)
    ]


def analyze_corners_xy(
    xy: np.ndarray,
    distances: np.ndarray,
    *,
    spacing_m: float = RESAMPLE_SPACING_M,
    closed: bool = False,
) -> dict:
    """
    Resample, compute curvature, detect corners.
    Returns curvature, resampled xy/distances, corners tuples, and min radii.
    """
    closing = float(np.linalg.norm(xy[0] - xy[-1])) if closed and len(xy) > 1 else 0.0
    loop_length = float(distances[-1] + closing) if closed else None

    xy_r, dist_r = resample_polyline_xy(xy, distances, spacing_m=spacing_m, closed=closed)
    curvature = compute_curvature_xy(
        xy_r[:, 0],
        xy_r[:, 1],
        dist_r,
        half_window_m=HALF_WINDOW_M,
        loop_length=loop_length,
    )
    radius = radii_from_curvature(curvature)
    finite = radius[np.isfinite(radius) & (radius < np.inf)]
    min_radius_m = float(np.min(finite)) if len(finite) else None

    corners = find_corners(curvature, dist_r)
    corner_radii = [r for _, r in corners]
    min_corner_radius_m = min(corner_radii) if corner_radii else None

    return {
        "xy": xy_r,
        "distances": dist_r,
        "curvature": curvature,
        "radius": radius,
        "corners": corners,
        "corners_dict": [
            {"position_m": round(d, 1), "radius_m": round(r, 1)} for d, r in corners
        ],
        "corner_count": len(corners),
        "min_radius_m": min_radius_m,
        "min_corner_radius_m": min_corner_radius_m,
    }
