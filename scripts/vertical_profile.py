"""
Designed vertical profile with limited cut and fill.

The raw profile is the terrain sampled on a uniform chainage grid around the
closed lap. The designed profile is the terrain moved only as far as it must
move to meet three limits:

- forward grade at or below the layout max-gradient setting
- crest vertical-curve radius and dip vertical-curve radius
- cut or fill depth at the centreline

The fit is successive projection onto those convex limits, starting from the
terrain, with a short pull back toward the terrain between rounds so the
result stays the nearby profile rather than an arbitrary feasible one. The
grid is periodic, so z, grade and curvature agree at s = 0 and s = L.

Where the depth cap makes a grade or radius limit impossible, the cap is kept
and the broken limit is reported as a binding run. Nothing is left over the
cap to hide a grade.

viewer/vertical_profile.js is a line-for-line port of the numeric steps.
Change both together.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

PROFILE_SPACING_M = 4.0
PROFILE_MIN_SAMPLES = 64
PROFILE_MAX_SAMPLES = 2048

DEFAULT_MAX_CUT_FILL_M = 6.0
DEFAULT_MIN_CREST_RADIUS_M = 1000.0
DEFAULT_MIN_DIP_RADIUS_M = 600.0
# Solve a little inside the published limits so a profile that can comply
# still clears them after the centred radius is measured.
LIMIT_TIGHTEN = 1.04

ROUNDS = 25
CURV_REPS = 25
GRAD_REPS = 40
PULL_STEPS = 8
PULL_ALPHA = 0.35
FINAL_CURV_REPS = 60

# 1 vertical : 2 horizontal.
SIDE_SLOPE = 0.5
CATCH_STEP_M = 2.0
MAX_CATCH_M = 80.0

# Reporting. A sample inside these margins is treated as meeting the limit.
GRADIENT_MARGIN = 0.0015
RADIUS_FRACTION = 0.98
DEPTH_MARGIN_M = 0.005
DEPTH_ACTIVE_M = 0.05


def sample_count_for_lap(lap_m: float) -> int:
    """Uniform stations around the lap. Same formula in the JS port."""
    n = int(math.floor(float(lap_m) / PROFILE_SPACING_M + 0.5))
    if n < PROFILE_MIN_SAMPLES:
        n = PROFILE_MIN_SAMPLES
    if n > PROFILE_MAX_SAMPLES:
        n = PROFILE_MAX_SAMPLES
    return n


def interp_closed(distances: np.ndarray, values: np.ndarray, lap_m: float, sample_at: np.ndarray) -> np.ndarray:
    """Linear interpolation on a closed chainage. values[0] is also the value at lap_m."""
    xp = np.concatenate([np.asarray(distances, dtype=np.float64), [float(lap_m)]])
    fp = np.concatenate([np.asarray(values, dtype=np.float64), [float(values[0])]])
    return np.interp(np.asarray(sample_at, dtype=np.float64), xp, fp)


def resample_closed(
    distances: np.ndarray,
    values: np.ndarray,
    lap_m: float,
    sample_count: int,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (station chainage, resampled values, spacing) on a uniform closed grid."""
    n = int(sample_count)
    spacing = float(lap_m) / n
    stations = np.arange(n, dtype=np.float64) * spacing
    sampled = interp_closed(distances, values, lap_m, stations)
    return stations, sampled, spacing


def _curvature_pass(
    z: np.ndarray,
    box_lo: np.ndarray,
    box_hi: np.ndarray,
    c_lo: float,
    c_hi: float,
) -> np.ndarray:
    """One Jacobi sweep. z[i] is moved into the curvature band allowed by its neighbours, then the depth box."""
    zp = np.empty_like(z)
    zm = np.empty_like(z)
    zp[:-1] = z[1:]
    zp[-1] = z[0]
    zm[1:] = z[:-1]
    zm[0] = z[-1]
    lo_c = (zp + zm - c_lo) * 0.5
    hi_c = (zp + zm + c_hi) * 0.5
    lo = np.maximum(lo_c, box_lo)
    hi = np.minimum(hi_c, box_hi)
    # np.minimum(hi, np.maximum(z, lo)) matches the JS order, including lo > hi.
    target = np.minimum(hi, np.maximum(z, lo))
    target = np.where(hi_c < box_lo, box_lo, target)
    target = np.where(lo_c > box_hi, box_hi, target)
    return target


