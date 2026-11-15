#!/usr/bin/env python3
"""
Layout checker for track designs on Nishiwaki Basin terrain.

Validates layouts against rough F1-style heuristics:
- Lap length: 3.5-4.5 km target
- Minimum corner radius: ≥15m
- Maximum longitudinal gradient: ≤12% on the designed profile (terrain shown beside it)
- Crest radius ≥1000 m and dip radius ≥600 m on the designed profile
- Maximum cross-slope
- No self-intersection
- Stays inside DEM bounds
- Cut/fill with a 6 m cap and 1:2 side slopes
- Longest straight
- Viewshed from best rim viewpoint

Usage:
    python3 scripts/layout_check.py data/sites/nishiwaki_basin/layouts/layout_a.json
    python3 scripts/layout_check.py --all  # Check all layouts
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio

from corner_detection import analyze_corners_xy
from road_profile import (
    GRADIENT_WINDOW_M,
    ROAD_PROFILE_SMOOTH_M,
    grade_profile,
    max_abs_gradient_pct,
    mean_sample_spacing,
)
from vertical_profile import (
    DEFAULT_MAX_CUT_FILL_M,
    DEFAULT_MIN_CREST_RADIUS_M,
    DEFAULT_MIN_DIP_RADIUS_M,
    MAX_CATCH_M,
    build_vertical_profile,
)

SITE_DIR = Path(__file__).parent.parent / "data" / "sites" / "nishiwaki_basin"
LAYOUTS_DIR = SITE_DIR / "layouts"

LAP_LENGTH_MIN = 3500
LAP_LENGTH_MAX = 4500
MIN_CORNER_RADIUS = 15
MAX_GRADIENT_PCT = 12
MAX_CROSS_SLOPE_PCT = 8
MAX_CUT_FILL_M = DEFAULT_MAX_CUT_FILL_M
MIN_CREST_RADIUS_M = DEFAULT_MIN_CREST_RADIUS_M
MIN_DIP_RADIUS_M = DEFAULT_MIN_DIP_RADIUS_M
GRADIENT_LIMIT_FOR_SMOOTHING = 0.12
SPLINE_SAMPLE_SPACING_M = 2


@dataclass
class CheckResult:
    """Result of a single check."""
    name: str
    passed: bool
    value: float | str
    target: str
    detail: str = ""


@dataclass
class LayoutReport:
    """Full validation report for a layout."""
    layout_name: str
    direction: str
    checks: list[CheckResult]
    all_passed: bool
    summary: dict


def load_dem(site_dir: Path):
    """Load the site DEM."""
    dem_path = site_dir / "dem.tif"
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    return dem, transform


def load_site_info(site_dir: Path) -> dict:
    """Load site metadata."""
    with open(site_dir / "site.json") as f:
        return json.load(f)


def load_layout(path: Path) -> dict:
    """Load a layout JSON file."""
    with open(path) as f:
        return json.load(f)


def catmull_rom_spline(points: np.ndarray, num_samples: int, closed: bool = True) -> np.ndarray:
    """
    Generate a centripetal Catmull-Rom spline through the given points.
    
    Args:
        points: Nx2 array of control points
        num_samples: Total number of output samples
        closed: Whether the curve is closed
        
    Returns:
        Mx2 array of interpolated points
    """
    n = len(points)
    if n < 2:
        return points.copy()
    
    if closed:
        pts = np.vstack([points[-1], points, points[0], points[1]])
    else:
        pts = np.vstack([points[0], points, points[-1]])
    
    def tj(ti, pi, pj, alpha=0.5):
        d = np.linalg.norm(pj - pi)
        return ti + d ** alpha
    
    result = []
    num_segments = n if closed else n - 1
    samples_per_segment = num_samples // num_segments
    
    for i in range(num_segments):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        
        t0 = 0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        
        t_range = np.linspace(t1, t2, samples_per_segment, endpoint=False)
        
        for t in t_range:
            a1 = (t1 - t) / (t1 - t0 + 1e-10) * p0 + (t - t0) / (t1 - t0 + 1e-10) * p1
            a2 = (t2 - t) / (t2 - t1 + 1e-10) * p1 + (t - t1) / (t2 - t1 + 1e-10) * p2
            a3 = (t3 - t) / (t3 - t2 + 1e-10) * p2 + (t - t2) / (t3 - t2 + 1e-10) * p3
            
            b1 = (t2 - t) / (t2 - t0 + 1e-10) * a1 + (t - t0) / (t2 - t0 + 1e-10) * a2
            b2 = (t3 - t) / (t3 - t1 + 1e-10) * a2 + (t - t1) / (t3 - t1 + 1e-10) * a3
            
            c = (t2 - t) / (t2 - t1 + 1e-10) * b1 + (t - t1) / (t2 - t1 + 1e-10) * b2
            result.append(c)
    
    return np.array(result)


def get_control_points_local_m(layout: dict, site_info: dict) -> np.ndarray:
    """Extract control points in local meters."""
    coord_sys = layout.get("coordinate_system", "local_m")
    points = layout["control_points"]
    
    if coord_sys == "local_m":
        return np.array([[p["x"], p["y"]] for p in points])
    elif coord_sys == "latlon":
        lat0 = site_info["lat"]
        lon0 = site_info["lon"]
        m_per_deg_lat = 111320
        m_per_deg_lon = 111320 * math.cos(math.radians(lat0))
        
        result = []
        for p in points:
            x = (p["lon"] - lon0) * m_per_deg_lon
            y = (p["lat"] - lat0) * m_per_deg_lat
            result.append([x, y])
        return np.array(result)
    else:
        raise ValueError(f"Unknown coordinate system: {coord_sys}")


def sample_dem(dem: np.ndarray, transform, x: np.ndarray, y: np.ndarray, 
               site_info: dict) -> np.ndarray:
    """Sample DEM at local (x, y) coordinates. Returns elevation in meters."""
    lat0 = site_info["lat"]
    lon0 = site_info["lon"]
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * math.cos(math.radians(lat0))
    
    lon = lon0 + x / m_per_deg_lon
    lat = lat0 + y / m_per_deg_lat
    
    rows, cols = dem.shape
    left = transform.c
    top = transform.f
    res_x = transform.a
    res_y = -transform.e
    
    col = (lon - left) / res_x - 0.5
    row = (top - lat) / res_y - 0.5
    
    col = np.clip(col, 0, cols - 1.001)
    row = np.clip(row, 0, rows - 1.001)
    
    c0 = np.floor(col).astype(int)
    r0 = np.floor(row).astype(int)
    c0 = np.minimum(c0, cols - 2)
    r0 = np.minimum(r0, rows - 2)
    
    dc = col - c0
    dr = row - r0
    
    e = (dem[r0, c0] * (1 - dc) * (1 - dr) +
         dem[r0, c0 + 1] * dc * (1 - dr) +
         dem[r0 + 1, c0] * (1 - dc) * dr +
         dem[r0 + 1, c0 + 1] * dc * dr)
    
    return e


def interpolate_layout(layout: dict, dem: np.ndarray, transform, 
                       site_info: dict, spacing_m: float = SPLINE_SAMPLE_SPACING_M):
    """
    Interpolate layout control points and drape on terrain.
    
    Returns:
        points: Nx3 array (x, y, z) in local meters
        distances: cumulative distance along track
    """
    control_pts = get_control_points_local_m(layout, site_info)
    
    total_approx_length = 0
    for i in range(len(control_pts)):
        j = (i + 1) % len(control_pts)
        total_approx_length += np.linalg.norm(control_pts[j] - control_pts[i])
    
    num_samples = max(100, int(total_approx_length / spacing_m))
    
    xy = catmull_rom_spline(control_pts, num_samples, closed=True)
    
    z = sample_dem(dem, transform, xy[:, 0], xy[:, 1], site_info)
    
    points = np.column_stack([xy, z])
    
    diffs = np.diff(points, axis=0, append=points[:1])
    segment_lengths = np.sqrt(np.sum(diffs[:, :2]**2, axis=1))
    distances = np.cumsum(np.concatenate([[0], segment_lengths[:-1]]))
    
    return points, distances


def check_self_intersection(points: np.ndarray, track_width: float) -> tuple[bool, list]:
    """Check if the track self-intersects. Returns (has_intersection, intersection_points)."""
    n = len(points)
    intersections = []
    
    def segments_intersect(p1, p2, p3, p4):
        d1 = p2 - p1
        d2 = p4 - p3
        d3 = p3 - p1
        
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-10:
            return None
        
        t = (d3[0] * d2[1] - d3[1] * d2[0]) / cross
        u = (d3[0] * d1[1] - d3[1] * d1[0]) / cross
        
        if 0.05 < t < 0.95 and 0.05 < u < 0.95:
            return p1 + t * d1
        return None
    
    for i in range(n):
        for j in range(i + 3, n):
            if j == (i - 1) % n or j == (i + 1) % n:
                continue
            
            p1 = points[i, :2]
            p2 = points[(i + 1) % n, :2]
            p3 = points[j, :2]
            p4 = points[(j + 1) % n, :2]
            
            intersection = segments_intersect(p1, p2, p3, p4)
            if intersection is not None:
                intersections.append(intersection)
    
    return len(intersections) > 0, intersections


def check_bounds(points: np.ndarray, dem: np.ndarray, transform, 
                 site_info: dict, margin_m: float = 50) -> tuple[bool, float]:
    """Check if all points are within DEM bounds with margin."""
    lat0 = site_info["lat"]
    lon0 = site_info["lon"]
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * math.cos(math.radians(lat0))
    
    rows, cols = dem.shape
    left = transform.c
    top = transform.f
    res_x = transform.a
    res_y = -transform.e
    
    lon = lon0 + points[:, 0] / m_per_deg_lon
    lat = lat0 + points[:, 1] / m_per_deg_lat
    
    left_bound = left + margin_m / m_per_deg_lon
    right_bound = left + cols * res_x - margin_m / m_per_deg_lon
    bottom_bound = top - rows * res_y + margin_m / m_per_deg_lat
    top_bound = top - margin_m / m_per_deg_lat
    
    inside = ((lon >= left_bound) & (lon <= right_bound) & 
              (lat >= bottom_bound) & (lat <= top_bound))
    
    outside_count = (~inside).sum()
    outside_pct = outside_count / len(points) * 100
    
    return outside_count == 0, outside_pct


def _fmt_radius(value) -> str:
    if value is None:
        return "—"
    return f"{value:.0f} m"


def _binding_brief(runs: list[dict], limit: int = 3) -> str:
    """One line naming the runs where a limit is missed or the depth cap is tight."""
    interesting = [r for r in runs if r["status"] == "violated" or r["kind"] == "depth"]
    if not interesting:
        return "no limit is binding"
    parts = []
    for run in interesting[:limit]:
        if run["kind"] == "gradient":
            label = f"gradient {abs(run['peak']) * 100:.1f}%"
        elif run["kind"] == "crest_radius":
            label = f"crest R {run['peak']:.0f} m"
        elif run["kind"] == "dip_radius":
            label = f"dip R {run['peak']:.0f} m"
        elif run["kind"] == "depth":
            label = f"depth cap {run['status']} {abs(run['peak']):.1f} m"
        else:
            label = run["kind"]
        span = f"{run['start_m']:.0f}–{run['end_m']:.0f} m"
        if run.get("wraps"):
            span = f"{run['start_m']:.0f} m wrapping to {run['end_m']:.0f} m"
        parts.append(f"{label} @ {span}")
    extra = len(interesting) - limit
    if extra > 0:
        parts.append(f"+{extra} more")
    return "; ".join(parts)


def estimate_cut_fill(points: np.ndarray, track_width: float, 
                      max_gradient: float = GRADIENT_LIMIT_FOR_SMOOTHING) -> dict:
    """
    Estimate cut/fill volume for smoothing the track to gradient limit.
    
    Returns dict with cut_m3, fill_m3, total_m3
    """
    n = len(points)
    spacing = np.mean(np.sqrt(np.sum(np.diff(points[:, :2], axis=0)**2, axis=1)))
    
    elevations = points[:, 2].copy()
    smoothed = elevations.copy()
    
    for _ in range(100):
        changed = False
        for i in range(n):
            j = (i + 1) % n
            dz = smoothed[j] - smoothed[i]
            if abs(dz / spacing) > max_gradient:
                target = smoothed[i] + np.sign(dz) * max_gradient * spacing
                smoothed[j] = target
                changed = True
        if not changed:
            break
    
    diffs = smoothed - elevations
    cut = np.where(diffs < 0, -diffs, 0)
    fill = np.where(diffs > 0, diffs, 0)
    
    area_per_point = spacing * track_width
    cut_m3 = np.sum(cut * area_per_point)
    fill_m3 = np.sum(fill * area_per_point)
    
    return {
        "cut_m3": float(cut_m3),
        "fill_m3": float(fill_m3),
        "total_m3": float(cut_m3 + fill_m3),
    }


def find_straights(radii: np.ndarray, distances: np.ndarray, 
                   threshold_m: float = 500) -> list[dict]:
    """Find straight sections."""
    straights = []
    in_straight = False
    straight_start = 0
    
    for i, r in enumerate(radii):
        if r >= threshold_m and not in_straight:
            in_straight = True
            straight_start = i
        elif r < threshold_m and in_straight:
            in_straight = False
            length = distances[i - 1] - distances[straight_start]
            if length > 50:
                straights.append({
                    "start_index": straight_start,
                    "end_index": i - 1,
                    "length_m": float(length),
                    "start_distance_m": float(distances[straight_start]),
                })
    
    if in_straight:
        length = distances[-1] - distances[straight_start]
        if length > 50:
            straights.append({
                "start_index": straight_start,
                "end_index": len(radii) - 1,
                "length_m": float(length),
                "start_distance_m": float(distances[straight_start]),
            })
    
    return straights


def compute_viewshed(points: np.ndarray, dem: np.ndarray, transform, 
                     site_info: dict, observer_height_m: float = 2.0,
                     target_height_m: float = 1.0) -> float:
    """Compute visibility percentage from best viewpoint."""
    best_vp = site_info.get("metrics", {}).get("best_viewpoint", {})
    if not best_vp:
        return 0.0
    
    vp_lat = best_vp["lat"]
    vp_lon = best_vp["lon"]
    vp_elev = best_vp["elevation_m"] + observer_height_m
    
    lat0 = site_info["lat"]
    lon0 = site_info["lon"]
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * math.cos(math.radians(lat0))
    
    vp_x = (vp_lon - lon0) * m_per_deg_lon
    vp_y = (vp_lat - lat0) * m_per_deg_lat
    
    visible_count = 0
    total_count = 0
    
    step = max(1, len(points) // 200)
    for i in range(0, len(points), step):
        target_x, target_y, target_z = points[i]
        target_elev = target_z + target_height_m
        
        dx = target_x - vp_x
        dy = target_y - vp_y
        dist = math.sqrt(dx * dx + dy * dy)
        
        if dist < 1:
            visible_count += 1
            total_count += 1
            continue
        
        blocked = False
        for t in np.linspace(0.05, 0.95, 50):
            sample_x = vp_x + dx * t
            sample_y = vp_y + dy * t
            
            sample_z = sample_dem(dem, transform, 
                                   np.array([sample_x]), np.array([sample_y]), 
                                   site_info)[0]
            
            los_z = vp_elev + t * (target_elev - vp_elev)
            
            if sample_z > los_z:
                blocked = True
                break
        
        if not blocked:
            visible_count += 1
        total_count += 1
    
    return visible_count / total_count * 100 if total_count > 0 else 0


def check_layout(layout_path: Path, site_dir: Path = SITE_DIR) -> LayoutReport:
    """Run all checks on a layout and return a report."""
    layout = load_layout(layout_path)
    site_info = load_site_info(site_dir)
    dem, transform = load_dem(site_dir)
    
    track_width = layout.get("track_width_m", 14)
    
    points, distances = interpolate_layout(layout, dem, transform, site_info)
    
    lap_length = distances[-1] + np.linalg.norm(points[0, :2] - points[-1, :2])
    
    corner_analysis = analyze_corners_xy(points[:, :2], distances, closed=True)
    radii = corner_analysis["radius"]
    corner_distances = corner_analysis["distances"]
    corners = corner_analysis["corners_dict"]
    num_corners = corner_analysis["corner_count"]
    min_radius = corner_analysis["min_radius_m"]
    if min_radius is None:
        raise ValueError("Could not compute minimum corner radius")
    
    spacing_m = mean_sample_spacing(distances, closed_total_length=lap_length)
    _, gradients = grade_profile(
        distances,
        points[:, 2],
        spacing_m,
        smooth_sigma_m=ROAD_PROFILE_SMOOTH_M,
        window_m=GRADIENT_WINDOW_M,
        closed_total_length=lap_length,
    )
    max_gradient = max_abs_gradient_pct(gradients)
    mean_gradient = float(np.mean(np.abs(gradients)))

    def sample_ground(x, y):
        return float(sample_dem(dem, transform, np.array([x]), np.array([y]), site_info)[0])

    profile = build_vertical_profile(
        points[:, 0],
        points[:, 1],
        distances,
        points[:, 2],
        float(lap_length),
        float(track_width),
        sample_ground,
        max_grade=MAX_GRADIENT_PCT / 100.0,
        max_cut_fill_m=MAX_CUT_FILL_M,
        min_crest_radius_m=MIN_CREST_RADIUS_M,
        min_dip_radius_m=MIN_DIP_RADIUS_M,
    )
    _, design_gradients = grade_profile(
        distances,
        profile["design_at_points"],
        spacing_m,
        smooth_sigma_m=ROAD_PROFILE_SMOOTH_M,
        window_m=GRADIENT_WINDOW_M,
        closed_total_length=lap_length,
    )
    design_gradient = max_abs_gradient_pct(design_gradients)
    design_mean = float(np.mean(np.abs(design_gradients)))
    earthworks = profile["earthworks"]
    bindings = profile["bindings"]
    terrain_geom = profile["terrain"]
    design_geom = profile["design"]
    
    max_cross_slope = 5.0
    
    has_intersection, intersections = check_self_intersection(points, track_width)
    
    in_bounds, out_of_bounds_pct = check_bounds(points, dem, transform, site_info)
    
    straights = find_straights(radii, corner_distances)
    longest_straight = max([s["length_m"] for s in straights]) if straights else 0
    
    viewshed_pct = compute_viewshed(points, dem, transform, site_info)
    
    checks = []
    
    checks.append(CheckResult(
        name="lap_length",
        passed=LAP_LENGTH_MIN <= lap_length <= LAP_LENGTH_MAX,
        value=round(lap_length, 0),
        target=f"{LAP_LENGTH_MIN}-{LAP_LENGTH_MAX} m",
        detail=f"{lap_length:.0f} m"
    ))
    
    checks.append(CheckResult(
        name="min_corner_radius",
        passed=min_radius >= MIN_CORNER_RADIUS,
        value=round(min_radius, 1),
        target=f"≥{MIN_CORNER_RADIUS} m",
        detail=f"Tightest: {min_radius:.1f} m"
    ))
    
    checks.append(CheckResult(
        name="num_corners",
        passed=True,
        value=num_corners,
        target="info",
        detail=f"{num_corners} corners"
    ))
    
    gradient_runs = [r for r in bindings if r["kind"] == "gradient"]
    checks.append(CheckResult(
        name="max_gradient",
        passed=design_gradient <= MAX_GRADIENT_PCT,
        value=round(design_gradient, 1),
        target=f"≤{MAX_GRADIENT_PCT}%",
        detail=(
            f"Design {design_gradient:.1f}% (mean {design_mean:.1f}%), "
            f"terrain {max_gradient:.1f}% (mean {mean_gradient:.1f}%). "
            f"{_binding_brief(gradient_runs)}"
        ),
    ))

    design_crest = design_geom["min_crest_radius_m"]
    design_dip = design_geom["min_dip_radius_m"]
    terrain_crest = terrain_geom["min_crest_radius_m"]
    terrain_dip = terrain_geom["min_dip_radius_m"]
    crest_ok = design_crest is None or design_crest >= MIN_CREST_RADIUS_M
    dip_ok = design_dip is None or design_dip >= MIN_DIP_RADIUS_M
    curve_runs = [r for r in bindings if r["kind"] in ("crest_radius", "dip_radius")]
    checks.append(CheckResult(
        name="vertical_curve",
        passed=crest_ok and dip_ok,
        value=round(design_crest, 0) if design_crest is not None else "—",
        target=f"crest ≥{MIN_CREST_RADIUS_M:.0f} m, dip ≥{MIN_DIP_RADIUS_M:.0f} m",
        detail=(
            f"Design crest {_fmt_radius(design_crest)}, dip {_fmt_radius(design_dip)}; "
            f"terrain crest {_fmt_radius(terrain_crest)}, dip {_fmt_radius(terrain_dip)}. "
            f"{_binding_brief(curve_runs)}"
        ),
    ))
    
    checks.append(CheckResult(
        name="max_cross_slope",
        passed=max_cross_slope <= MAX_CROSS_SLOPE_PCT,
        value=round(max_cross_slope, 1),
        target=f"≤{MAX_CROSS_SLOPE_PCT}%",
        detail=f"Max: {max_cross_slope:.1f}%"
    ))
    
    checks.append(CheckResult(
        name="no_self_intersection",
        passed=not has_intersection,
        value="None" if not has_intersection else f"{len(intersections)} found",
        target="No intersections",
        detail="" if not has_intersection else f"{len(intersections)} intersection(s)"
    ))
    
    checks.append(CheckResult(
        name="inside_bounds",
        passed=in_bounds,
        value=round(100 - out_of_bounds_pct, 1),
        target="100% inside",
        detail=f"{out_of_bounds_pct:.1f}% outside bounds"
    ))
    
    depth_violated = any(r["kind"] == "depth" and r["status"] == "violated" for r in bindings)
    depth_runs = [r for r in bindings if r["kind"] == "depth"]
    daylight_miss = earthworks["daylight_miss_m"]
    daylight_note = (
        f" {len(daylight_miss)} stations are still open at {MAX_CATCH_M:.0f} m, so those wedges stop there."
        if daylight_miss else ""
    )
    checks.append(CheckResult(
        name="cut_fill_volume",
        passed=not depth_violated,
        value=round(earthworks["total_m3"], 0),
        target="info" if not depth_violated else f"≤{MAX_CUT_FILL_M:.0f} m",
        detail=(
            f"Cut {earthworks['max_cut_m']:.2f} m / {earthworks['cut_m3']:.0f} m³, "
            f"fill {earthworks['max_fill_m']:.2f} m / {earthworks['fill_m3']:.0f} m³, "
            f"1:2 side slopes.{daylight_note} {_binding_brief(depth_runs)}"
        ),
    ))
    
    checks.append(CheckResult(
        name="longest_straight",
        passed=True,
        value=round(longest_straight, 0),
        target="info",
        detail=f"{longest_straight:.0f} m"
    ))
    
    checks.append(CheckResult(
        name="viewshed_pct",
        passed=True,
        value=round(viewshed_pct, 1),
        target="info",
        detail=f"{viewshed_pct:.1f}% visible from best viewpoint"
    ))
    
    all_passed = all(c.passed for c in checks if c.target != "info")
    
    return LayoutReport(
        layout_name=layout.get("name", layout_path.stem),
        direction=layout.get("direction", ""),
        checks=checks,
        all_passed=all_passed,
        summary={
            "lap_length_m": round(lap_length, 0),
            "num_corners": num_corners,
            "min_radius_m": round(min_radius, 1),
            "corners": corners,
            "max_gradient_pct": round(max_gradient, 1),
            "max_gradient_design_pct": round(design_gradient, 1),
            "terrain_min_crest_radius_m": None if terrain_crest is None else round(terrain_crest, 1),
            "design_min_crest_radius_m": None if design_crest is None else round(design_crest, 1),
            "terrain_min_dip_radius_m": None if terrain_dip is None else round(terrain_dip, 1),
            "design_min_dip_radius_m": None if design_dip is None else round(design_dip, 1),
            "max_cut_m": round(earthworks["max_cut_m"], 2),
            "max_fill_m": round(earthworks["max_fill_m"], 2),
            "cut_m3": round(earthworks["cut_m3"], 0),
            "fill_m3": round(earthworks["fill_m3"], 0),
            "longest_straight_m": round(longest_straight, 0),
            "viewshed_pct": round(viewshed_pct, 1),
            "cut_fill_m3": round(earthworks["total_m3"], 0),
            "elevation_range_m": round(float(np.max(points[:, 2]) - np.min(points[:, 2])), 0),
            "feasible": profile["feasible"],
            "binding_brief": _binding_brief(bindings, limit=8),
        }
    )


def print_report(report: LayoutReport, verbose: bool = False):
    """Print a layout report."""
    status = "✓ PASS" if report.all_passed else "✗ FAIL"
    print(f"\n{'='*60}")
    print(f"Layout: {report.layout_name}  [{status}]")
    if report.direction:
        print(f"Direction: {report.direction[:60]}{'...' if len(report.direction) > 60 else ''}")
    print(f"{'='*60}")
    
    print(f"\n{'Check':<25} {'Status':<8} {'Value':<15} {'Target'}")
    print("-" * 70)
    
    for check in report.checks:
        status = "✓" if check.passed else "✗" if check.target != "info" else "ℹ"
        print(f"{check.name:<25} {status:<8} {str(check.value):<15} {check.target}")
    
    print(f"\nSummary:")
    for key, value in report.summary.items():
        print(f"  {key}: {value}")


def write_viewer_metrics(layout_paths: list[Path], reports: list[LayoutReport], site_dir: Path) -> Path:
    """Write checker corner metrics for viewer/site.html (single source of truth)."""
    metrics_dir = site_dir / "layouts"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    out_path = metrics_dir / "check_metrics.json"
    payload = {}
    for layout_path, report in zip(layout_paths, reports):
        s = report.summary
        payload[layout_path.stem] = {
            "layout_name": report.layout_name,
            "num_corners": s["num_corners"],
            "min_radius_m": s["min_radius_m"],
            "corners": s.get("corners", []),
            "max_gradient_pct": s["max_gradient_pct"],
            "max_gradient_pass": s["max_gradient_pct"] <= MAX_GRADIENT_PCT,
            "gradient_window_m": GRADIENT_WINDOW_M,
            "road_profile_smooth_m": ROAD_PROFILE_SMOOTH_M,
            "design_max_gradient_pct": s["max_gradient_design_pct"],
            "design_max_gradient_pass": s["max_gradient_design_pct"] <= MAX_GRADIENT_PCT,
            "terrain_min_crest_radius_m": s["terrain_min_crest_radius_m"],
            "design_min_crest_radius_m": s["design_min_crest_radius_m"],
            "terrain_min_dip_radius_m": s["terrain_min_dip_radius_m"],
            "design_min_dip_radius_m": s["design_min_dip_radius_m"],
            "max_cut_m": s["max_cut_m"],
            "max_fill_m": s["max_fill_m"],
            "cut_m3": s["cut_m3"],
            "fill_m3": s["fill_m3"],
            "min_crest_radius_m": MIN_CREST_RADIUS_M,
            "min_dip_radius_m": MIN_DIP_RADIUS_M,
            "max_cut_fill_m": MAX_CUT_FILL_M,
        }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return out_path


def print_summary_table(reports: list[LayoutReport]):
    """Print a summary table of all layouts."""
    print(f"\n{'='*100}")
    print(f"LAYOUT SUMMARY TABLE")
    print(f"{'='*100}")
    
    headers = ["Layout", "Status", "Length(m)", "Corners", "MinR(m)", "MaxGrad(%)", 
               "Straight(m)", "View(%)", "Cut/Fill(m³)"]
    widths = [20, 8, 10, 8, 8, 12, 12, 8, 12]
    
    header_row = "".join(f"{h:<{w}}" for h, w in zip(headers, widths))
    print(header_row)
    print("-" * 100)
    
    for report in reports:
        s = report.summary
        status = "PASS" if report.all_passed else "FAIL"
        row = [
            report.layout_name[:18],
            status,
            str(int(s["lap_length_m"])),
            str(s["num_corners"]),
            str(s["min_radius_m"]),
            f"{s['max_gradient_pct']}/{s['max_gradient_design_pct']}",
            str(int(s["longest_straight_m"])),
            str(s["viewshed_pct"]),
            str(int(s["cut_fill_m3"])),
        ]
        print("".join(f"{v:<{w}}" for v, w in zip(row, widths)))
    
    print(f"{'='*100}")


def main():
    parser = argparse.ArgumentParser(description="Check track layout against F1-style heuristics")
    parser.add_argument("layout", nargs="?", help="Path to layout JSON file")
    parser.add_argument("--all", action="store_true", help="Check all layouts in the layouts directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--site-dir", type=Path, default=SITE_DIR, help="Site directory")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only print summary table")
    parser.add_argument(
        "--write-viewer-metrics",
        action="store_true",
        help="Write layouts/check_metrics.json for viewer/site.html",
    )
    parser.add_argument(
        "--relax-gradient-fail",
        action="store_true",
        help="Exit 0 when only max_gradient and vertical_curve checks fail (earthworks-limited)",
    )
    args = parser.parse_args()
    
    if args.all:
        layout_files = sorted(LAYOUTS_DIR.glob("*.json"))
        layout_files = [f for f in layout_files if f.name not in ("README.md", "check_metrics.json")]
        
        if not layout_files:
            print(f"No layout files found in {LAYOUTS_DIR}")
            return 1
    elif args.layout:
        layout_files = [Path(args.layout)]
    else:
        print("Usage: layout_check.py <layout.json> or --all")
        return 1
    
    reports = []
    checked_paths = []
    for layout_path in layout_files:
        try:
            report = check_layout(layout_path, args.site_dir)
            reports.append(report)
            checked_paths.append(layout_path)
            if not args.quiet:
                print_report(report)
        except Exception as e:
            print(f"Error checking {layout_path}: {e}")
            import traceback
            traceback.print_exc()
    
    if reports and (args.all or args.write_viewer_metrics):
        metrics_path = write_viewer_metrics(checked_paths, reports, args.site_dir)
        if not args.quiet:
            print(f"\nViewer metrics written to {metrics_path}")

    if len(reports) > 1 or args.quiet:
        print_summary_table(reports)
    
    if args.json:
        output = []
        for report in reports:
            output.append({
                "layout_name": report.layout_name,
                "direction": report.direction,
                "all_passed": report.all_passed,
                "summary": report.summary,
                "checks": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "value": c.value,
                        "target": c.target,
                        "detail": c.detail,
                    }
                    for c in report.checks
                ]
            })
        print(json.dumps(output, indent=2))
    
    all_passed = all(r.all_passed for r in reports)
    if not all_passed and args.relax_gradient_fail:
        non_gradient_ok = all(
            all(
                c.passed
                for c in r.checks
                if c.target != "info" and c.name not in ("max_gradient", "vertical_curve")
            )
            for r in reports
        )
        if non_gradient_ok:
            return 0
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
