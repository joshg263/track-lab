"""Run-off zones as a layout constraint.

Starting estimates, not FIA figures. At each corner from the shared detector,
the zone is a parallelogram on the outside of the approach: its width runs
along the approach tangent and its depth runs along the escape direction
(that tangent, turned toward the outside).

Depth is v^2 / (2 a). Deceleration defaults to gravel at 0.5 g; tarmac is
about 0.9 g and is the other setting the viewer offers. v is the speed
profile's entry speed when a trace is supplied, otherwise the corner-radius
proxy sqrt(1.5 g R).

A zone passes only when terrain inside it stays under the slope limit, stays
within the drop/wall step of the designed road-edge height, and does not meet
another part of the track more than overlap_gap metres away along the lap.

The road edge is the designed deck. That deck is flat from the centreline to
the edge, so its height is the vertical profile's z(s), not a terrain or mesh
sample at the edge coordinates. Each sample is compared with z(s) on the
outside edge beside it.

The earthworks figure is the rough cut and fill to level a failing zone with
that road-edge height. It is not a construction estimate.

Line-for-line with viewer/runoff.js.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

from corner_detection import analyze_corners_xy

GRAVITY_MPS2 = 9.80665

# Gravel is the default. Tarmac is the other editable setting, about 0.9 g.
DEFAULT_DECEL_G = 0.5
TARMAC_DECEL_G = 0.9
DEFAULT_WIDTH_M = 50.0
DEFAULT_SLOPE_LIMIT = 0.10
DEFAULT_STEP_M = 3.0
DEFAULT_OVERLAP_GAP_M = 100.0
# Same lateral limit as the speed profile's generic car, used only as a proxy.
PROXY_LAT_G = 1.5

ESCAPE_OUTWARD = 0.5
TANGENT_LOOK_M = 4.0
SAMPLE_SPACING_M = 5.0
# Ribbon contact closer than this is an overlap. The zone begins on the
# outside edge, so a fraction of a metre keeps a bare touch from counting.
OVERLAP_MARGIN_M = 0.05


def speed_from_trace(trace: list[dict] | None, lap_m: float, distance_m: float) -> float | None:
    """Linear entry speed at chainage. Same knots as drive_profile.sampleProfile."""
    if not trace or not (lap_m > 0):
        return None
    n = len(trace)
    d = distance_m % lap_m
    if d < 0:
        d += lap_m
    lo = 0
    hi = n - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if float(trace[mid]["distance"]) <= d:
            lo = mid
        else:
            hi = mid - 1
    i = lo
    j = (i + 1) % n
    d0 = float(trace[i]["distance"])
    d1 = float(lap_m) if j == 0 else float(trace[j]["distance"])
    span = d1 - d0
    if span > 1e-6:
        t = (d - d0) / span
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
    else:
        t = 0.0
    v0 = float(trace[i]["speed_mps"])
    v1 = float(trace[j]["speed_mps"])
    speed = v0 + (v1 - v0) * t
    if not math.isfinite(speed) or speed <= 0:
        return None
    return speed


def _unit(x: float, y: float) -> tuple[float, float]:
    length = math.hypot(x, y)
    if length < 1e-9:
        return 1.0, 0.0
    return x / length, y / length


def _sample_xy(xy: np.ndarray, distances: np.ndarray, lap_m: float, distance_m: float) -> tuple[float, float]:
    n = len(distances)
    d = distance_m % lap_m if lap_m > 0 else distance_m
    if d < 0:
        d += lap_m
    lo = 0
    hi = n - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if float(distances[mid]) <= d:
            lo = mid
        else:
            hi = mid - 1
    i = lo
    if i >= n - 1:
        d0 = float(distances[i])
        d1 = float(lap_m)
        v1x = float(xy[0, 0])
        v1y = float(xy[0, 1])
    else:
        d0 = float(distances[i])
        d1 = float(distances[i + 1])
        v1x = float(xy[i + 1, 0])
        v1y = float(xy[i + 1, 1])
    span = d1 - d0
    if span > 1e-9:
        t = (d - d0) / span
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
    else:
        t = 0.0
    return (
        float(xy[i, 0]) + t * (v1x - float(xy[i, 0])),
        float(xy[i, 1]) + t * (v1y - float(xy[i, 1])),
    )


def _approach_tangent(xy, distances, lap_m, distance_m) -> tuple[float, float]:
    x0, y0 = _sample_xy(xy, distances, lap_m, distance_m - TANGENT_LOOK_M)
    x1, y1 = _sample_xy(xy, distances, lap_m, distance_m)
    return _unit(x1 - x0, y1 - y0)


def _travel_tangent(xy, distances, lap_m, distance_m) -> tuple[float, float]:
    x0, y0 = _sample_xy(xy, distances, lap_m, distance_m)
    x1, y1 = _sample_xy(xy, distances, lap_m, distance_m + TANGENT_LOOK_M)
    return _unit(x1 - x0, y1 - y0)


def _turn_sign(xy, distances, lap_m, start_index: int, end_index: int) -> int:
    total = 0.0
    prev = None
    i = int(start_index)
    end_index = int(end_index)
    guard = 0
    limit = max(1, end_index - i + 1)
    while guard <= limit:
        guard += 1
        tx, ty = _travel_tangent(xy, distances, lap_m, float(distances[i]))
        if prev is not None:
            total += prev[0] * ty - prev[1] * tx
        prev = (tx, ty)
        if i >= end_index:
            break
        i += 1
    if total < -1e-8:
        return -1
    return 1


def _outside_normal(tx: float, ty: float, sign: int) -> tuple[float, float]:
    if sign >= 0:
        return ty, -tx
    return -ty, tx


def _escape(tx: float, ty: float, sign: int) -> tuple[float, float]:
    nx, ny = _outside_normal(tx, ty, sign)
    return _unit(tx + ESCAPE_OUTWARD * nx, ty + ESCAPE_OUTWARD * ny)


def _interp_closed(distances: np.ndarray, values: np.ndarray, lap_m: float, distance_m: float) -> float:
    n = len(distances)
    if n == 0:
        return 0.0
    if not (lap_m > 0):
        return float(values[0])
    d = distance_m % lap_m
    if d < 0:
        d += lap_m
    lo = 0
    hi = n - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if float(distances[mid]) <= d:
            lo = mid
        else:
            hi = mid - 1
    i = lo
    if i + 1 >= n:
        d0 = float(distances[i])
        d1 = float(lap_m)
        v1 = float(values[0])
    else:
        d0 = float(distances[i])
        d1 = float(distances[i + 1])
        v1 = float(values[i + 1])
    span = d1 - d0
    if span > 1e-9:
        t = (d - d0) / span
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
    else:
        t = 0.0
    v0 = float(values[i])
    return v0 + t * (v1 - v0)


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _dist_point_segment(px, py, ax, ay, bx, by) -> float:
    abx = bx - ax
    aby = by - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / ab2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + abx * t), py - (ay + aby * t))


def _dist_point_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> float:
    if _point_in_polygon(px, py, poly):
        return 0.0
    best = math.inf
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        dist = _dist_point_segment(px, py, ax, ay, bx, by)
        if dist < best:
            best = dist
    return best


def _chainage_gap(distance_m: float, start_m: float, end_m: float, lap_m: float) -> float:
    if not (lap_m > 0):
        return 0.0
    length = end_m - start_m
    if length < 0.0:
        length = 0.0
    if length >= lap_m - 1e-6:
        return 0.0
    x = (distance_m - start_m) % lap_m
    if x < 0:
        x += lap_m
    if x <= length:
        return 0.0
    return min(x - length, lap_m - x)


def _along_edge(poly: list[tuple[float, float]], sx: float, sy: float) -> float:
    """Fraction along the outside road edge, the first side of the parallelogram."""
    ax, ay = poly[0]
    bx, by = poly[1]
    dx, dy = poly[3]
    ux = bx - ax
    uy = by - ay
    vx = dx - ax
    vy = dy - ay
    det = ux * vy - uy * vx
    if abs(det) < 1e-9:
        return 0.0
    u = ((sx - ax) * vy - (sy - ay) * vx) / det
    if u < 0.0:
        return 0.0
    if u > 1.0:
        return 1.0
    return u


def _road_edge_z(
    distances: np.ndarray,
    design_z: np.ndarray,
    lap_m: float,
    poly: list[tuple[float, float]],
    sx: float,
    sy: float,
    start_m: float,
    width_m: float,
) -> float:
    """Designed deck height on the outside edge beside this sample.

    The deck is flat from centreline to edge, so this is z(s) from the
    vertical profile. It is not a terrain or mesh sample at the edge.
    """
    u = _along_edge(poly, sx, sy)
    return _interp_closed(distances, design_z, lap_m, start_m + u * width_m)


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    area = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def _interior_samples(poly, spacing, sample_ground) -> list[tuple[int, int, float, float, float]]:
    """Grid points inside the zone, tagged with their grid indices."""
    min_x = min(p[0] for p in poly)
    max_x = max(p[0] for p in poly)
    min_y = min(p[1] for p in poly)
    max_y = max(p[1] for p in poly)
    x0 = math.floor(min_x / spacing) * spacing
    y0 = math.floor(min_y / spacing) * spacing
    samples: list[tuple[int, int, float, float, float]] = []
    ix = 0
    while True:
        x = x0 + ix * spacing
        if x > max_x + 1e-9:
            break
        iy = 0
        while True:
            y = y0 + iy * spacing
            if y > max_y + 1e-9:
                break
            if _point_in_polygon(x, y, poly):
                z = float(sample_ground(x, y))
                if not math.isfinite(z):
                    z = math.nan
                samples.append((ix, iy, x, y, z))
            iy += 1
        ix += 1
    if samples:
        return samples
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    z = float(sample_ground(cx, cy))
    return [(-1, -1, cx, cy, z)]


def _partial(by_key, ix: int, iy: int, x: float, y: float, z: float, axis: int) -> float | None:
    """One component of the terrain gradient, from the grid axis neighbours."""
    if axis == 0:
        fwd = by_key.get((ix + 1, iy))
        back = by_key.get((ix - 1, iy))
        here = x
        coord = 0
    else:
        fwd = by_key.get((ix, iy + 1))
        back = by_key.get((ix, iy - 1))
        here = y
        coord = 1
    if fwd is not None and back is not None:
        span = fwd[coord] - back[coord]
        if abs(span) < 1e-9:
            return None
        return (fwd[2] - back[2]) / span
    if fwd is not None:
        span = fwd[coord] - here
        if abs(span) < 1e-9:
            return None
        return (fwd[2] - z) / span
    if back is not None:
        span = here - back[coord]
        if abs(span) < 1e-9:
            return None
        return (z - back[2]) / span
    return None


def _max_slopes(
    samples: list[tuple[int, int, float, float, float]],
    ex: float,
    ey: float,
    px: float,
    py: float,
) -> tuple[float, float]:
    """Steepest rise/run along the escape and across it.

    A neighbour pair is only one partial derivative. Both components are
    rebuilt at a sample and then projected, so a grade that runs diagonally
    across the grid is not split between the axes and under-counted.
    """
    by_key = {(ix, iy): (x, y, z) for ix, iy, x, y, z in samples if ix >= 0}
    max_long = 0.0
    max_cross = 0.0
    for (ix, iy), (x, y, z) in by_key.items():
        gx = _partial(by_key, ix, iy, x, y, z, 0)
        gy = _partial(by_key, ix, iy, x, y, z, 1)
        if gx is None or gy is None:
            continue
        long_slope = abs(gx * ex + gy * ey)
        cross_slope = abs(gx * px + gy * py)
        if long_slope > max_long:
            max_long = long_slope
        if cross_slope > max_cross:
            max_cross = cross_slope
    return max_long, max_cross


def _zone_overlaps(
    poly,
    track_x,
    track_y,
    track_d,
    lap_m,
    start_m,
    end_m,
    half_width,
    overlap_gap_m,
) -> bool:
    limit = half_width - OVERLAP_MARGIN_M
    n = len(track_d)
    for i in range(n):
        gap = _chainage_gap(float(track_d[i]), start_m, end_m, lap_m)
        if gap <= overlap_gap_m:
            continue
        dist = _dist_point_polygon(float(track_x[i]), float(track_y[i]), poly)
        if dist < limit:
            return True
    return False


def _proxy_speed(radius_m: float, proxy_lat_g: float) -> float:
    radius = radius_m if radius_m > 1.0 else 1.0
    return math.sqrt(proxy_lat_g * GRAVITY_MPS2 * radius)


def assess_runoff(
    x: np.ndarray,
    y: np.ndarray,
    distances: np.ndarray,
    design_z: np.ndarray,
    lap_m: float,
    sample_ground: Callable[[float, float], float],
    *,
    track_width_m: float = 14.0,
    speed_trace: list[dict] | None = None,
    decel_g: float = DEFAULT_DECEL_G,
    width_m: float = DEFAULT_WIDTH_M,
    slope_limit: float = DEFAULT_SLOPE_LIMIT,
    step_m: float = DEFAULT_STEP_M,
    overlap_gap_m: float = DEFAULT_OVERLAP_GAP_M,
    proxy_lat_g: float = PROXY_LAT_G,
    closed: bool = True,
) -> dict:
    """Size one outside zone per corner and test it against sample_ground."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    distances = np.asarray(distances, dtype=np.float64)
    design_z = np.asarray(design_z, dtype=np.float64)
    xy = np.column_stack([x, y])
    lap_m = float(lap_m)
    decel_g = float(decel_g) if float(decel_g) > 0.05 else 0.05
    width_m = float(width_m) if float(width_m) >= 5.0 else DEFAULT_WIDTH_M
    slope_limit = float(slope_limit)
    step_m = float(step_m)
    half_width = float(track_width_m) * 0.5
    decel = decel_g * GRAVITY_MPS2

    analysis = analyze_corners_xy(xy, distances, closed=closed)
    resampled = analysis["xy"]
    resampled_d = analysis["distances"]
    # Resampled chainage already includes the closing point at lap length.
    resample_lap = float(resampled_d[-1]) if len(resampled_d) else lap_m
    if resample_lap <= 0:
        resample_lap = lap_m

    corners = []
    for index, run in enumerate(analysis["runs"]):
        start_m = float(run["start_m"])
        end_m = float(run["end_m"])
        radius_m = float(run["radius_m"])
        traced = speed_from_trace(speed_trace, lap_m, start_m)
        if traced is None:
            speed = _proxy_speed(radius_m, proxy_lat_g)
            speed_source = "radius_proxy"
        else:
            speed = traced
            speed_source = "profile"
        depth = (speed * speed) / (2.0 * decel)

        sign = _turn_sign(
            resampled, resampled_d, resample_lap, run["start_index"], run["end_index"],
        )
        tx, ty = _approach_tangent(resampled, resampled_d, resample_lap, start_m)
        nx, ny = _outside_normal(tx, ty, sign)
        ex, ey = _escape(tx, ty, sign)
        cx, cy = _sample_xy(resampled, resampled_d, resample_lap, start_m)
        ax = cx + nx * half_width
        ay = cy + ny * half_width
        bx = ax + tx * width_m
        by = ay + ty * width_m
        dx = ax + ex * depth
        dy = ay + ey * depth
        cpx = bx + ex * depth
        cpy = by + ey * depth
        poly = [(ax, ay), (bx, by), (cpx, cpy), (dx, dy)]

        z_edge = _interp_closed(distances, design_z, lap_m, start_m)
        samples = _interior_samples(poly, SAMPLE_SPACING_M, sample_ground)
        usable = []
        edge_z = []
        for ix, iy, sx, sy, sz in samples:
            z_ref = _road_edge_z(
                distances, design_z, lap_m, poly, sx, sy, start_m, width_m,
            )
            if not math.isfinite(sz):
                sz = z_ref - step_m - 1.0
            usable.append((ix, iy, sx, sy, sz))
            edge_z.append(z_ref)
        px, py = -ey, ex
        long_slope, cross_slope = _max_slopes(usable, ex, ey, px, py)
        max_drop = 0.0
        max_wall = 0.0
        cut_sum = 0.0
        fill_sum = 0.0
        for (_ix, _iy, _sx, _sy, sz), z_ref in zip(usable, edge_z):
            delta = sz - z_ref
            if -delta > max_drop:
                max_drop = -delta
            if delta > max_wall:
                max_wall = delta
            if delta > 0.0:
                cut_sum += delta
            else:
                fill_sum += -delta
        count = len(usable)
        area = _polygon_area(poly)
        cut_m3 = (cut_sum / count) * area if count else 0.0
        fill_m3 = (fill_sum / count) * area if count else 0.0

        reasons = []
        if cross_slope > slope_limit:
            reasons.append("cross-slope")
        if long_slope > slope_limit:
            reasons.append("long-slope")
        if max_drop > step_m:
            reasons.append("drop")
        if max_wall > step_m:
            reasons.append("wall")
        overlap = _zone_overlaps(
            poly, x, y, distances, lap_m, start_m, end_m, half_width, float(overlap_gap_m),
        )
        if overlap:
            reasons.append("overlap")
        terrain_fail = any(r != "overlap" for r in reasons)
        earthworks_m3 = (cut_m3 + fill_m3) if terrain_fail else 0.0

        corners.append({
            "index": index,
            "position_m": float(run["position_m"]),
            "radius_m": radius_m,
            "turn_in_m": start_m,
            "turn_sign": sign,
            "speed_mps": speed,
            "speed_source": speed_source,
            "depth_m": depth,
            "width_m": width_m,
            "passed": len(reasons) == 0,
            "reasons": reasons,
            "max_cross_slope": cross_slope,
            "max_long_slope": long_slope,
            "max_drop_m": max_drop,
            "max_wall_m": max_wall,
            "overlap": overlap,
            "cut_m3": cut_m3,
            "fill_m3": fill_m3,
            "earthworks_m3": earthworks_m3,
            "z_edge_m": z_edge,
            "area_m2": area,
            "polygon": [[p[0], p[1]] for p in poly],
        })

    passed = sum(1 for c in corners if c["passed"])
    count = len(corners)
    return {
        "estimate": "starting estimate, not an FIA figure",
        "decel_g": decel_g,
        "decel_label": "gravel" if abs(decel_g - DEFAULT_DECEL_G) < 0.05 else (
            "tarmac" if abs(decel_g - TARMAC_DECEL_G) < 0.05 else "custom"
        ),
        "width_m": width_m,
        "slope_limit": slope_limit,
        "step_m": step_m,
        "overlap_gap_m": float(overlap_gap_m),
        "corners": corners,
        "pass_count": passed,
        "corner_count": count,
        "summary": f"{passed}/{count} corners pass",
    }
