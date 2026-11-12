# Japan Site Scout - Spike 4 (Kansai Focus)

This spike identifies candidate natural bowls and valleys in Japan's Kansai region that could hold an F1-style circuit, using free public elevation data and systematic terrain analysis.

## Scope

**Kansai region only**: Osaka, Kyoto, Hyogo, Nara, Shiga, Wakayama, Mie prefectures.

## Selected Sites

| Site | Prefecture | Lat/Lon | Floor Extent | Relief | Rim Slopes | Rim Contiguous | Best VP | Floor Mean | Nearest Town |
|------|------------|---------|--------------|--------|------------|----------------|---------|------------|--------------|
| **Nishiwaki Basin** | Hyogo | 34.99°N, 135.01°E | 2,356 m | 127 m | 7°–33° | 350 m | 57.5% | 16.8% | Nishiwaki (2.8 km) |

Only **1 survivor** from systematic Kansai scan. The region's topography consists mostly of mountain valleys that exceed the target size (4000m+) or relief (200m+).

## Site Criteria

### Hard Filters (Strict - No Relaxation)
- **Floor extent**: 2–3 km across (bounding box of floor region)
- **Relief**: 50–150 m between floor and rim
- **Rim slopes**: 10–30° over at least 300m contiguous stretch (suitable for grandstands)
- **Arena check**: Best rim viewpoint floor visibility must exceed mean of 10 seeded-random floor points

### Exclusions
- OSM `boundary=national_park` centroid intersection
- OSM `boundary=protected_area` with `protect_class` in (1, 1a, 1b, 2) - core protected zones
- OSM `place=city|town|village` within ~1 km
- OSM `landuse=residential` with >10% floor overlap

## Scan Funnel

```
Cells scanned:        15,840,000 (11 GLO-90 tiles)
Coarse candidates:           75 (TPI basin detection)
Refined at GLO-30:           40
After strict filters:         1
After OSM exclusions:         1
Final survivors:              1
```

### Common Rejection Reasons

Most candidates failed for:
- Floor extent > 3000m (typically 4000-4500m) - valleys are too large
- Relief > 150m (typically 200-300m) - too much vertical drop
- Rim contiguous < 300m - insufficient grandstand-suitable terrain

## Methodology

### Stage 1: Download GLO-90 Tiles
Download Copernicus GLO-90 (90m resolution) tiles covering Kansai region (11 tiles total).

### Stage 2: Basin Detection
For each tile:
1. Compute Topographic Position Index (TPI) at radius 5 cells
2. Find local minima using minimum filter (11x11 kernel)
3. Seed depressions where TPI < -8 and is local minimum
4. Flood fill from seeds up to 100m above floor to define basin extent
5. Apply coarse filters (60% of strict thresholds) to reduce candidates

### Stage 3: GLO-30 Refinement
Download 30m resolution DEM for top ~40 candidates and:
1. Recompute floor/rim from actual percentiles
2. Measure floor extent as bounding box dimension
3. Compute slope gradient and identify 10-30° rim cells
4. Find contiguous 10-30° slope regions and measure extent
5. Apply strict filters with no relaxation

### Stage 4: Viewshed Analysis
For passing candidates:
- Sample 15 rim points and compute floor visibility
- Sample 10 seeded-random floor points and compute their visibility
- Best viewpoint must exceed mean floor visibility (arena effect)

### Stage 5: OSM Exclusion Check
Query Overpass API for parks, protected areas, towns, residential zones. Reject if:
- Centroid in national park core
- >10% floor overlaps residential
- Town/village within 1km buffer

## Pipeline Usage

```bash
# Run systematic Kansai scan
python3 scripts/site_scout/kansai_scan.py

# Generate viewer data for survivors
python3 scripts/site_scout/generate_site_data.py
python3 scripts/site_scout/export_viewer_data.py

# Verify site metrics
make site-scout-verify
```

## Viewer

```bash
make serve
# Open http://localhost:8000/site.html?site=nishiwaki_basin
```

Features:
- Site selector dropdown
- "Best Viewpoint" button (positions camera at optimal rim viewing point)
- Overview button (returns to default camera)
- Vertical exaggeration control

## Data Sources

| Data | Source | License |
|------|--------|---------|
| Elevation | [Copernicus DEM GLO-90/30](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model) | Free & open (Copernicus) |
| Imagery | [Sentinel-2 cloudless](https://s2maps.eu) by EOX IT Services GmbH | CC BY 4.0 |
| Exclusions | [OpenStreetMap](https://www.openstreetmap.org/copyright) | ODbL 1.0 |

## Caveats

1. **One Survivor**: The strict criteria yield only one site in Kansai. Japan's mountain valleys tend to be larger (4000m+) with steeper relief (200m+) than the target bowl shape.

2. **DEM Resolution**: 30m resolution smooths small-scale terrain features. Actual site surveys would reveal more detail.

3. **Viewshed Simplification**: Line-of-sight calculation doesn't account for vegetation or structures not in DEM.

4. **OSM Coverage**: Exclusion data varies by region. Some protected areas may not be fully mapped.

5. **Land Ownership**: Analysis doesn't consider ownership, access rights, or development restrictions.

6. **Geologic Suitability**: Terrain shape doesn't indicate soil stability, drainage, or construction factors.

## Anti-Jobs

This spike explicitly does NOT:
- Search regions outside Kansai
- Design track layouts
- Require API keys or paid data sources
- Polish the viewer beyond functional state
