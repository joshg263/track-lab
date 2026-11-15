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
    GRAVITY_MPS2,
    assess_runoff,
)
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
    add_line(pts, 30, 420, 420, 420)
    add_line(pts, 420, 420, 420, -18)
    add_line(pts, 420, -18, 10, -18)
    add_line(pts, 10, -18, -180, -18)
    add_line(pts, -180, -18, -180, 0)
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
        return 88.0 if x > 240.0 else 100.0

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
        xy, distances, design, lap, {"kind": "step", "z": 100, "low": 88, "axis": "x", "cut": 240},
    ))
    compare(cliff_py, cliff_js, "cliff")


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
    if failed[0]["reasons"] != ["overlap"]:
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

    def ground(x, y):
        return float(sample_dem(dem, transform, np.array([x]), np.array([y]), site)[0])

    profile = build_vertical_profile(
        points[:, 0], points[:, 1], distances, points[:, 2], lap, width, ground,
        max_grade=MAX_GRADIENT_PCT / 100.0,
    )
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
    return reports


def main() -> int:
    skip_js = shutil.which("node") is None and bool(os.environ.get("SKIP_JS_PARITY"))
    if shutil.which("node") is None and not skip_js:
        fail("node not found: install Node.js 18+ or set SKIP_JS_PARITY=1")
    check_flat_and_cliff(skip_js)
    check_overlap(skip_js)
    check_trace_changes_depth(skip_js)
    print("fitting layouts...")
    reports = check_layouts(skip_js)
    print_layout_tables(reports)
    print("run-off verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