def _gradient_pass(
    z: np.ndarray,
    box_lo: np.ndarray,
    box_hi: np.ndarray,
    limit: float,
) -> None:
    """One in-place sweep. Each segment longer than the grade limit gives the excess to whichever end still has depth left."""
    n = z.shape[0]
    for i in range(n):
        j = 0 if i + 1 == n else i + 1
        dz = z[j] - z[i]
        ad = dz if dz >= 0.0 else -dz
        if ad <= limit:
            continue
        sign = 1.0 if dz > 0.0 else -1.0
        excess = ad - limit
        if sign > 0.0:
            room_i = box_hi[i] - z[i]
            room_j = z[j] - box_lo[j]
        else:
            room_i = z[i] - box_lo[i]
            room_j = box_hi[j] - z[j]
        if room_i < 0.0:
            room_i = 0.0
        if room_j < 0.0:
            room_j = 0.0
        give_i = excess * 0.5
        if give_i > room_i:
            give_i = room_i
        give_j = excess - give_i
        if give_j > room_j:
            give_j = room_j
        left = excess - give_i - give_j
        if left > 0.0:
            extra = room_i - give_i
            if extra < 0.0:
                extra = 0.0
            if extra > left:
                extra = left
            give_i += extra
            left = excess - give_i - give_j
            extra = room_j - give_j
            if extra < 0.0:
                extra = 0.0
            if extra > left:
                extra = left
            give_j += extra
        z[i] += sign * give_i
        z[j] -= sign * give_j


def _clip_box(z: np.ndarray, box_lo: np.ndarray, box_hi: np.ndarray) -> np.ndarray:
    return np.minimum(np.maximum(z, box_lo), box_hi)


def fit_profile(
    terrain_z: np.ndarray,
    spacing_m: float,
    *,
    max_grade: float,
    max_cut_fill_m: float = DEFAULT_MAX_CUT_FILL_M,
    min_crest_radius_m: float = DEFAULT_MIN_CREST_RADIUS_M,
    min_dip_radius_m: float = DEFAULT_MIN_DIP_RADIUS_M,
) -> np.ndarray:
    """
    Designed elevations on the same grid as terrain_z.

    max_grade is a fraction (0.12 for 12%), not a percent.
    The depth box is hard. Grade and radius are met wherever the box allows.
    """
    terrain = np.asarray(terrain_z, dtype=np.float64)
    n = terrain.shape[0]
    if n < 4:
        raise ValueError("profile needs at least 4 stations")
    if not (spacing_m > 0.0):
        raise ValueError("spacing must be positive")
    depth = float(max_cut_fill_m)
    if depth < 0.0:
        raise ValueError("cut/fill cap must be non-negative")
    crest_r = float(min_crest_radius_m)
    dip_r = float(min_dip_radius_m)
    if crest_r <= 0.0 or dip_r <= 0.0:
        raise ValueError("vertical-curve radii must be positive")

    grade_limit = float(max_grade) / LIMIT_TIGHTEN
    if grade_limit < 0.0:
        raise ValueError("max grade must be non-negative")
    # Smaller curvature than the published radius, so the measured radius
    # (which is at least 1/|z''|) still clears the published figure.
    kc = 1.0 / (crest_r * LIMIT_TIGHTEN)
    kd = 1.0 / (dip_r * LIMIT_TIGHTEN)
    ds = float(spacing_m)
    limit = grade_limit * ds
    c_lo = kd * ds * ds
    c_hi = kc * ds * ds
    box_lo = terrain - depth
    box_hi = terrain + depth
    z = terrain.copy()

    def curvature(reps: int) -> None:
        nonlocal z
        for _ in range(reps):
            z = _curvature_pass(z, box_lo, box_hi, c_lo, c_hi)

    def gradient(reps: int) -> None:
        for _ in range(reps):
            _gradient_pass(z, box_lo, box_hi, limit)

    for _ in range(ROUNDS):
        curvature(CURV_REPS)
        gradient(GRAD_REPS)
    for _ in range(PULL_STEPS):
        z = z + PULL_ALPHA * (terrain - z)
        z = _clip_box(z, box_lo, box_hi)
        curvature(CURV_REPS)
        gradient(GRAD_REPS)
    curvature(FINAL_CURV_REPS)
    gradient(GRAD_REPS)
    curvature(FINAL_CURV_REPS)
    # The depth box is the one limit that is never traded away.
    z = _clip_box(z, box_lo, box_hi)
    return z


