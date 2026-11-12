# Nishiwaki Basin Track Layouts

This directory contains track layout specifications for the Nishiwaki Basin site.

## Layout Format

Each layout is a JSON file with the following structure:

```json
{
  "name": "layout_name",
  "direction": "The plain-English design direction that produced this layout",
  "version": 1,
  "track_width_m": 14,
  "coordinate_system": "local_m",
  "control_points": [
    {"x": 100, "y": 200},
    {"x": 150, "y": 250},
    ...
  ],
  "notes": "Any additional notes about the design"
}
```

### Fields

- **name**: Unique identifier for the layout (matches filename without .json)
- **direction**: The design direction text that guided this layout
- **version**: Schema version (currently 1)
- **track_width_m**: Track width in meters (12-15m for F1-style)
- **coordinate_system**: Either "local_m" (local meters from site center) or "latlon"
- **control_points**: Ordered list of control points forming a closed loop
  - For "local_m": `{x, y}` in meters, where x=east, y=north from site center
  - For "latlon": `{lat, lon}` in degrees
- **notes**: Optional design notes

### Spline Interpolation

Control points define a closed **centripetal Catmull-Rom spline** that is 
draped on the terrain. The spline passes through all control points in order,
forming a smooth closed curve.

### Validation

Run `python3 scripts/layout_check.py <layout.json>` to validate a layout against:
- Lap length (target: 3.5-4.5 km)
- Minimum corner radius (≥15m)
- Maximum longitudinal gradient (≤12%)
- Maximum cross-slope
- No self-intersection
- Stays inside crop bounds
- Cut/fill volume estimate
- Longest straight
- Viewshed from best rim viewpoint
