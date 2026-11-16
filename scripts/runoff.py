"""Run-off zones as a layout constraint.

Starting estimates, not FIA figures. At each corner from the shared detector,
the zone is a fan on the outside of the turn. Its inner boundary is the
outside track edge from before turn-in through the exit taper. From the
braking point the fan extends along the straight-on approach tangent for
v^2 / (2 a), widens as the corner turns away from that line, and tapers back
to the edge on the exit.

Depth is v^2 / (2 a). Deceleration defaults to gravel at 0.5 g; tarmac is
about 0.9 g and is the other setting the viewer offers. v is the speed
profile's entry speed when a trace is supplied, otherwise the corner-radius
proxy sqrt(1.5 g R). Width is the lateral cap.

Where the fan meets another part of the lap it is clipped, with a small
buffer, and the shortfall is a fail. Terrain inside the clipped fan still has
to stay under the slope limit and within the drop/wall step of the designed
road-edge height.

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
# 5 m chords left a hairpin edge more than 0.5 m off the track.
EDGE_STEP_M = 2.5
LEAD_MIN_M = 20.0
LEAD_FRAC = 0.15
LEAD_MAX_M = 60.0
TAPER_MIN_M = 30.0
TAPER_FRAC = 0.45
TAPER_MAX_M = 140.0
# Clear of the other track edge by this much after the half-width.
CLIP_BUFFER_M = 3.0
CLIP_SEARCH_STEPS = 18


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


def _forward(start_m: float, end_m: float, lap_m: float) -> float:
    if not (lap_m > 0):
        return end_m - start_m
    d = (end_m - start_m) % lap_m
    if d < 0:
        d += lap_m
    return d


def _gap_to_window(distance_m: float, s0: float, s2: float, lap_m: float) -> float:
    """Chainage gap from distance_m to the forward window [s0, s2]."""
    if not (lap_m > 0):
        return 0.0
    span = _forward(s0, s2, lap_m)
    if span >= lap_m - 1e-6:
        return 0.0
    x = _forward(s0, distance_m, lap_m)
    if x <= span:
        return 0.0
    return min(x - span, lap_m - x)


def _insert_chain(samples: list[float], s0: float, lap_m: float, target: float) -> None:
    fwd_t = _forward(s0, target, lap_m)
    for i, s in enumerate(samples):
        fwd = _forward(s0, s, lap_m)
        if abs(fwd - fwd_t) < 0.05:
            return
        if fwd > fwd_t:
            samples.insert(i, target % lap_m if lap_m > 0 else target)
            return
    samples.append(target % lap_m if lap_m > 0 else target)


def _chain_samples(s0: float, s2: float, lap_m: float, step: float, extras: list[float]) -> list[float]:
    span = _forward(s0, s2, lap_m)
    if span < 1e-3:
        return [s0 % lap_m if lap_m > 0 else s0]
    n = max(1, int(math.ceil(span / step)))
    samples = []
    for i in range(n + 1):
        s = s0 + span * i / n
        samples.append(s % lap_m if lap_m > 0 else s)
    for extra in extras:
        _insert_chain(samples, s0, lap_m, extra)
    return samples


def _edge_frame(xy, distances, lap_m, distance_m, sign: int, half_width: float):
    px, py = _sample_xy(xy, distances, lap_m, distance_m)
    tx, ty = _travel_tangent(xy, distances, lap_m, distance_m)
    nx, ny = _outside_normal(tx, ty, sign)
    return px + nx * half_width, py + ny * half_width, tx, ty, nx, ny


def _window_lengths(depth: float, corner_len: float, lap_m: float) -> tuple[float, float]:
    lead = min(LEAD_MAX_M, max(LEAD_MIN_M, LEAD_FRAC * depth))
    taper = min(TAPER_MAX_M, max(TAPER_MIN_M, TAPER_FRAC * depth))
    room = max(0.0, lap_m - corner_len)
    if lead + taper > room * 0.85 and lead + taper > 1e-6:
        scale = room * 0.85 / (lead + taper)
        lead *= scale
        taper *= scale
    if room > 8.0:
        lead = max(8.0, lead) if lead > 0 else 8.0
        if lead + taper > room * 0.85:
            taper = max(0.0, room * 0.85 - lead)
    return lead, taper


def _blockers(track_x, track_y, track_d, lap_m, s0, s2, overlap_gap_m):
    found = []
    n = len(track_d)
    for i in range(n):
        gap = _gap_to_window(float(track_d[i]), s0, s2, lap_m)
        if gap <= overlap_gap_m:
            continue
        found.append((float(track_x[i]), float(track_y[i]), float(track_d[i])))
    return found


def _segment_hit(ax, ay, bx, by, blockers, radius: float):
    """Chainage of the closest blocker the segment comes within radius of."""
    best = None
    best_d = radius
    for px, py, ps in blockers:
        dist = _dist_point_segment(px, py, ax, ay, bx, by)
        if dist < best_d:
            best_d = dist
            best = ps
    return best


def _segments_cross(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    """Proper crossing. Shared endpoints do not count."""
    rx = bx - ax
    ry = by - ay
    sx = dx - cx
    sy = dy - cy
    den = rx * sy - ry * sx
    if abs(den) < 1e-9:
        return False
    qx = cx - ax
    qy = cy - ay
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    return 1e-4 < t < 1.0 - 1e-4 and 1e-4 < u < 1.0 - 1e-4


def _in_tri(px, py, ax, ay, bx, by, cx, cy) -> bool:
    c1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    c2 = (cx - bx) * (py - by) - (cy - by) * (px - bx)
    c3 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
    if c1 >= -1e-6 and c2 >= -1e-6 and c3 >= -1e-6:
        return True
    return c1 <= 1e-6 and c2 <= 1e-6 and c3 <= 1e-6


def _quad_hit(ax, ay, bx, by, cx, cy, dx, dy, blockers, radius: float):
    """Chainage of a blocker inside the strip quad or within radius of its outer edges.

    The quad is inner A, outer B, inner C, outer D. The inner edge A-C is the
    track edge and is not a reason to pull the zone off the track.
    """
    best = None
    best_d = radius
    for px, py, ps in blockers:
        inside = _in_tri(px, py, ax, ay, bx, by, cx, cy) or _in_tri(px, py, bx, by, dx, dy, cx, cy)
        dist = _dist_point_segment(px, py, bx, by, dx, dy)
        for ex, ey, fx, fy in ((ax, ay, bx, by), (cx, cy, dx, dy)):
            edge = _dist_point_segment(px, py, ex, ey, fx, fy)
            if edge < dist:
                dist = edge
        if inside or dist < best_d:
            best_d = 0.0 if inside else dist
            best = ps
    return best


def _shorten_ray(ax, ay, bx, by, blockers, radius: float):
    """Farthest point from A toward B whose segment stays clear of blockers."""
    hit = _segment_hit(ax, ay, bx, by, blockers, radius)
    if hit is None:
        return bx, by, None
    lo = 0.0
    hi = 1.0
    found = hit
    for _ in range(CLIP_SEARCH_STEPS):
        mid = (lo + hi) * 0.5
        mx = ax + (bx - ax) * mid
        my = ay + (by - ay) * mid
        step_hit = _segment_hit(ax, ay, mx, my, blockers, radius)
        if step_hit is None:
            lo = mid
        else:
            hi = mid
            found = step_hit
    return ax + (bx - ax) * lo, ay + (by - ay) * lo, found


def _build_fan(
    xy,
    distances,
    lap_m,
    start_m,
    end_m,
    sign,
    half_width,
    depth,
    width_m,
    escape_tx,
    escape_ty,
    track_x,
    track_y,
    track_d,
    overlap_gap_m,
):
    """Outside fan. inner[i] is on the track edge; outer[i] is the clipped rim."""
    corner_len = _forward(start_m, end_m, lap_m)
    if corner_len < 1.0:
        corner_len = 1.0
    lead, taper = _window_lengths(depth, corner_len, lap_m)
    s0 = (start_m - lead) % lap_m if lap_m > 0 else start_m - lead
    s2 = (end_m + taper) % lap_m if lap_m > 0 else end_m + taper
    window = _forward(s0, s2, lap_m)
    samples = _chain_samples(s0, s2, lap_m, EDGE_STEP_M, [start_m, end_m])

    bx, by, _rtx, _rty, nx, ny = _edge_frame(xy, distances, lap_m, start_m, sign, half_width)
    tx, ty = escape_tx, escape_ty
    blockers = _blockers(track_x, track_y, track_d, lap_m, s0, s2, overlap_gap_m)
    radius = half_width + CLIP_BUFFER_M

    inner = []
    frames = []
    for s in samples:
        ex, ey, _ltx, _lty, lnx, lny = _edge_frame(xy, distances, lap_m, s, sign, half_width)
        inner.append((ex, ey, s))
        frames.append((lnx, lny))

    # The braking sample is the one at start_m.
    brake_index = 0
    best_brake = 1e18
    for index, (_ex, _ey, s) in enumerate(inner):
        gap = min(_forward(start_m, s, lap_m), _forward(s, start_m, lap_m))
        if gap < best_brake:
            best_brake = gap
            brake_index = index

    # The straight escape is the outer edge from the brake out to depth.
    # Stop it once the corner has turned off the approach, so a hairpin
    # does not keep the straight line across the rest of the bend.
    escape_end = brake_index
    for index in range(brake_index, len(inner)):
        ex, ey, _s = inner[index]
        lnx, lny = frames[index]
        agree = lnx * nx + lny * ny
        along = (ex - bx) * tx + (ey - by) * ty
        if index > brake_index and agree < 0.5:
            break
        escape_end = index
        if index > brake_index and along >= depth:
            break
    if escape_end == brake_index and brake_index + 1 < len(inner):
        escape_end = brake_index + 1
    # Leave the exit samples for the taper back to the track edge.
    taper_samples = max(2, int(round(taper / EDGE_STEP_M)))
    taper_limit = len(inner) - 1 - taper_samples
    if taper_limit < brake_index + 1:
        taper_limit = brake_index + 1 if brake_index + 1 < len(inner) else brake_index
    if escape_end > taper_limit:
        escape_end = taper_limit

    desired = []
    for index, (ex, ey, s) in enumerate(inner):
        lnx, lny = frames[index]
        if index <= brake_index:
            # Lead-in stays on the outside edge, so the zone starts there.
            desired.append((ex, ey))
            continue
        if index <= escape_end:
            span = escape_end - brake_index
            u = (index - brake_index) / span
            cross = (ex - bx) * nx + (ey - by) * ny
            reach = -cross if cross < 0.0 else 0.0
            if reach > width_m:
                desired.append((ex + nx * width_m, ey + ny * width_m))
            else:
                desired.append((bx + tx * (depth * u), by + ty * (depth * u)))
            continue
        from_brake = _forward(start_m, s, lap_m)
        if from_brake <= corner_len + 0.2:
            u = 0.0 if corner_len < 1e-6 else from_brake / corner_len
            if u < 0.0:
                u = 0.0
            elif u > 1.0:
                u = 1.0
            h = width_m * u
        else:
            into = from_brake - corner_len
            u = 0.0 if taper < 1e-6 else into / taper
            if u < 0.0:
                u = 0.0
            elif u > 1.0:
                u = 1.0
            h = width_m * (1.0 - u)
        desired.append((ex + lnx * h, ey + lny * h))

    ts = []
    overlap_s = None
    apex_s = None
    for index, (ex, ey, _s) in enumerate(inner):
        ox, oy = desired[index]
        cx, cy, hit = _shorten_ray(ex, ey, ox, oy, blockers, radius)
        dx = ox - ex
        dy = oy - ey
        len2 = dx * dx + dy * dy
        if len2 < 1e-18:
            ts.append(0.0)
        else:
            ts.append(((cx - ex) * dx + (cy - ey) * dy) / len2)
        if hit is not None:
            if overlap_s is None:
                overlap_s = hit
            if index == escape_end:
                apex_s = hit

    def _at(index: int, scale: float):
        ex, ey, _s = inner[index]
        return (
            ex + (desired[index][0] - ex) * scale,
            ey + (desired[index][1] - ey) * scale,
        )

    # Rays can miss another section while the chord between their ends cuts it.
    # Pull neighbouring rays back until each strip quad is clear.
    for _pass in range(6):
        moved = False
        for index in range(len(inner) - 1):
            ax, ay = _at(index, ts[index])
            bx2, by2 = _at(index + 1, ts[index + 1])
            iex, iey, _s = inner[index]
            jex, jey, _s2 = inner[index + 1]
            if _quad_hit(iex, iey, ax, ay, jex, jey, bx2, by2, blockers, radius) is None:
                continue
            lo = 0.0
            hi = 1.0
            found = None
            for _step in range(CLIP_SEARCH_STEPS):
                mid = (lo + hi) * 0.5
                ax, ay = _at(index, ts[index] * mid)
                bx2, by2 = _at(index + 1, ts[index + 1] * mid)
                hit = _quad_hit(iex, iey, ax, ay, jex, jey, bx2, by2, blockers, radius)
                if hit is None:
                    lo = mid
                else:
                    hi = mid
                    found = hit
            ts[index] *= lo
            ts[index + 1] *= lo
            if found is not None:
                if overlap_s is None:
                    overlap_s = found
                if index == escape_end or index + 1 == escape_end:
                    apex_s = found
            moved = True
        if not moved:
            break

    def _outer_cross(i: int, j: int) -> bool:
        if abs(i - j) <= 1:
            return False
        ax, ay = _at(i, ts[i])
        bx2, by2 = _at(i + 1, ts[i + 1])
        cx, cy = _at(j, ts[j])
        dx, dy = _at(j + 1, ts[j + 1])
        return _segments_cross(ax, ay, bx2, by2, cx, cy, dx, dy)

    for _round in range(len(inner)):
        cleared = True
        last = len(inner) - 1
        for i in range(last):
            for j in range(i):
                if not _outer_cross(i, j):
                    continue
                cleared = False
                # Drop the folded side onto the edge. If it already is the
                # edge, the earlier side is the one crossing the track.
                if ts[i] > 1e-6 or ts[i + 1] > 1e-6:
                    ts[i] = 0.0
                    ts[i + 1] = 0.0
                else:
                    ts[j] = 0.0
                    ts[j + 1] = 0.0
        if cleared:
            break

    if apex_s is not None:
        overlap_s = apex_s

    outer = [_at(index, ts[index]) for index in range(len(inner))]

    # Depth is how far the (possibly clipped) escape reaches along the approach.
    available = (outer[escape_end][0] - bx) * tx + (outer[escape_end][1] - by) * ty
    if available < 0.0:
        available = 0.0

    ring = [(p[0], p[1]) for p in inner]
    for ox, oy in reversed(outer):
        if ring and abs(ring[-1][0] - ox) < 1e-9 and abs(ring[-1][1] - oy) < 1e-9:
            continue
        ring.append((ox, oy))
    if len(ring) > 1 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9:
        ring.pop()

    return {
        "inner": inner,
        "outer": outer,
        "polygon": ring,
        "brake_index": brake_index,
        "available_depth_m": available,
        "overlap_s": overlap_s,
        "tx": tx,
        "ty": ty,
        "nx": nx,
        "ny": ny,
        "s0": s0,
        "s2": s2,
        "window_m": window,
    }


def _nearest_edge_s(inner, px: float, py: float, lap_m: float) -> float:
    best_s = inner[0][2]
    best_d = 1e18
    n = len(inner)
    for i in range(n - 1):
        ax, ay, sa = inner[i]
        bx, by, sb = inner[i + 1]
        dx = bx - ax
        dy = by - ay
        len2 = dx * dx + dy * dy
        if len2 < 1e-12:
            t = 0.0
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / len2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        qx = ax + dx * t
        qy = ay + dy * t
        dist = (px - qx) * (px - qx) + (py - qy) * (py - qy)
        if dist < best_d:
            best_d = dist
            best_s = (sa + t * _forward(sa, sb, lap_m)) % lap_m if lap_m > 0 else sa + t * (sb - sa)
    return best_s


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
        fan = _build_fan(
            resampled, resampled_d, resample_lap, start_m, end_m, sign, half_width, depth, width_m,
            tx, ty, x, y, distances, float(overlap_gap_m),
        )
        # Straight-on escape is the approach tangent. Slope is measured in that frame.
        ex, ey = tx, ty
        px, py = nx, ny
        poly = fan["polygon"]
        inner = fan["inner"]

        z_edge = _interp_closed(distances, design_z, lap_m, start_m)
        samples = _interior_samples(poly, SAMPLE_SPACING_M, sample_ground)
        usable = []
        edge_z = []
        for ix, iy, sx, sy, sz in samples:
            edge_s = _nearest_edge_s(inner, sx, sy, lap_m)
            z_ref = _interp_closed(distances, design_z, lap_m, edge_s)
            if not math.isfinite(sz):
                sz = z_ref - step_m - 1.0
            usable.append((ix, iy, sx, sy, sz))
            edge_z.append(z_ref)
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
        overlap_s = fan["overlap_s"]
        available = float(fan["available_depth_m"])
        if available > depth:
            available = depth
        overlap = overlap_s is not None
        if overlap:
            at = start_m if overlap_s is None else float(overlap_s)
            reasons.append(f"overlaps track section at s={int(round(at))}")
        terrain_fail = any(not r.startswith("overlaps") for r in reasons)
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
            "available_depth_m": available,
            "width_m": width_m,
            "passed": len(reasons) == 0,
            "reasons": reasons,
            "max_cross_slope": cross_slope,
            "max_long_slope": long_slope,
            "max_drop_m": max_drop,
            "max_wall_m": max_wall,
            "overlap": overlap,
            "overlap_s": None if overlap_s is None else float(overlap_s),
            "cut_m3": cut_m3,
            "fill_m3": fill_m3,
            "earthworks_m3": earthworks_m3,
            "z_edge_m": z_edge,
            "area_m2": area,
            "polygon": [[p[0], p[1]] for p in poly],
            "inner": [[p[0], p[1], p[2]] for p in inner],
            "outer": [[p[0], p[1]] for p in fan["outer"]],
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