def station_geometry(z: np.ndarray, spacing_m: float) -> dict:
    """Forward grade, centred grade, curvature and osculating radius at each station."""
    elev = np.asarray(z, dtype=np.float64)
    n = elev.shape[0]
    ds = float(spacing_m)
    forward = np.empty(n, dtype=np.float64)
    if n:
        forward[:-1] = (elev[1:] - elev[:-1]) / ds
        forward[-1] = (elev[0] - elev[-1]) / ds
    zp = np.empty(n, dtype=np.float64)
    zm = np.empty(n, dtype=np.float64)
    if n:
        zp[:-1] = elev[1:]
        zp[-1] = elev[0]
        zm[1:] = elev[:-1]
        zm[0] = elev[-1]
    centred = (zp - zm) / (2.0 * ds)
    curvature = (zp - 2.0 * elev + zm) / (ds * ds)
    radius = (1.0 + centred * centred) ** 1.5 / np.maximum(np.abs(curvature), 1e-12)
    return {
        "forward_grade": forward,
        "centred_grade": centred,
        "curvature": curvature,
        "radius_m": radius,
    }


def _runs(mask: np.ndarray, distance: np.ndarray, values: np.ndarray, limit: float,
          kind: str, status: str, peak: str) -> list[dict]:
    """Contiguous True runs, joined across the lap closure."""
    n = int(mask.shape[0])
    if n == 0 or not np.any(mask):
        return []
    on = mask.astype(bool)
    if np.all(on):
        if peak == "min":
            idx = int(np.argmin(values))
        else:
            idx = int(np.argmax(np.abs(values)))
        return [{
            "kind": kind,
            "status": status,
            "start_m": 0.0,
            "end_m": float(distance[-1]),
            "wraps": True,
            "peak": float(values[idx]),
            "peak_at_m": float(distance[idx]),
            "limit": float(limit),
            "samples": n,
        }]

    # Start just after a False so the first run does not split.
    start0 = 0
    for i in range(n):
        if not on[i]:
            start0 = (i + 1) % n
            break
    runs = []
    i = start0
    seen = 0
    while seen < n:
        if on[i]:
            a = i
            count = 0
            while on[i] and count < n:
                i = (i + 1) % n
                count += 1
                seen += 1
            # i is the first False (or we've lapped). The run is a .. i-1.
            b = (i - 1) % n
            if a <= b:
                idx = np.arange(a, b + 1)
                wraps = False
            else:
                idx = np.concatenate([np.arange(a, n), np.arange(0, b + 1)])
                wraps = True
            if peak == "min":
                local = idx[int(np.argmin(values[idx]))]
            else:
                local = idx[int(np.argmax(np.abs(values[idx])))]
            runs.append({
                "kind": kind,
                "status": status,
                "start_m": float(distance[a]),
                "end_m": float(distance[b]),
                "wraps": wraps,
                "peak": float(values[local]),
                "peak_at_m": float(distance[local]),
                "limit": float(limit),
                "samples": int(idx.shape[0]),
            })
        else:
            i = (i + 1) % n
            seen += 1
    return runs


