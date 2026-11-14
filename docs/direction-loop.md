# AI Direction Loop for Track Design

This document describes the workflow for turning plain-English design directions into drivable circuit layouts on real terrain.

## Overview

The direction loop converts a high-level design intent (e.g., "a long straight into a hairpin") into:
1. A track layout file with control points
2. Validation against rough F1-style heuristics
3. A 3D preview draped on real terrain
4. Actionable feedback for iteration

## Quick Start

```bash
# 1. Edit or create a layout file
nano data/sites/nishiwaki_basin/layouts/layout_a_straight_hairpin.json

# 2. Check the layout against rules
make layout-check LAYOUT=data/sites/nishiwaki_basin/layouts/layout_a_straight_hairpin.json

# 3. View in browser (with 'make serve' running)
# Open: http://localhost:8000/site.html?site=nishiwaki_basin&layout=layout_a_straight_hairpin

# 4. Check all layouts and regenerate viewer data
make layout-check-all
```

## Layout File Format

Layouts are stored in `data/sites/<site>/layouts/<name>.json`:

```json
{
  "name": "layout_a_straight_hairpin",
  "direction": "A long straight across the basin floor into a hairpin under the north rim",
  "version": 1,
  "track_width_m": 14,
  "coordinate_system": "local_m",
  "control_points": [
    {"x": -600, "y": -300, "note": "Start/finish"},
    {"x": -600, "y": 400, "note": "Main straight"},
    ...
  ],
  "notes": "Design notes and limitations"
}
```

### Coordinate System

- `local_m`: East/North meters from site center (recommended)
- `latlon`: WGS84 latitude/longitude

### Control Points

An ordered list defining a **closed centripetal Catmull-Rom spline**:
- Points are visited in sequence
- The spline passes through every control point
- The curve automatically closes (last point connects to first)
- Add more control points for tighter curve control

## Rules Checker

The checker (`scripts/layout_check.py`) validates layouts against rough F1-style heuristics:

| Check | Target | Notes |
|-------|--------|-------|
| Lap length | 3.5–4.5 km | Measured along spline centerline |
| Min corner radius | ≥15 m | Smallest radius in any corner |
| Max gradient | ≤12% | Graded road profile (15m σ) over 100m window (`scripts/road_profile.py`) |
| Max cross-slope | ≤8% | Track banking (simplified) |
| No self-intersection | None | Track cannot cross itself |
| Inside bounds | 100% | All points within DEM extent |
| Cut/fill volume | Info | Estimated earthwork for gradient smoothing |
| Longest straight | Info | Useful for DRS zone planning |
| Viewshed % | Info | Track visible from best rim viewpoint |

### Running the Checker

```bash
# Single layout
python3 scripts/layout_check.py data/sites/nishiwaki_basin/layouts/layout_a.json

# All layouts with summary table
python3 scripts/layout_check.py --all

# JSON output for automation
python3 scripts/layout_check.py --all --json
```

## Redirect Workflow

To modify a layout based on feedback:

### 1. Read the Checker Output

```
Check                     Status   Value           Target
----------------------------------------------------------------------
lap_length                ✓        4299.0          3500-4500 m
min_corner_radius         ✓        36.6            ≥15 m
max_gradient              ✗        31.5            ≤12%
```

### 2. Interpret the Failure

- **Gradient too high**: Control points go through steep terrain
- **Radius too small**: Hairpin is too tight, add intermediate points
- **Length wrong**: Add/remove control points or adjust spacing
- **Self-intersection**: Visualize in browser to find the crossing

### 3. Edit the Layout

Example: "Tighten turn 3, add a chicane before the hairpin"

```diff
  {"x": -600, "y": 400, "note": "Approaching hairpin"},
+ {"x": -550, "y": 500, "note": "Chicane left"},
+ {"x": -500, "y": 520, "note": "Chicane right"},
  {"x": -350, "y": 600, "note": "Hairpin entry - tightened"},
- {"x": -200, "y": 650, "note": "Old hairpin entry"},
```

### 4. Re-check and Preview

```bash
# Re-run checker
make layout-check LAYOUT=data/sites/nishiwaki_basin/layouts/layout_a.json

# Refresh browser to see updated layout
```

## Terrain Constraints at Nishiwaki Basin

The Nishiwaki Basin site has specific terrain challenges:

### Floor Geography
- **Flat zone** (<100m elevation): Western corridor and far north/south
- **Obstacle**: Central high ground (130-200m) blocks east-west connection
- **Relief**: 144m from floor (89m) to rim (233m)

### Practical Limits
- A 3.5-4.5 km circuit **cannot** meet the 12% gradient limit without earthwork
- The flat area is ~1.7 km² but fragmented
- Any layout crossing the central obstacle requires ~120-200k m³ cut/fill

### Recommended Approach
1. Keep circuits in the western corridor and far north/south flat zones
2. Accept gradient failures and note earthwork requirements in layout notes
3. Use cut/fill estimate to evaluate construction feasibility

## Viewer Features

The site viewer (`site.html`) shows:
- Terrain with satellite imagery
- Track ribbon draped on terrain (cyan, with outline)
- Start/finish marker (checkered)
- Corner labels (T1, T2, etc.)
- Layout stats panel with pass/fail indicators
- Best viewpoint marker for arena analysis

### URL Parameters
- `?site=nishiwaki_basin` - Select site
- `?layout=layout_a_straight_hairpin` - Load layout
- `?capture` - Enable screenshot mode (preserveDrawingBuffer)

## Anti-Jobs

This system explicitly does NOT:
- Claim FIA homologation compliance (rules are "rough heuristics")
- Guarantee constructability (earthwork is estimated only)
- Automatically generate optimal layouts
- Replace professional track design

## Make Targets

```bash
make layout-check LAYOUT=<path>   # Check one layout
make layout-check-all             # Check all Nishiwaki layouts
make direction-loop-verify        # Full verification suite
make serve                        # Start viewer server
```
