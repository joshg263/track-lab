#!/usr/bin/env python3
"""Designed vertical profile: limits, bindings, and the JS port.

Python and viewer/vertical_profile.js must agree. A gentle bump has to clear
the grade, radius and depth limits. On the real layouts the depth cap is kept
even where the grade or a vertical curve cannot, and every miss is named.

Offline. No network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

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
from road_profile import (  # noqa: E402
    GRADIENT_WINDOW_M,
    grade_profile,
    max_abs_gradient_pct,
    mean_sample_spacing,
)
from vertical_profile import (  # noqa: E402
    DEFAULT_MAX_CUT_FILL_M,
    DEFAULT_MIN_CREST_RADIUS_M,
    DEFAULT_MIN_DIP_RADIUS_M,
    DEPTH_MARGIN_M,
    GRADIENT_MARGIN,
    binding_report,
    build_vertical_profile,
    earthworks,
    fit_profile,
    geometry_summary,
    violations_are_reported,
)

# Elevations from the same operations. Volumes pick up a few ulps from the
# catch walk, so the allowance is far tighter than a cubic metre.
Z_TOL_M = 1e-6
VOLUME_TOL_M3 = 0.05

LAYOUTS = [
    ("layout_a_straight_hairpin", "A", SITE_DIR / "layouts" / "layout_a_straight_hairpin.json"),
    ("layout_b_elevation_sweep", "B", SITE_DIR / "layouts" / "layout_b_elevation_sweep.json"),
    ("layout_c_arena", "C", SITE_DIR / "layouts" / "layout_c_arena.json"),
    ("version-1", "version-1", SITE_DIR / "layouts" / "edits" / "version-1.json"),
    ("version-2", "version-2", PROJECT_DIR / "scripts" / "fixtures" / "version-2.json"),
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def run_js(mode: str, payload: dict | None = None) -> dict:
    result = subprocess.run(
        ["node", str(SCRIPT_DIR / "js_vertical_profile.mjs"), mode],
        input=None if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        fail(f"js {mode} failed\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


def assert_limits_or_bindings(name: str, terrain: np.ndarray, design: np.ndarray, spacing: float, runs: list) -> None:
    gaps = violations_are_reported(
        terrain,
        design,
        spacing,
        runs,
        max_grade=MAX_GRADIENT_PCT / 100.0,
        max_cut_fill_m=DEFAULT_MAX_CUT_FILL_M,
        min_crest_radius_m=DEFAULT_MIN_CREST_RADIUS_M,
        min_dip_radius_m=DEFAULT_MIN_DIP_RADIUS_M,
    )
    if gaps:
        fail(f"{name}: {gaps[0]}")
    depth = np.max(np.abs(design - terrain))
    depth_violated = any(r["kind"] == "depth" and r["status"] == "violated" for r in runs)
    if depth > DEFAULT_MAX_CUT_FILL_M + DEPTH_MARGIN_M and not depth_violated:
        fail(f"{name}: depth {depth:.3f} m exceeds the cap with no depth run")
    if depth > DEFAULT_MAX_CUT_FILL_M + DEPTH_MARGIN_M:
        fail(f"{name}: depth {depth:.3f} m exceeds the cap")


def check_bump() -> None:
    n = 512
    lap = 4000.0
    ds = lap / n
    s = np.arange(n) * ds

    def bump(center, sigma, amp):
        d = np.minimum(np.abs(s - center), lap - np.abs(s - center))
        return amp * np.exp(-0.5 * (d / sigma) ** 2)

    terrain = 100.0 + bump(1000, 40, 3) + bump(2500, 90, 2.5)
    design, status = fit_profile(terrain, ds, max_grade=0.12)
    if status != "converged":
        fail(f"bump solve status {status}")
    summary = geometry_summary(design, ds)
    body = {
        "terrain": terrain.tolist(),
        "spacing": ds,
        "maxGrade": 0.12,
        "maxCutFillM": 6,
        "minCrestRadiusM": 1000,
        "minDipRadiusM": 600,
    }
    got = run_js("fit", body)
    err = float(np.max(np.abs(np.array(got["design"]) - design)))
    if err > Z_TOL_M:
        fail(f"bump Python/JS elevation differs by {err:.3e} m")
    if got["solve_status"] != "converged":
        fail(f"bump JS solve status {got['solve_status']}")
    if got["gaps"]:
        fail(f"bump JS left a gap: {got['gaps'][0]}")
    if got["runs"]:
        fail(f"bump JS reported a binding run on a feasible profile: {got['runs'][0]}")
    depth = float(np.max(np.abs(design - terrain)))
    grade = summary["max_grade"]
    crest = summary["min_crest_radius_m"]
    dip = summary["min_dip_radius_m"]
    if grade > 0.12 + GRADIENT_MARGIN or depth > 6 or crest < 1000 or dip < 600:
        fail(f"bump missed a limit: grade {grade:.4f} crest {crest:.0f} dip {dip:.0f} depth {depth:.3f}")
    print(
        f"bump: grade {grade * 100:.2f}% crest {crest:.0f} m dip {dip:.0f} m "
        f"depth {depth:.3f} m, Python/JS |dz| {err:.1e} m"
    )


def check_feasible_wave() -> None:
    """A wave inside the depth cap has a feasible flat profile, so it must clear."""
    n = 1000
    ds = 4.0
    s = np.arange(n) * ds
    terrain = 100.0 + 6.0 * np.sin(2.0 * np.pi * s / 200.0)
    short, short_status = fit_profile(terrain, ds, max_grade=0.12, max_sweeps=1)
    if short_status != "not converged":
        fail(f"wave with one sweep should be not converged, got {short_status}")
    short_summary = geometry_summary(short, ds)
    if (
        short_summary["min_crest_radius_m"] is not None
        and short_summary["min_crest_radius_m"] >= 1000
        and short_summary["min_dip_radius_m"] is not None
        and short_summary["min_dip_radius_m"] >= 600
    ):
        fail("one sweep already cleared the wave; the cap test no longer separates outcomes")
    design, status = fit_profile(terrain, ds, max_grade=0.12)
    if status != "converged":
        fail(f"wave solve status {status}")
    runs = binding_report(
        terrain,
        design,
        ds,
        max_grade=0.12,
        max_cut_fill_m=DEFAULT_MAX_CUT_FILL_M,
        min_crest_radius_m=DEFAULT_MIN_CREST_RADIUS_M,
        min_dip_radius_m=DEFAULT_MIN_DIP_RADIUS_M,
    )
    assert_limits_or_bindings("wave", terrain, design, ds, runs)
    violated = [r for r in runs if r["status"] == "violated"]
    if violated:
        fail(f"wave is feasible within the depth cap but reported {violated[0]['kind']}")
    summary = geometry_summary(design, ds)
    if summary["min_crest_radius_m"] < 1000 or summary["min_dip_radius_m"] < 600:
        fail(
            f"wave radii crest {summary['min_crest_radius_m']:.0f} "
            f"dip {summary['min_dip_radius_m']:.0f}"
        )
    got = run_js("fit", {
        "terrain": terrain.tolist(),
        "spacing": ds,
        "maxGrade": 0.12,
        "maxCutFillM": 6,
        "minCrestRadiusM": 1000,
        "minDipRadiusM": 600,
    })
    err = float(np.max(np.abs(np.array(got["design"]) - design)))
    if err > Z_TOL_M:
        fail(f"wave Python/JS elevation differs by {err:.3e} m")
    if got["solve_status"] != "converged":
        fail(f"wave JS solve status {got['solve_status']}")
    if any(r["status"] == "violated" for r in got["runs"]):
        fail(f"wave JS reported a violation on a feasible profile: {got['runs'][0]}")
    capped = run_js("fit", {
        "terrain": terrain.tolist(),
        "spacing": ds,
        "maxGrade": 0.12,
        "maxCutFillM": 6,
        "minCrestRadiusM": 1000,
        "minDipRadiusM": 600,
        "maxSweeps": 1,
    })
    if capped["solve_status"] != "not converged":
        fail(f"wave JS one-sweep status {capped['solve_status']}")
    if capped["solve_status"] == "infeasible within the 6 m cap":
        fail("wave JS reported the depth cap for a profile that had not converged")
    print(
        f"wave: crest {summary['min_crest_radius_m']:.0f} m "
        f"dip {summary['min_dip_radius_m']:.0f} m, status {status}, Python/JS |dz| {err:.1e} m"
    )


def check_open_boundary() -> None:
    """A side that never daylights keeps the 1:2 face in the end strip."""
    width = 14.0
    n = 4
    x = np.arange(n, dtype=np.float64)
    y = np.zeros(n, dtype=np.float64)
    design = np.zeros(n, dtype=np.float64)

    def ground(_x, py):
        return 6.0 + abs(float(py))

    works = earthworks(x, y, design, design, 1.0, width, ground)
    area = works["cut_m3"] / n
    # 14 m deck at 0, ground 6 + |lateral|, open at 80 m. The last strip stays
    # on the slope, so the section is 5413 m² of cut rather than 5307.
    if abs(area - 5413.0) > 1.0:
        fail(f"open boundary cut area {area:.2f} m², expected 5413")
    if works["fill_m3"] > 1e-6:
        fail(f"open boundary fill {works['fill_m3']:.3e} m³ should be zero")
    if any(s["left"]["daylight"] or s["right"]["daylight"] for s in works["sections"]):
        fail("open boundary fixture daylit inside 80 m")
    if not works["open_m"] or not all(s["open"] for s in works["sections"]):
        fail("open boundary stations are not flagged open in the volume report")
    got = run_js("section", {"width": width, "z": 0.0, "offset": 6.0})
    if abs(got["per_section_cut"] - area) > VOLUME_TOL_M3:
        fail(
            f"open boundary Python/JS cut differs: {area:.4f} vs {got['per_section_cut']:.4f}"
        )
    if got["left_daylight"] or got["right_daylight"] or not got["open"]:
        fail("JS open boundary fixture was not flagged open")
    print(f"open boundary: cut {area:.2f} m², still open at 80 m, Python/JS agree")


def check_infeasible_hill() -> None:
    n = 500
    lap = 4000.0
    ds = lap / n
    s = np.arange(n) * ds
    # 100 m over 400 m. Six metres of cut and fill cannot bring this under 12%.
    terrain = 100.0 + 100.0 * np.clip(1.0 - np.abs(s - 1000.0) / 200.0, 0.0, 1.0)
    design, status = fit_profile(terrain, ds, max_grade=0.12)
    if status != "infeasible within the 6 m cap":
        fail(f"hill solve status {status}")
    if status == "not converged":
        fail("hill was reported not converged instead of infeasible within the cap")
    runs = binding_report(
        terrain,
        design,
        ds,
        max_grade=0.12,
        max_cut_fill_m=DEFAULT_MAX_CUT_FILL_M,
        min_crest_radius_m=DEFAULT_MIN_CREST_RADIUS_M,
        min_dip_radius_m=DEFAULT_MIN_DIP_RADIUS_M,
    )
    assert_limits_or_bindings("hill", terrain, design, ds, runs)
    if not any(r["kind"] == "gradient" and r["status"] == "violated" for r in runs):
        fail("hill met the grade limit; the fixture is supposed to show a binding grade")
    depth = float(np.max(np.abs(design - terrain)))
    got = run_js("fit", {
        "terrain": terrain.tolist(),
        "spacing": ds,
        "maxGrade": 0.12,
        "maxCutFillM": 6,
        "minCrestRadiusM": 1000,
        "minDipRadiusM": 600,
    })
    if got["solve_status"] != status:
        fail(f"hill JS status {got['solve_status']} != {status}")
    print(f"hill: depth {depth:.3f} m, {status}, gradient runs reported")


def windowed_grade(distances, elevations, lap) -> float:
    spacing = mean_sample_spacing(distances, closed_total_length=lap)
    _, gradients = grade_profile(distances, elevations, spacing, closed_total_length=lap)
    return max_abs_gradient_pct(gradients)


def load_profiles():
    dem, transform = load_dem(SITE_DIR)
    site = load_site_info(SITE_DIR)

    def ground(x, y):
        return float(sample_dem(dem, transform, np.array([x]), np.array([y]), site)[0])

    profiles = {}
    for key, label, path in LAYOUTS:
        layout = load_layout(path)
        points, distances = interpolate_layout(layout, dem, transform, site)
        lap = float(distances[-1] + np.linalg.norm(points[0, :2] - points[-1, :2]))
        profile = build_vertical_profile(
            points[:, 0],
            points[:, 1],
            distances,
            points[:, 2],
            lap,
            float(layout.get("track_width_m", 14)),
            ground,
            max_grade=MAX_GRADIENT_PCT / 100.0,
        )
        profile["label"] = label
        profile["terrain_window_pct"] = windowed_grade(distances, points[:, 2], lap)
        profile["design_window_pct"] = windowed_grade(distances, profile["design_at_points"], lap)
        profiles[key] = profile
    return profiles


def compare_js(profiles: dict, js_layouts: dict) -> None:
    for key, profile in profiles.items():
        other = js_layouts[key]
        z_err = float(np.max(np.abs(np.array(other["design_z"]) - profile["design_z"])))
        cut_err = abs(other["earthworks"]["cut_m3"] - profile["earthworks"]["cut_m3"])
        fill_err = abs(other["earthworks"]["fill_m3"] - profile["earthworks"]["fill_m3"])
        if z_err > Z_TOL_M or cut_err > VOLUME_TOL_M3 or fill_err > VOLUME_TOL_M3:
            fail(
                f"{key}: Python/JS disagree (dz {z_err:.3e} m, "
                f"cut {cut_err:.4f} m3, fill {fill_err:.4f} m3)"
            )
        if len(other["bindings"]) != len(profile["bindings"]):
            fail(f"{key}: binding count Python {len(profile['bindings'])} JS {len(other['bindings'])}")
        if other.get("solve_status") != profile["solve_status"]:
            fail(f"{key}: solve status Python {profile['solve_status']} JS {other.get('solve_status')}")
        print(
            f"{profile['label']}: Python/JS |dz| {z_err:.1e} m, "
            f"volume Δ {max(cut_err, fill_err):.4f} m³, {len(profile['bindings'])} binding runs"
        )


def check_layouts(profiles: dict) -> None:
    for key, profile in profiles.items():
        assert_limits_or_bindings(
            profile["label"],
            profile["terrain_z"],
            profile["design_z"],
            profile["spacing_m"],
            profile["bindings"],
        )
        if abs(profile["join_step_m"]) / profile["spacing_m"] > 0.5:
            fail(f"{profile['label']}: join step {profile['join_step_m']:.2f} m is a discontinuity")


def fmt_radius(value) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}"


def print_table(profiles: dict) -> None:
    print()
    print("| Layout | Terrain grade | Design grade | Terrain crest | Design crest | Terrain dip | Design dip | Max cut | Max fill | Cut m³ | Fill m³ |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key, label, _path in LAYOUTS:
        p = profiles[key]
        ew = p["earthworks"]
        print(
            f"| {label} | {p['terrain_window_pct']:.1f}% | {p['design_window_pct']:.1f}% | "
            f"{fmt_radius(p['terrain']['min_crest_radius_m'])} m | {fmt_radius(p['design']['min_crest_radius_m'])} m | "
            f"{fmt_radius(p['terrain']['min_dip_radius_m'])} m | {fmt_radius(p['design']['min_dip_radius_m'])} m | "
            f"{ew['max_cut_m']:.2f} m | {ew['max_fill_m']:.2f} m | {ew['cut_m3']:.0f} | {ew['fill_m3']:.0f} |"
        )
        open_n = len(ew.get("open_m") or [])
        if open_n or p["solve_status"] != "converged":
            print(f"  {label}: solve {p['solve_status']}, {open_n} stations open at 80 m")
    print()
    print(f"Gradient is the {GRADIENT_WINDOW_M:.0f} m window used by the checker. Radii are local osculating radii.")
    print("Python and JS elevations agree within 1e-6 m and volumes within 0.05 m³.")


def write_chart(profile: dict, path: Path) -> None:
    terrain = np.asarray(profile["terrain_z"], dtype=np.float64)
    design = np.asarray(profile["design_z"], dtype=np.float64)
    n = len(terrain)
    lap = float(profile["lap_m"])
    width, height = 1200, 460
    image = Image.new("RGB", (width, height), "#f7f4ee")
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 70, 30, 36, 48
    lo = float(min(terrain.min(), design.min()))
    hi = float(max(terrain.max(), design.max()))
    span = max(hi - lo, 1.0)
    pad = span * 0.06

    def x_of(i):
        return left + i / max(n - 1, 1) * (width - left - right)

    def y_of(z):
        return top + (hi + pad - z) / (span + 2 * pad) * (height - top - bottom)

    for km in range(0, int(lap / 1000) + 1):
        x = left + (km * 1000 / lap) * (width - left - right)
        draw.line([(x, top), (x, height - bottom)], fill=(226, 220, 210))
    for i in range(n - 1):
        colour = (224, 122, 61) if terrain[i] >= design[i] else (61, 139, 255)
        draw.polygon(
            [
                (x_of(i), y_of(terrain[i])),
                (x_of(i + 1), y_of(terrain[i + 1])),
                (x_of(i + 1), y_of(design[i + 1])),
                (x_of(i), y_of(design[i])),
            ],
            fill=colour,
        )
    raw = [(x_of(i), y_of(terrain[i])) for i in range(n)]
    designed = [(x_of(i), y_of(design[i])) for i in range(n)]
    draw.line(raw, fill=(90, 98, 110), width=2)
    draw.line(designed, fill=(20, 140, 80), width=3)
    draw.text((left, 12), "Layout A  ·  raw terrain and designed profile", fill=(30, 30, 30))
    draw.text((left, height - 32), "Chainage, km", fill=(60, 60, 60))
    for km in range(0, int(lap / 1000) + 1):
        x = left + (km * 1000 / lap) * (width - left - right)
        draw.text((x + 2, height - 28), str(km), fill=(60, 60, 60))
    draw.text((8, top), f"{hi:.0f} m", fill=(60, 60, 60))
    draw.text((8, height - bottom - 14), f"{lo:.0f} m", fill=(60, 60, 60))
    legend_y = 14
    draw.rectangle([width - 280, legend_y, width - 264, legend_y + 10], fill=(90, 98, 110))
    draw.text((width - 258, legend_y - 2), "raw", fill=(30, 30, 30))
    draw.rectangle([width - 210, legend_y, width - 194, legend_y + 10], fill=(20, 140, 80))
    draw.text((width - 188, legend_y - 2), "design", fill=(30, 30, 30))
    draw.rectangle([width - 130, legend_y, width - 114, legend_y + 10], fill=(224, 122, 61))
    draw.text((width - 108, legend_y - 2), "cut", fill=(30, 30, 30))
    draw.rectangle([width - 70, legend_y, width - 54, legend_y + 10], fill=(61, 139, 255))
    draw.text((width - 48, legend_y - 2), "fill", fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f"chart {path}")


def main() -> int:
    check_bump()
    check_feasible_wave()
    check_open_boundary()
    check_infeasible_hill()
    print("fitting layouts...")
    profiles = load_profiles()
    check_layouts(profiles)
    js = run_js("layouts")
    compare_js(profiles, js["layouts"])
    print_table(profiles)
    write_chart(profiles["layout_a_straight_hairpin"], PROJECT_DIR / "docs" / "profile_raw_vs_design.png")
    print("vertical profile verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