def binding_report(
    terrain_z: np.ndarray,
    design_z: np.ndarray,
    spacing_m: float,
    *,
    max_grade: float,
    max_cut_fill_m: float,
    min_crest_radius_m: float,
    min_dip_radius_m: float,
) -> list[dict]:
    """
    Where a published limit is missed, or where the depth cap is tight.

    Grades are fractions. Radius limits are metres. Depth active means the
    station is within DEPTH_ACTIVE_M of the cap (the cap is what stops a
    further correction).
    """
    terrain = np.asarray(terrain_z, dtype=np.float64)
    design = np.asarray(design_z, dtype=np.float64)
    n = design.shape[0]
    ds = float(spacing_m)
    distance = np.arange(n, dtype=np.float64) * ds
    geom = station_geometry(design, ds)
    grade = geom["forward_grade"]
    curvature = geom["curvature"]
    radius = geom["radius_m"]
    depth = design - terrain

    grade_bad = np.abs(grade) > float(max_grade) + GRADIENT_MARGIN
    crest_bad = (curvature < -1e-8) & (radius < float(min_crest_radius_m) * RADIUS_FRACTION)
    dip_bad = (curvature > 1e-8) & (radius < float(min_dip_radius_m) * RADIUS_FRACTION)
    depth_bad = np.abs(depth) > float(max_cut_fill_m) + DEPTH_MARGIN_M
    depth_active = (np.abs(depth) >= float(max_cut_fill_m) - DEPTH_ACTIVE_M) & ~depth_bad

    runs = []
    runs.extend(_runs(grade_bad, distance, grade, float(max_grade), "gradient", "violated", "abs"))
    runs.extend(_runs(crest_bad, distance, radius, float(min_crest_radius_m), "crest_radius", "violated", "min"))
    runs.extend(_runs(dip_bad, distance, radius, float(min_dip_radius_m), "dip_radius", "violated", "min"))
    runs.extend(_runs(depth_bad, distance, depth, float(max_cut_fill_m), "depth", "violated", "abs"))
    runs.extend(_runs(depth_active, distance, depth, float(max_cut_fill_m), "depth", "active", "abs"))
    return runs


def geometry_summary(z: np.ndarray, spacing_m: float) -> dict:
    """Scalar grade and radius figures for one profile."""
    geom = station_geometry(z, spacing_m)
    grade = geom["forward_grade"]
    curvature = geom["curvature"]
    radius = geom["radius_m"]
    crest = curvature < -1e-8
    dip = curvature > 1e-8
    return {
        "max_grade": float(np.max(np.abs(grade))) if grade.size else 0.0,
        "min_crest_radius_m": float(np.min(radius[crest])) if np.any(crest) else None,
        "min_dip_radius_m": float(np.min(radius[dip])) if np.any(dip) else None,
    }


