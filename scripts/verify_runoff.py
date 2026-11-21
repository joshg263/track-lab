#!/usr/bin/env python3
"""Run-off zones: synthetic terrain, real layouts, and the JS port.

Flat ground passes every corner. A cliff under one zone fails that corner
for the drop. A later part of the lap inside a zone fails it for overlap.
Python and viewer/runoff.js agree. Layouts A, version-1 and the version-2
fixture are printed for the notes. They are not modified.

Offline. No network. Set SKIP_JS_PARITY=1 to skip when node is missing.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from layout_check import (  # noqa: E402
    MAX_GRADIENT_PCT,
    SITE_DIR,
    interpolate_layout,
    load_dem,
    load_layout,
    load_site_info,
    sample_dem,
)
from runoff import (  # noqa: E402
    ASPECT_LIMIT,
    CLIP_BUFFER_M,
    DEFAULT_DECEL_G,
    DEFAULT_OVERLAP_GAP_M,
    DEPTH_MARGIN_M,
    GRAVITY_MPS2,
    _approach_tangent,
    _dist_point_polygon,
    _dist_point_segment,
    _edge_frame,
    _forward,
    _gap_to_window,
    _interp_closed,
    _max_slopes,
    _nearest_edge_s,
    _point_in_polygon,
    _turn_sign,
    SLIVER_ALT_M,
    SLIVER_EDGE_M,
    SLIVER_OFF_M,
    SLIVER_RATIO,
    assess_runoff,
    carved_ground,
)
from corner_detection import analyze_corners_xy  # noqa: E402
from vertical_profile import build_vertical_profile  # noqa: E402

LAYOUTS = [
    ("layout_a_straight_hairpin", "A", SITE_DIR / "layouts" / "layout_a_straight_hairpin.json"),
    ("version-1", "version-1", SITE_DIR / "layouts" / "edits" / "version-1.json"),
    ("version-2", "version-2", SCRIPT_DIR / "fixtures" / "version-2.json"),
]

POLY_TOL_M = 1e-4
SPEED_TOL = 1e-4
SLOPE_TOL = 1e-5
DROP_TOL_M = 1e-4
VOLUME_TOL_M3 = 0.5


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def run_js(mode: str, payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("SKIP_JS_PARITY"):
            print("! node not found, run-off parity skipped")
            return {}
        fail("node not found: install Node.js 18+ or set SKIP_JS_PARITY=1")
    result = subprocess.run(
        [node, str(SCRIPT_DIR / "js_runoff.mjs"), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        fail(f"js {mode} failed\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


def chainage(xy: np.ndarray) -> tuple[np.ndarray, float]:
    distances = [0.0]
    for i in range(1, len(xy)):
        distances.append(distances[-1] + float(np.linalg.norm(xy[i] - xy[i - 1])))
    lap = distances[-1] + float(np.linalg.norm(xy[0] - xy[-1]))
    return np.asarray(distances, dtype=np.float64), lap


def add_line(pts: list[tuple[float, float]], x0, y0, x1, y1, step: float = 2.0) -> None:
    dist = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(dist / step))
    for i in range(n):
        t = i / n
        pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))


def add_arc(pts, cx, cy, radius, a0, a1, step: float = 2.0) -> None:
    sweep = a1 - a0
    length = abs(sweep) * radius
    n = max(2, int(length / step))
    for i in range(1, n):
        a = a0 + sweep * i / n
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))


def dedupe(pts: list[tuple[float, float]]) -> np.ndarray:
    kept = [pts[0]]
    for point in pts[1:]:
        if math.hypot(point[0] - kept[-1][0], point[1] - kept[-1][1]) > 0.4:
            kept.append(point)
    return np.asarray(kept, dtype=np.float64)


def stadium() -> tuple[np.ndarray, np.ndarray, float]:
    """Two straights and two 40 m semicircles. Outside zones point away from the lap."""
    straight = 250.0
    radius = 40.0
    step = 2.0
    pts: list[tuple[float, float]] = []
    x = 0.0
    while x < straight - 1e-6:
        pts.append((x, 0.0))
        x += step
    n_arc = max(8, int(math.pi * radius / step))
    for i in range(1, n_arc):
        a = -math.pi / 2 + math.pi * i / n_arc
        pts.append((straight + radius * math.cos(a), radius + radius * math.sin(a)))
    x = straight
    while x > 1e-6:
        pts.append((x, 2 * radius))
        x -= step
    for i in range(1, n_arc):
        a = math.pi / 2 + math.pi * i / n_arc
        pts.append((radius * math.cos(a), radius + radius * math.sin(a)))
    xy = dedupe(pts)
    distances, lap = chainage(xy)
    return xy, distances, lap


def overlap_track() -> tuple[np.ndarray, np.ndarray, float]:
    """A left-hand turn whose escape corridor is crossed by the return straight."""
    pts: list[tuple[float, float]] = []
    add_line(pts, -180, 0, 0, 0)
    add_arc(pts, 0, 30, 30, -math.pi / 2, 0)
    add_line(pts, 30, 30, 30, 420)
    add_line(pts, 30, 420, 60, 420)
    # Drops across the straight-on escape of the opening left-hander.
    add_line(pts, 60, 420, 60, -40)
    add_line(pts, 60, -40, -180, -40)
    add_line(pts, -180, -40, -180, 0)
    xy = dedupe(pts)
    distances, lap = chainage(xy)
    return xy, distances, lap


def payload_from(xy, distances, design_z, lap, ground, **options) -> dict:
    body = {
        "x": xy[:, 0].tolist(),
        "y": xy[:, 1].tolist(),
        "distances": distances.tolist(),
        "designZ": np.asarray(design_z, dtype=np.float64).tolist(),
        "lapM": lap,
        "trackWidthM": options.get("track_width_m", 14),
        "ground": ground,
        "decelG": options.get("decel_g", 0.5),
        "widthM": options.get("width_m", 50),
        "slopeLimit": options.get("slope_limit", 0.10),
        "stepM": options.get("step_m", 3),
        "speedTrace": options.get("speed_trace"),
    }
    return body


def compare(py: dict, js: dict, label: str) -> None:
    if not js:
        return
    if py["corner_count"] != js["corner_count"] or py["pass_count"] != js["pass_count"]:
        fail(
            f"{label}: count Python {py['pass_count']}/{py['corner_count']} "
            f"JS {js['pass_count']}/{js['corner_count']}"
        )
    if py["summary"] != js["summary"]:
        fail(f"{label}: summary {py['summary']!r} vs {js['summary']!r}")
    for a, b in zip(py["corners"], js["corners"]):
        where = f"{label} corner {a['index']}"
        if a["reasons"] != b["reasons"] or a["passed"] != b["passed"]:
            fail(f"{where}: Python {a['passed']} {a['reasons']} vs JS {b['passed']} {b['reasons']}")
        if a["speed_source"] != b["speed_source"]:
            fail(f"{where}: speed source {a['speed_source']} vs {b['speed_source']}")
        pairs = [
            ("turn_in_m", POLY_TOL_M),
            ("position_m", POLY_TOL_M),
            ("radius_m", POLY_TOL_M),
            ("speed_mps", SPEED_TOL),
            ("depth_m", SPEED_TOL),
            ("available_depth_m", 1e-3),
            ("max_cross_slope", SLOPE_TOL),
            ("max_long_slope", SLOPE_TOL),
            ("max_drop_m", DROP_TOL_M),
            ("max_wall_m", DROP_TOL_M),
            ("z_edge_m", DROP_TOL_M),
            ("earthworks_m3", VOLUME_TOL_M3),
            ("cut_m3", VOLUME_TOL_M3),
            ("fill_m3", VOLUME_TOL_M3),
        ]
        for key, tol in pairs:
            err = abs(float(a[key]) - float(b[key]))
            if err > tol:
                fail(f"{where}: {key} Δ {err:.4g} (Python {a[key]:.6g}, JS {b[key]:.6g})")
        if len(a["polygon"]) != len(b["polygon"]):
            fail(f"{where}: polygon size")
        for pa, pb in zip(a["polygon"], b["polygon"]):
            if abs(pa[0] - pb[0]) > POLY_TOL_M or abs(pa[1] - pb[1]) > POLY_TOL_M:
                fail(f"{where}: polygon {pa} vs {pb}")
    print(f"  parity {label}: {py['summary']}")


def check_flat_and_cliff(skip_js: bool) -> None:
    xy, distances, lap = stadium()
    design = np.full(len(xy), 100.0)

    def flat(_x, _y):
        return 100.0

    flat_py = assess_runoff(xy[:, 0], xy[:, 1], distances, design, lap, flat, track_width_m=14)
    if flat_py["corner_count"] < 2:
        fail(f"flat stadium found {flat_py['corner_count']} corners")
    if flat_py["pass_count"] != flat_py["corner_count"]:
        reasons = [c["reasons"] for c in flat_py["corners"]]
        fail(f"flat stadium should pass every corner, got {flat_py['summary']} {reasons}")
    for corner in flat_py["corners"]:
        if corner["speed_source"] != "radius_proxy":
            fail("flat stadium should use the radius proxy")
        expected = (corner["speed_mps"] ** 2) / (2 * 0.5 * GRAVITY_MPS2)
        if abs(corner["depth_m"] - expected) > 1e-6:
            fail(f"depth {corner['depth_m']} != v^2/(2a) {expected}")
    print(f"flat stadium: {flat_py['summary']}")

    def cliff(x, _y):
        return 88.0 if x > 200.0 else 100.0

    cliff_py = assess_runoff(xy[:, 0], xy[:, 1], distances, design, lap, cliff, track_width_m=14)
    failed = [c for c in cliff_py["corners"] if not c["passed"]]
    if len(failed) != 1:
        fail(f"cliff should fail one corner, got {cliff_py['summary']} {[c['reasons'] for c in cliff_py['corners']]}")
    if failed[0]["index"] != 0 or failed[0]["reasons"] != ["drop"]:
        fail(f"cliff should fail corner 0 for drop, got #{failed[0]['index']} {failed[0]['reasons']}")
    print(f"cliff: corner {failed[0]['index']} fails for drop, {cliff_py['summary']}")

    if skip_js:
        return
    flat_js = run_js("assess", payload_from(
        xy, distances, design, lap, {"kind": "constant", "z": 100},
    ))
    compare(flat_py, flat_js, "flat")
    cliff_js = run_js("assess", payload_from(
        xy, distances, design, lap, {"kind": "step", "z": 100, "low": 88, "axis": "x", "cut": 200},
    ))
    compare(cliff_py, cliff_js, "cliff")


def _zone_ground(distances, design, lap, zones, turn_in_only: bool):
    """Terrain that sits on the designed edge, or on the turn-in height only."""

    def height(x, y):
        for zone in zones:
            poly = zone["polygon"]
            if not _point_in_polygon(x, y, poly):
                continue
            if turn_in_only:
                return float(_interp_closed(distances, design, lap, zone["turn_in_m"]))
            edge_s = _nearest_edge_s(zone["inner"], x, y, lap)
            return float(_interp_closed(distances, design, lap, edge_s))
        return 100.0

    return height


def _zones_spec(distances, design, lap, zones, mode: str) -> dict:
    return {
        "kind": "zones",
        "distances": distances.tolist(),
        "designZ": np.asarray(design, dtype=np.float64).tolist(),
        "lapM": lap,
        "mode": mode,
        "zones": [
            {
                "polygon": zone["polygon"],
                "inner": zone["inner"],
                "turnInM": zone["turn_in_m"],
                "widthM": zone["width_m"],
            }
            for zone in zones
        ],
    }


def check_designed_edge(skip_js: bool) -> None:
    """Drop and wall use the designed road-edge height, which is z(s)."""
    xy, distances, lap = stadium()

    def terrain(_x, _y):
        return 100.0

    raised_design = np.full(len(xy), 108.0)
    raised = assess_runoff(
        xy[:, 0], xy[:, 1], distances, raised_design, lap, terrain, track_width_m=14,
    )
    if raised["pass_count"] != 0:
        fail(f"an edge 8 m above the ground should fail, got {raised['summary']}")
    for corner in raised["corners"]:
        if corner["reasons"] != ["drop"]:
            fail(f"raised edge corner {corner['index']} reasons {corner['reasons']}")
        if abs(corner["z_edge_m"] - 108.0) > 1e-6:
            fail(f"z_edge {corner['z_edge_m']} is not the designed height 108 m")
        if abs(corner["max_drop_m"] - 8.0) > 0.05:
            fail(f"drop {corner['max_drop_m']} m should be the 8 m designed step")
    print(f"designed edge: {raised['summary']}, each an 8 m drop below z(s)")

    design = 100.0 + 0.08 * distances
    probe = assess_runoff(
        xy[:, 0], xy[:, 1], distances, design, lap, terrain,
        track_width_m=14, slope_limit=100.0,
    )
    zones = probe["corners"]
    matched = assess_runoff(
        xy[:, 0], xy[:, 1], distances, design, lap,
        _zone_ground(distances, design, lap, zones, False),
        track_width_m=14, slope_limit=100.0,
    )
    if matched["pass_count"] != matched["corner_count"]:
        detail = [(c["index"], c["reasons"], c["max_drop_m"], c["max_wall_m"]) for c in matched["corners"]]
        fail(f"terrain on the designed edge should pass, got {detail}")
    held = assess_runoff(
        xy[:, 0], xy[:, 1], distances, design, lap,
        _zone_ground(distances, design, lap, zones, True),
        track_width_m=14, slope_limit=100.0,
    )
    for corner in held["corners"]:
        if "drop" not in corner["reasons"] and "wall" not in corner["reasons"]:
            fail(
                f"terrain held at the turn-in height should miss the rising edge, "
                f"corner {corner['index']} {corner['reasons']}"
            )
    print("designed edge follows z(s) along the outside edge")

    if skip_js:
        return
    compare(raised, run_js("assess", payload_from(
        xy, distances, raised_design, lap, {"kind": "constant", "z": 100},
    )), "raised-edge")
    compare(matched, run_js("assess", payload_from(
        xy, distances, design, lap,
        _zones_spec(distances, design, lap, zones, "along"),
        slope_limit=100.0,
    )), "edge-along")
    compare(held, run_js("assess", payload_from(
        xy, distances, design, lap,
        _zones_spec(distances, design, lap, zones, "turn-in"),
        slope_limit=100.0,
    )), "edge-turn-in")


def check_default_decel(skip_js: bool) -> None:
    """Omitting deceleration is the gravel default, not the 0.05 g floor."""
    xy, distances, lap = stadium()
    design = np.full(len(xy), 100.0)

    def flat(_x, _y):
        return 100.0

    report = assess_runoff(xy[:, 0], xy[:, 1], distances, design, lap, flat)
    if abs(report["decel_g"] - DEFAULT_DECEL_G) > 1e-9:
        fail(f"default decel {report['decel_g']} is not gravel {DEFAULT_DECEL_G} g")
    for corner in report["corners"]:
        expected = (corner["speed_mps"] ** 2) / (2 * DEFAULT_DECEL_G * GRAVITY_MPS2)
        if abs(corner["depth_m"] - expected) > 1e-6:
            fail(f"default depth {corner['depth_m']} != v^2/(2*0.5g) {expected}")
    print(f"default decel is gravel {DEFAULT_DECEL_G} g")
    if skip_js:
        return
    body = payload_from(xy, distances, design, lap, {"kind": "constant", "z": 100})
    del body["decelG"]
    compare(report, run_js("assess", body), "default-decel")


def check_diagonal_slope(skip_js: bool) -> None:
    """A grade along the escape is the full longitudinal slope, not a split partial."""
    ex, ey = 1.0 / math.sqrt(1.25), -0.5 / math.sqrt(1.25)
    px, py = -ey, ex
    spacing = 5.0
    samples = []
    for ix in range(6):
        for iy in range(6):
            x = ix * spacing
            y = iy * spacing
            z = 0.12 * (ex * x + ey * y)
            samples.append((ix, iy, x, y, z))
    long_slope, cross_slope = _max_slopes(samples, ex, ey, px, py)
    if abs(long_slope - 0.12) > 1e-6 or abs(cross_slope) > 1e-6:
        fail(f"diagonal plane should be 12% along escape and 0% across, got {long_slope:.4%} / {cross_slope:.4%}")
    print("diagonal grade: 12% longitudinal, 0% cross")

    xy, distances, lap = stadium()
    analysis = analyze_corners_xy(np.column_stack([xy[:, 0], xy[:, 1]]), distances, closed=True)
    run = analysis["runs"][0]
    resampled = analysis["xy"]
    resampled_d = analysis["distances"]
    resample_lap = float(resampled_d[-1])
    sign = _turn_sign(resampled, resampled_d, resample_lap, run["start_index"], run["end_index"])
    tx, ty = _approach_tangent(resampled, resampled_d, resample_lap, float(run["start_m"]))
    cex, cey = tx, ty
    design = np.full(len(xy), 100.0)

    def plane(x, y):
        return 100.0 + 0.12 * (cex * x + cey * y)

    report = assess_runoff(
        xy[:, 0], xy[:, 1], distances, design, lap, plane,
        track_width_m=14, step_m=1000,
    )
    corner = report["corners"][0]
    if abs(corner["max_long_slope"] - 0.12) > 1e-3 or abs(corner["max_cross_slope"]) > 1e-3:
        fail(
            f"escape-aligned plane corner 0 slopes "
            f"{corner['max_long_slope']:.4f} / {corner['max_cross_slope']:.4f}"
        )
    if corner["reasons"] != ["long-slope"]:
        fail(f"12% along the escape should fail only for long-slope, got {corner['reasons']}")
    print("escape-aligned 12% grade fails for long-slope")
    if skip_js:
        return
    compare(report, run_js("assess", payload_from(
        xy, distances, design, lap,
        {"kind": "plane", "z0": 100, "gx": 0.12 * cex, "gy": 0.12 * cey},
        step_m=1000,
    )), "diagonal-slope")


def check_overlap(skip_js: bool) -> None:
    xy, distances, lap = overlap_track()
    design = np.full(len(xy), 50.0)

    def flat(_x, _y):
        return 50.0

    report = assess_runoff(xy[:, 0], xy[:, 1], distances, design, lap, flat, track_width_m=14)
    failed = [c for c in report["corners"] if not c["passed"]]
    if len(failed) != 1:
        detail = [(c["index"], c["reasons"]) for c in report["corners"]]
        fail(f"overlap track should fail one corner, got {detail}")
    if len(failed[0]["reasons"]) != 1 or not str(failed[0]["reasons"][0]).startswith("overlaps track section at s="):
        fail(f"overlap corner reasons {failed[0]['reasons']}")
    if failed[0]["index"] != 0:
        fail(f"overlap should be the entry left-hander, index {failed[0]['index']}")
    print(f"overlap: corner {failed[0]['index']} fails for overlap, {report['summary']}")
    if skip_js:
        return
    js = run_js("assess", payload_from(
        xy, distances, design, lap, {"kind": "constant", "z": 50},
    ))
    compare(report, js, "overlap")


def check_trace_changes_depth(skip_js: bool) -> None:
    xy, distances, lap = stadium()
    design = np.full(len(xy), 100.0)

    def flat(_x, _y):
        return 100.0

    proxy = assess_runoff(xy[:, 0], xy[:, 1], distances, design, lap, flat)
    trace = [{"distance": 0.0, "speed_mps": 40.0}, {"distance": lap * 0.5, "speed_mps": 40.0}]
    traced = assess_runoff(
        xy[:, 0], xy[:, 1], distances, design, lap, flat, speed_trace=trace,
    )
    if any(c["speed_source"] != "profile" for c in traced["corners"]):
        fail("a supplied trace should be the entry speed")
    if not all(c["speed_mps"] > proxy["corners"][i]["speed_mps"] + 5 for i, c in enumerate(traced["corners"])):
        fail("profile entry speed should be used instead of the radius proxy")
    print("speed trace overrides the radius proxy")
    if skip_js:
        return
    js = run_js("assess", payload_from(
        xy, distances, design, lap, {"kind": "constant", "z": 100}, speed_trace=trace,
    ))
    compare(traced, js, "trace")


def load_centreline(path: Path, dem, transform, site):
    layout = load_layout(path)
    points, distances = interpolate_layout(layout, dem, transform, site)
    lap = float(distances[-1] + np.linalg.norm(points[0, :2] - points[-1, :2]))
    width = float(layout.get("track_width_m", 14))

    def dem_at(x, y):
        return float(sample_dem(dem, transform, np.array([x]), np.array([y]), site)[0])

    profile = build_vertical_profile(
        points[:, 0], points[:, 1], distances, points[:, 2], lap, width, dem_at,
        max_grade=MAX_GRADIENT_PCT / 100.0,
    )
    sections = profile["sections"]

    def ground(x, y):
        # The escape sits on the carved display surface beside the road, and
        # on the DEM once that surface has run back into the hillside.
        return carved_ground(sections, dem_at, x, y)

    return {
        "layout": layout,
        "x": points[:, 0],
        "y": points[:, 1],
        "distances": distances,
        "design_z": np.asarray(profile["design_at_points"], dtype=np.float64),
        "lap": lap,
        "width": width,
        "ground": ground,
    }


def fmt_reasons(reasons: list[str]) -> str:
    return ", ".join(reasons) if reasons else "—"


def print_layout_tables(reports: dict) -> None:
    print()
    print("Run-off is a starting estimate (gravel decel 0.5 g, width 50 m, slope 10%, step 3 m). Not an FIA figure.")
    print()
    print("| Layout | Corners | Pass |")
    print("| --- | ---: | ---: |")
    for key, label, _path in LAYOUTS:
        report = reports[key]
        print(f"| {label} | {report['corner_count']} | {report['pass_count']} |")
    print()
    print("| Layout | Corner | Chainage m | Radius m | Entry km/h | Source | Depth m | Result | Reasons | Earthworks m³ |")
    print("| --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- | ---: |")
    for key, label, _path in LAYOUTS:
        for corner in reports[key]["corners"]:
            result = "pass" if corner["passed"] else "fail"
            works = f"{corner['earthworks_m3']:.0f}"
            print(
                f"| {label} | {corner['index'] + 1} | {corner['turn_in_m']:.0f} | {corner['radius_m']:.0f} | "
                f"{corner['speed_mps'] * 3.6:.0f} | {corner['speed_source']} | {corner['depth_m']:.0f} | "
                f"{result} | {fmt_reasons(corner['reasons'])} | {works} |"
            )
    print()


def check_fan_geometry(prepared: dict, reports: dict) -> None:
    """Inner edge stays on the track, clipping clears other sections, depth follows speed."""
    for key, label, _path in LAYOUTS:
        item = prepared[key]
        report = reports[key]
        xy = np.column_stack([item["x"], item["y"]])
        analysis = analyze_corners_xy(xy, item["distances"], closed=True)
        resampled = analysis["xy"]
        resampled_d = analysis["distances"]
        resample_lap = float(resampled_d[-1])
        half = item["width"] * 0.5
        speeds = []
        for corner in report["corners"]:
            sign = int(corner["turn_sign"])
            inner = corner["inner"]
            polygon = corner["polygon"]
            if len(inner) < 2:
                fail(f"{label} corner {corner['index']} has no inner edge")
            for point in inner:
                ex, ey, *_rest = _edge_frame(
                    resampled, resampled_d, resample_lap, point[2], sign, half,
                )
                if math.hypot(point[0] - ex, point[1] - ey) > 0.05:
                    fail(f"{label} corner {corner['index']} inner point leaves the outside edge")
            worst = 0.0
            for i in range(len(inner) - 1):
                s0 = inner[i][2]
                s1 = inner[i + 1][2]
                mid = (s0 + 0.5 * _forward(s0, s1, resample_lap)) % resample_lap
                ex, ey, *_rest = _edge_frame(
                    resampled, resampled_d, resample_lap, mid, sign, half,
                )
                dist = _dist_point_segment(
                    ex, ey, inner[i][0], inner[i][1], inner[i + 1][0], inner[i + 1][1],
                )
                if dist > worst:
                    worst = dist
            if worst > 0.5:
                fail(f"{label} corner {corner['index']} inner boundary gap {worst:.2f} m")
            for i in range(len(inner)):
                if abs(polygon[i][0] - inner[i][0]) > 1e-6 or abs(polygon[i][1] - inner[i][1]) > 1e-6:
                    fail(f"{label} corner {corner['index']} polygon does not follow its inner edge")
            s0 = inner[0][2]
            s2 = inner[-1][2]
            for i, chain in enumerate(item["distances"]):
                gap = _gap_to_window(float(chain), s0, s2, item["lap"])
                if gap <= DEFAULT_OVERLAP_GAP_M:
                    continue
                dist = _dist_point_polygon(float(item["x"][i]), float(item["y"][i]), polygon)
                if dist < half + CLIP_BUFFER_M - 0.5:
                    fail(
                        f"{label} corner {corner['index']} still overlaps track at "
                        f"s={float(chain):.0f} ({dist:.2f} m from the centreline)"
                    )
            if corner["available_depth_m"] > corner["depth_m"] + 1e-3:
                fail(f"{label} corner {corner['index']} available depth exceeds the required depth")
            tx, ty = _approach_tangent(resampled, resampled_d, resample_lap, corner["turn_in_m"])
            brake = min(
                inner,
                key=lambda point: min(
                    _forward(point[2], corner["turn_in_m"], resample_lap),
                    _forward(corner["turn_in_m"], point[2], resample_lap),
                ),
            )
            bx, by = brake[0], brake[1]
            nx, ny = -ty, tx
            max_along = -1e18
            min_along = 1e18
            max_lat = 0.0
            reach = -1e18
            for px, py in polygon:
                along = (px - bx) * tx + (py - by) * ty
                lat = abs((px - bx) * nx + (py - by) * ny)
                if along > max_along:
                    max_along = along
                if along < min_along:
                    min_along = along
                if lat > max_lat:
                    max_lat = lat
                off = 1e18
                for i in range(len(inner) - 1):
                    dist = _dist_point_segment(
                        px, py, inner[i][0], inner[i][1], inner[i + 1][0], inner[i + 1][1],
                    )
                    if dist < off:
                        off = dist
                if off > 1.0 and along > reach:
                    reach = along
            span = max_along - min_along
            short = span if span < max_lat else max_lat
            long = max_lat if span < max_lat else span
            if short > 0.5 and long / short > ASPECT_LIMIT:
                fail(
                    f"{label} corner {corner['index']} aspect {long / short:.1f} "
                    f"exceeds {ASPECT_LIMIT:.0f} (length {long:.0f} m, width {short:.0f} m)"
                )
            outer = corner["outer"]
            for i in range(len(outer) - 1):
                ax, ay = outer[i]
                bx2, by2 = outer[i + 1]
                elen = math.hypot(bx2 - ax, by2 - ay)
                if elen < SLIVER_EDGE_M:
                    continue
                alt = _dist_point_segment(inner[i][0], inner[i][1], ax, ay, bx2, by2)
                alt2 = _dist_point_segment(inner[i + 1][0], inner[i + 1][1], ax, ay, bx2, by2)
                if alt2 > alt:
                    alt = alt2
                off0 = 1e18
                off1 = 1e18
                for j in range(len(inner) - 1):
                    off0 = min(off0, _dist_point_segment(
                        ax, ay, inner[j][0], inner[j][1], inner[j + 1][0], inner[j + 1][1],
                    ))
                    off1 = min(off1, _dist_point_segment(
                        bx2, by2, inner[j][0], inner[j][1], inner[j + 1][0], inner[j + 1][1],
                    ))
                far = off0 if off0 > off1 else off1
                hairline = alt < SLIVER_ALT_M or elen > alt * SLIVER_RATIO
                if hairline and far > SLIVER_OFF_M:
                    fail(
                        f"{label} corner {corner['index']} has a {elen:.0f} m sliver "
                        f"only {alt:.2f} m thick"
                    )
            if reach > corner["depth_m"] + DEPTH_MARGIN_M + 0.05:
                fail(
                    f"{label} corner {corner['index']} extends {reach:.1f} m along the escape, "
                    f"past depth {corner['depth_m']:.1f} m plus {DEPTH_MARGIN_M:.0f} m"
                )
            speeds.append((corner["speed_mps"], corner["depth_m"]))
        speeds.sort()
        for left, right in zip(speeds, speeds[1:]):
            if right[0] > left[0] + 0.5 and right[1] <= left[1] + 1e-6:
                fail(
                    f"{label} zone depth did not increase with approach speed "
                    f"({left[0] * 3.6:.0f} km/h → {left[1]:.0f} m, "
                    f"{right[0] * 3.6:.0f} km/h → {right[1]:.0f} m)"
                )
        print(f"fan geometry {label}: inner edge, clip clearance, depth vs speed, aspect, escape extent")


def check_layouts(skip_js: bool) -> dict:
    dem, transform = load_dem(SITE_DIR)
    site = load_site_info(SITE_DIR)
    prepared = {}
    body = {}
    for key, _label, path in LAYOUTS:
        item = load_centreline(path, dem, transform, site)
        prepared[key] = item
        body[key] = payload_from(
            np.column_stack([item["x"], item["y"]]),
            item["distances"],
            item["design_z"],
            item["lap"],
            {"kind": "dem"},
            track_width_m=item["width"],
        )
        body[key]["trackWidthM"] = item["width"]
    js_layouts = {}
    if not skip_js:
        js_layouts = run_js("layouts", body)["layouts"]
    reports = {}
    for key, label, _path in LAYOUTS:
        item = prepared[key]
        trace = None
        if key in js_layouts:
            trace = js_layouts[key]["trace"]
        report = assess_runoff(
            item["x"], item["y"], item["distances"], item["design_z"], item["lap"], item["ground"],
            track_width_m=item["width"],
            speed_trace=trace,
        )
        if trace is not None:
            if any(c["speed_source"] != "profile" for c in report["corners"]):
                fail(f"{label}: expected the speed profile's entry speeds")
            compare(report, js_layouts[key]["report"], label)
        reports[key] = report
        print(f"{label}: {report['summary']}")
    check_fan_geometry(prepared, reports)
    return reports


def main() -> int:
    skip_js = shutil.which("node") is None and bool(os.environ.get("SKIP_JS_PARITY"))
    if shutil.which("node") is None and not skip_js:
        fail("node not found: install Node.js 18+ or set SKIP_JS_PARITY=1")
    check_flat_and_cliff(skip_js)
    check_designed_edge(skip_js)
    check_default_decel(skip_js)
    check_diagonal_slope(skip_js)
    check_overlap(skip_js)
    check_trace_changes_depth(skip_js)
    print("fitting layouts...")
    reports = check_layouts(skip_js)
    print_layout_tables(reports)
    print("run-off verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
