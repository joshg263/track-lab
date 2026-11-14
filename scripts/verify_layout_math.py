#!/usr/bin/env python3
"""JS layout maths agrees with the Python checker and with Red Bull Ring analysis.

Runs viewer/layout_math.js under Node on Nishiwaki layouts A, B and C and on the
Red Bull Ring centreline. Layouts are compared with layouts/check_metrics.json
and with a live call into the same Python functions the checker uses. Red Bull
Ring is compared with analyze_track on the committed DEM fixture.

Offline. No network. Set SKIP_JS_PARITY=1 to skip when node is missing.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_track import analyze  # noqa: E402
from corner_detection import analyze_corners_xy, compute_curvature_lonlat, find_corners  # noqa: E402
from layout_check import (  # noqa: E402
    MAX_GRADIENT_PCT,
    SITE_DIR,
    interpolate_layout,
    load_dem,
    load_layout,
    load_site_info,
)
from road_profile import (  # noqa: E402
    grade_profile,
    max_abs_gradient_pct,
    mean_sample_spacing,
)

LAYOUTS = [
    "layout_a_straight_hairpin",
    "layout_b_elevation_sweep",
    "layout_c_arena",
]
# Rounding in check_metrics.json is 0.1. The live Python comparison is tighter.
METRICS_TOL_RADIUS_M = 0.15
METRICS_TOL_POS_M = 0.15
METRICS_TOL_GRADIENT = 0.15
# Unrounded JS vs the same Python functions. Float noise only.
PYTHON_TOL_RADIUS_M = 1e-6
PYTHON_TOL_POS_M = 1e-6
PYTHON_TOL_GRADIENT = 1e-6
PYTHON_TOL_LAP_M = 1e-4
RBR_TOL_RADIUS_M = 1e-4
RBR_TOL_POS_M = 1e-4
RBR_TOL_GRADIENT = 1e-4


def run_node(args, stdin=None):
    node = shutil.which("node")
    if node is None:
        return None
    return subprocess.run(
        [node, str(SCRIPT_DIR / "js_layout_math.mjs"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def python_layout_metrics(layout_path: Path, dem, transform, site_info) -> dict:
    layout = load_layout(layout_path)
    points, distances = interpolate_layout(layout, dem, transform, site_info)
    lap_length = float(distances[-1] + np.linalg.norm(points[0, :2] - points[-1, :2]))
    corners = analyze_corners_xy(points[:, :2], distances, closed=True)
    spacing = mean_sample_spacing(distances, closed_total_length=lap_length)
    _, gradients = grade_profile(
        distances,
        points[:, 2],
        spacing,
        closed_total_length=lap_length,
    )
    raw = [{"position_m": d, "radius_m": r} for d, r in corners["corners"]]
    return {
        "lapLengthM": lap_length,
        "numCorners": corners["corner_count"],
        "minRadiusM": corners["min_radius_m"],
        "maxGradientPct": max_abs_gradient_pct(gradients),
        "corners": raw,
        "pointCount": int(len(points)),
    }


def check_gaussian() -> bool:
    rng = np.random.default_rng(12)
    values = rng.normal(size=240).tolist()
    sigma = 7.5
    expected = gaussian_filter1d(np.asarray(values, dtype=np.float64), sigma)
    res = run_node(["gaussian"], stdin=json.dumps({"values": values, "sigma": sigma}))
    if res.returncode != 0:
        print(f"✗ gaussian JS failed: {res.stderr.strip()}")
        return False
    got = np.asarray(json.loads(res.stdout)["values"], dtype=np.float64)
    err = float(np.max(np.abs(got - expected)))
    ok = err < 1e-8
    print(f"{'✓' if ok else '✗'} Gaussian filter matches scipy (max abs diff {err:.3e})")
    return ok


def compare_corners(js_corners, py_corners, pos_tol, rad_tol, label) -> bool:
    if len(js_corners) != len(py_corners):
        print(f"✗ {label} corner count {len(js_corners)} vs {len(py_corners)}")
        return False
    worst_pos = 0.0
    worst_rad = 0.0
    for js_c, py_c in zip(js_corners, py_corners):
        worst_pos = max(worst_pos, abs(js_c["position_m"] - py_c["position_m"]))
        worst_rad = max(worst_rad, abs(js_c["radius_m"] - py_c["radius_m"]))
    ok = worst_pos <= pos_tol and worst_rad <= rad_tol
    print(
        f"{'✓' if ok else '✗'} {label} corners: "
        f"max |Δ position| {worst_pos:.4f} m, max |Δ radius| {worst_rad:.4f} m"
    )
    return ok


def main() -> int:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("SKIP_JS_PARITY"):
            print("! node not found, layout maths parity skipped (SKIP_JS_PARITY set)")
            return 0
        print("✗ node not found: install Node.js 18+ or set SKIP_JS_PARITY=1")
        return 1

    all_ok = check_gaussian()

    print("\nNishiwaki layouts (JS vs Python checker and check_metrics.json)")
    dem, transform = load_dem(SITE_DIR)
    site_info = load_site_info(SITE_DIR)
    with open(SITE_DIR / "layouts" / "check_metrics.json") as f:
        metrics_file = json.load(f)

    res = run_node(["layouts"])
    if res.returncode != 0:
        print(f"✗ JS layouts failed: {res.stderr.strip()}")
        return 1
    js_layouts = json.loads(res.stdout)["layouts"]

    for name in LAYOUTS:
        py = python_layout_metrics(SITE_DIR / "layouts" / f"{name}.json", dem, transform, site_info)
        js = js_layouts[name]
        stored = metrics_file[name]
        print(f"\n{name}")
        print(
            f"  lap {js['lapLengthM']:.2f} m (py {py['lapLengthM']:.2f}), "
            f"corners {js['numCorners']} (py {py['numCorners']}, file {stored['num_corners']}), "
            f"min R {js['minRadiusM']:.3f} m (py {py['minRadiusM']:.3f}, file {stored['min_radius_m']}), "
            f"max grad {js['maxGradientPct']:.3f}% (py {py['maxGradientPct']:.3f}, file {stored['max_gradient_pct']})"
        )
        lap_ok = abs(js["lapLengthM"] - py["lapLengthM"]) <= PYTHON_TOL_LAP_M
        count_ok = js["numCorners"] == py["numCorners"] == stored["num_corners"]
        radius_py_ok = abs(js["minRadiusM"] - py["minRadiusM"]) <= PYTHON_TOL_RADIUS_M
        radius_file_ok = abs(js["minRadiusM"] - stored["min_radius_m"]) <= METRICS_TOL_RADIUS_M
        grad_py_ok = abs(js["maxGradientPct"] - py["maxGradientPct"]) <= PYTHON_TOL_GRADIENT
        grad_file_ok = abs(js["maxGradientPct"] - stored["max_gradient_pct"]) <= METRICS_TOL_GRADIENT
        file_pass = bool(stored["max_gradient_pass"]) == (stored["max_gradient_pct"] <= MAX_GRADIENT_PCT)
        scalar_ok = all([lap_ok, count_ok, radius_py_ok, radius_file_ok, grad_py_ok, grad_file_ok, file_pass])
        print(
            f"{'✓' if scalar_ok else '✗'} {name} scalars "
            f"(lap Δ {abs(js['lapLengthM'] - py['lapLengthM']):.4f} m, "
            f"R Δpy {abs(js['minRadiusM'] - py['minRadiusM']):.4f}, "
            f"grad Δpy {abs(js['maxGradientPct'] - py['maxGradientPct']):.4f})"
        )
        corners_py = compare_corners(js["corners"], py["corners"], PYTHON_TOL_POS_M, PYTHON_TOL_RADIUS_M, f"{name} vs Python")
        corners_file = compare_corners(
            [{"position_m": round(c["position_m"], 1), "radius_m": round(c["radius_m"], 1)} for c in js["corners"]],
            stored["corners"],
            METRICS_TOL_POS_M,
            METRICS_TOL_RADIUS_M,
            f"{name} vs check_metrics",
        )
        # round() above is Python bankers; the tolerance covers a 0.05 rounding step.
        all_ok = all_ok and scalar_ok and corners_py and corners_file

    print("\nRed Bull Ring (JS vs analyze_track on the fixture DEM)")
    centreline = PROJECT_DIR / "data" / "centrelines" / "red_bull_ring.geojson"
    fixture = PROJECT_DIR / "data" / "fixtures" / "red_bull_ring_dem.tif"
    with contextlib.redirect_stdout(io.StringIO()):
        analysis = analyze(str(centreline), [str(fixture)])
    profile = analysis["profile"]
    coords = np.asarray(profile["coords"], dtype=np.float64)
    distances = np.asarray(profile["distances_m"], dtype=np.float64)
    elevations = np.asarray(profile["elevations_raw_m"], dtype=np.float64)
    curvature = compute_curvature_lonlat(coords, distances)
    raw_corners = [
        {"position_m": d, "radius_m": r} for d, r in find_corners(curvature, distances)
    ]
    spacing = mean_sample_spacing(distances)
    _, gradients = grade_profile(distances, elevations, spacing)
    py_up = float(np.max(gradients))
    py_down = float(np.min(gradients))
    payload = {
        "lons": coords[:, 0].tolist(),
        "lats": coords[:, 1].tolist(),
        "distances": profile["distances_m"],
        "elevations": profile["elevations_raw_m"],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(payload, tmp)
        tmp_path = tmp.name
    try:
        res = run_node(["profile", tmp_path])
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    if res.returncode != 0:
        print(f"✗ JS profile failed: {res.stderr.strip()}")
        return 1
    js = json.loads(res.stdout)
    print(
        f"  corners {js['cornerCount']} (py {analysis['corner_count']}), "
        f"min corner R {js['minCornerRadiusM']:.4f} m (py {min(c['radius_m'] for c in raw_corners):.4f}, "
        f"reported {analysis['min_corner_radius_m']}), "
        f"up {js['maxUphillGradientPct']:.4f}% (py {py_up:.4f}, reported {analysis['max_uphill_gradient_pct']}), "
        f"down {js['maxDownhillGradientPct']:.4f}% (py {py_down:.4f}, reported {analysis['max_downhill_gradient_pct']})"
    )
    rbr_count = js["cornerCount"] == analysis["corner_count"] == len(raw_corners)
    rbr_radius = abs(js["minCornerRadiusM"] - min(c["radius_m"] for c in raw_corners)) <= RBR_TOL_RADIUS_M
    rbr_up = abs(js["maxUphillGradientPct"] - py_up) <= RBR_TOL_GRADIENT
    rbr_down = abs(js["maxDownhillGradientPct"] - py_down) <= RBR_TOL_GRADIENT
    reported_ok = (
        abs(js["minCornerRadiusM"] - analysis["min_corner_radius_m"]) <= 0.05
        and abs(js["maxUphillGradientPct"] - analysis["max_uphill_gradient_pct"]) <= 0.005
        and abs(js["maxDownhillGradientPct"] - analysis["max_downhill_gradient_pct"]) <= 0.005
    )
    rbr_ok = rbr_count and rbr_radius and rbr_up and rbr_down and reported_ok
    print(f"{'✓' if rbr_ok else '✗'} Red Bull Ring scalars")
    rbr_corners = compare_corners(js["corners"], raw_corners, RBR_TOL_POS_M, RBR_TOL_RADIUS_M, "Red Bull Ring vs Python")
    all_ok = all_ok and rbr_ok and rbr_corners

    if not all_ok:
        print("\nLayout maths parity failed")
        return 1
    print("\nLayout maths parity passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