def _catch_side(
    sample_ground: Callable[[float, float], float],
    x: float,
    y: float,
    nx: float,
    ny: float,
    side: float,
    half_m: float,
    z_design: float,
) -> dict:
    """
    Walk outward from the road edge on a 1:2 slope until it meets the ground.

    side is +1 along the left normal and -1 along the right.
    reach_m is the distance from the centreline to the catch point.
    """
    edge = side * half_m

    def ground_at(lateral: float) -> float:
        return float(sample_ground(x + nx * lateral, y + ny * lateral))

    edge_ground = ground_at(edge)
    if abs(edge_ground - z_design) <= 1e-3:
        return {"reach_m": half_m, "slope_sign": 0.0, "daylight": True}
    slope_sign = 1.0 if edge_ground > z_design else -1.0
    prev_s = 0.0
    prev_ground = edge_ground
    prev_slope = z_design
    s = CATCH_STEP_M
    while s <= MAX_CATCH_M + 1e-9:
        ground = ground_at(side * (half_m + s))
        slope = z_design + slope_sign * (s * SIDE_SLOPE)
        crossed = (slope_sign > 0.0 and slope >= ground) or (slope_sign < 0.0 and slope <= ground)
        if crossed:
            dslope = slope - prev_slope
            dground = ground - prev_ground
            denom = dslope - dground
            frac = 0.0 if abs(denom) < 1e-12 else (prev_ground - prev_slope) / denom
            if frac < 0.0:
                frac = 0.0
            if frac > 1.0:
                frac = 1.0
            hit = prev_s + frac * (s - prev_s)
            return {"reach_m": half_m + hit, "slope_sign": slope_sign, "daylight": True}
        prev_s = s
        prev_ground = ground
        prev_slope = slope
        s += CATCH_STEP_M
    return {"reach_m": half_m + MAX_CATCH_M, "slope_sign": slope_sign, "daylight": False}


def _section_areas(
    sample_ground: Callable[[float, float], float],
    x: float,
    y: float,
    nx: float,
    ny: float,
    half_m: float,
    z_design: float,
    left: dict,
    right: dict,
) -> tuple[float, float]:
    """Cut and fill area (m²) between the original ground and the road-plus-slopes."""
    def ground_at(lateral: float) -> float:
        return float(sample_ground(x + nx * lateral, y + ny * lateral))

    # slope_sign is defined outward, so it already points the right way on both sides.
    def surface_at(lateral: float) -> float:
        ad = abs(lateral)
        if ad <= half_m + 1e-9:
            return z_design
        info = left if lateral >= 0.0 else right
        outward = ad - half_m
        reach_out = info["reach_m"] - half_m
        if outward >= reach_out - 1e-6:
            return ground_at(lateral)
        return z_design + info["slope_sign"] * outward * SIDE_SLOPE

    lat_min = -right["reach_m"]
    lat_max = left["reach_m"]
    lats = [lat_min]
    cursor = lat_min
    while cursor < lat_max - 1e-9:
        cursor = min(lat_max, cursor + CATCH_STEP_M)
        lats.append(cursor)
    for mark in (-half_m, 0.0, half_m):
        if lat_min < mark < lat_max and all(abs(mark - v) > 1e-6 for v in lats):
            lats.append(mark)
    lats.sort()

    cut = 0.0
    fill = 0.0
    prev = lats[0]
    prev_gap = ground_at(prev) - surface_at(prev)
    for lat in lats[1:]:
        gap = ground_at(lat) - surface_at(lat)
        width = lat - prev
        # Trapezoid. Split if the gap changes sign so cut and fill stay apart.
        if prev_gap == 0.0 and gap == 0.0:
            prev, prev_gap = lat, gap
            continue
        if prev_gap * gap < 0.0:
            frac = abs(prev_gap) / (abs(prev_gap) + abs(gap))
            mid = prev + frac * width
            w0 = mid - prev
            w1 = lat - mid
            cut += (max(prev_gap, 0.0) * w0) * 0.5
            fill += (max(-prev_gap, 0.0) * w0) * 0.5
            cut += (max(gap, 0.0) * w1) * 0.5
            fill += (max(-gap, 0.0) * w1) * 0.5
        else:
            avg = 0.5 * (prev_gap + gap)
            if avg >= 0.0:
                cut += avg * width
            else:
                fill += -avg * width
        prev, prev_gap = lat, gap
    return cut, fill


def design_sections(
    x: np.ndarray,
    y: np.ndarray,
    design_z: np.ndarray,
    width_m: float,
    sample_ground: Callable[[float, float], float],
) -> list[dict]:
    """Per-station road frame and 1:2 catch points. Normals point to the left of travel."""
    xs = np.asarray(x, dtype=np.float64)
    ys = np.asarray(y, dtype=np.float64)
    design = np.asarray(design_z, dtype=np.float64)
    n = design.shape[0]
    half = float(width_m) * 0.5
    if half <= 0.0:
        raise ValueError("track width must be positive")
    sections = []
    for i in range(n):
        prev = n - 1 if i == 0 else i - 1
        nxt = 0 if i + 1 == n else i + 1
        tx = float(xs[nxt] - xs[prev])
        ty = float(ys[nxt] - ys[prev])
        length = math.hypot(tx, ty)
        if length < 1e-9:
            tx, ty, length = 1.0, 0.0, 1.0
        tx /= length
        ty /= length
        nx = -ty
        ny = tx
        zx = float(xs[i])
        zy = float(ys[i])
        zd = float(design[i])
        left = _catch_side(sample_ground, zx, zy, nx, ny, 1.0, half, zd)
        right = _catch_side(sample_ground, zx, zy, nx, ny, -1.0, half, zd)
        sections.append({
            "x": zx,
            "y": zy,
            "z_design": zd,
            "nx": nx,
            "ny": ny,
            "half_m": half,
            "left": left,
            "right": right,
        })
    return sections


def earthworks(
    x: np.ndarray,
    y: np.ndarray,
    terrain_z: np.ndarray,
    design_z: np.ndarray,
    spacing_m: float,
    width_m: float,
    sample_ground: Callable[[float, float], float],
) -> dict:
    """
    1:2 side slopes from each road edge back to the terrain.

    Volumes are average end areas, in cubic metres. Max cut and fill are the
    centreline depths (terrain minus design is cut).
    """
    terrain = np.asarray(terrain_z, dtype=np.float64)
    design = np.asarray(design_z, dtype=np.float64)
    n = design.shape[0]
    ds = float(spacing_m)
    sections = design_sections(x, y, design, width_m, sample_ground)
    cut_m3 = 0.0
    fill_m3 = 0.0
    max_cut = 0.0
    max_fill = 0.0
    daylight_miss = []
    for i, section in enumerate(sections):
        centre_cut = float(terrain[i] - design[i])
        if centre_cut > max_cut:
            max_cut = centre_cut
        if -centre_cut > max_fill:
            max_fill = -centre_cut
        if not section["left"]["daylight"] or not section["right"]["daylight"]:
            daylight_miss.append(i * ds)
        area_cut, area_fill = _section_areas(
            sample_ground,
            section["x"],
            section["y"],
            section["nx"],
            section["ny"],
            section["half_m"],
            section["z_design"],
            section["left"],
            section["right"],
        )
        cut_m3 += area_cut * ds
        fill_m3 += area_fill * ds
    return {
        "max_cut_m": max_cut,
        "max_fill_m": max_fill,
        "cut_m3": cut_m3,
        "fill_m3": fill_m3,
        "total_m3": cut_m3 + fill_m3,
        "daylight_miss_m": daylight_miss,
        "side_slope": "1:2",
        "sections": sections,
    }


def build_vertical_profile(
    x: np.ndarray,
    y: np.ndarray,
    distances: np.ndarray,
    terrain_z: np.ndarray,
    lap_m: float,
    width_m: float,
    sample_ground: Callable[[float, float], float],
    *,
    max_grade: float,
    max_cut_fill_m: float = DEFAULT_MAX_CUT_FILL_M,
    min_crest_radius_m: float = DEFAULT_MIN_CREST_RADIUS_M,
    min_dip_radius_m: float = DEFAULT_MIN_DIP_RADIUS_M,
) -> dict:
    """
    Full closed-lap design.

    max_grade is a fraction. The returned design_at_points lines up with the
    input centreline samples. The uniform grid is the one the limits are
    enforced on, and it is periodic.
    """
    n = sample_count_for_lap(lap_m)
    stations, terrain_u, spacing = resample_closed(distances, terrain_z, lap_m, n)
    _, x_u, _ = resample_closed(distances, x, lap_m, n)
    _, y_u, _ = resample_closed(distances, y, lap_m, n)
    design_u = fit_profile(
        terrain_u,
        spacing,
        max_grade=max_grade,
        max_cut_fill_m=max_cut_fill_m,
        min_crest_radius_m=min_crest_radius_m,
        min_dip_radius_m=min_dip_radius_m,
    )
    design_at_points = interp_closed(stations, design_u, lap_m, distances)
    runs = binding_report(
        terrain_u,
        design_u,
        spacing,
        max_grade=max_grade,
        max_cut_fill_m=max_cut_fill_m,
        min_crest_radius_m=min_crest_radius_m,
        min_dip_radius_m=min_dip_radius_m,
    )
    works = earthworks(x_u, y_u, terrain_u, design_u, spacing, width_m, sample_ground)
    terrain_geom = geometry_summary(terrain_u, spacing)
    design_geom = geometry_summary(design_u, spacing)
    violated = [r for r in runs if r["status"] == "violated"]
    return {
        "sample_count": n,
        "spacing_m": spacing,
        "lap_m": float(lap_m),
        "distance_m": stations,
        "x": x_u,
        "y": y_u,
        "terrain_z": terrain_u,
        "design_z": design_u,
        "design_at_points": design_at_points,
        "limits": {
            "max_grade": float(max_grade),
            "max_cut_fill_m": float(max_cut_fill_m),
            "min_crest_radius_m": float(min_crest_radius_m),
            "min_dip_radius_m": float(min_dip_radius_m),
        },
        "terrain": terrain_geom,
        "design": design_geom,
        "earthworks": {k: v for k, v in works.items() if k != "sections"},
        "sections": works["sections"],
        "bindings": runs,
        "feasible": len(violated) == 0,
        "join_step_m": float(design_u[0] - design_u[-1]),
    }


def run_covers(run: dict, distance_m: float, spacing_m: float) -> bool:
    """True if distance_m sits on a station covered by the run."""
    tol = float(spacing_m) * 0.51
    start = float(run["start_m"])
    end = float(run["end_m"])
    d = float(distance_m)
    if run.get("wraps"):
        return d >= start - tol or d <= end + tol
    return start - tol <= d <= end + tol


def violations_are_reported(
    terrain_z: np.ndarray,
    design_z: np.ndarray,
    spacing_m: float,
    runs: list[dict],
    *,
    max_grade: float,
    max_cut_fill_m: float,
    min_crest_radius_m: float,
    min_dip_radius_m: float,
) -> list[str]:
    """Human-readable gaps: a limit missed at a station with no covering run."""
    terrain = np.asarray(terrain_z, dtype=np.float64)
    design = np.asarray(design_z, dtype=np.float64)
    ds = float(spacing_m)
    geom = station_geometry(design, ds)
    grade = geom["forward_grade"]
    curvature = geom["curvature"]
    radius = geom["radius_m"]
    depth = design - terrain
    problems = []
    kind_of = (
        ("gradient", np.abs(grade) > float(max_grade) + GRADIENT_MARGIN),
        ("crest_radius", (curvature < -1e-8) & (radius < float(min_crest_radius_m) * RADIUS_FRACTION)),
        ("dip_radius", (curvature > 1e-8) & (radius < float(min_dip_radius_m) * RADIUS_FRACTION)),
        ("depth", np.abs(depth) > float(max_cut_fill_m) + DEPTH_MARGIN_M),
    )
    for kind, mask in kind_of:
        covering = [r for r in runs if r["kind"] == kind and r["status"] == "violated"]
        for i in np.flatnonzero(mask):
            d = i * ds
            if not any(run_covers(r, d, ds) for r in covering):
                problems.append(f"{kind} at {d:.1f} m is not in the binding report")
                if len(problems) > 8:
                    return problems
    return problems
