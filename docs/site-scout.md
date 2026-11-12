# Japan Site Scout - Spike 4

This spike identifies candidate natural bowls and valleys in Japan that could hold an F1-style circuit, using free public elevation data and deterministic terrain analysis.

## Selected Sites

| Site | Prefecture | Lat/Lon | Diameter | Relief | Floor Flat | Rim Slopes | Best VP Visibility | Nearest Town | Why It Fits |
|------|------------|---------|----------|--------|------------|------------|-------------------|--------------|-------------|
| **Yamanashi Basin** | Yamanashi | 35.68°N, 138.55°E | 2,488 m | 86 m | 88% | 10°–28° | 61.4% | Kofu area | Natural bowl with ideal dimensions |
| **Sado Highland** | Niigata | 38.05°N, 138.38°E | 1,903 m | 92 m | 64% | 4°–16° | 53.6% | Sado Island | Very flat floor for track layout |
| **Hitoyoshi Basin** | Kumamoto | 32.22°N, 130.78°E | 2,005 m | 50 m | 69% | 4°–17° | 37.7% | Nishiki (6 km) | Natural bowl with ideal dimensions |
| **Iwaki Basin** | Aomori | 40.65°N, 140.25°E | 1,780 m | 158 m | 26% | 8°–20° | 16.7% | Northern highlands | Good balance of size and enclosure |

## Site Criteria

### Hard Filters
- **Diameter**: 2–3 km across (relaxed to 1.5–4 km for initial scan)
- **Relief**: 50–150 m between floor and rim (relaxed to 40–180 m)
- **Rim slopes**: 10–30° on at least part of the rim (suitable for grandstands)

### Soft Filters
- **Floor flatness**: Percentage of floor cells under 5° slope
- **Arena effect**: Best viewpoint floor visibility (what % of the floor is visible from the best rim position)

### Exclusions
- OSM `boundary=national_park` within 1 km
- OSM `place=city` or `place=town` within 1 km
- OSM `landuse=residential` (dense residential areas)

## Methodology

### Stage 1: Coarse Scan
Used pre-researched candidate coordinates based on Japan's topographic features (volcanic calderas, highland basins, river valleys). Downloaded Copernicus GLO-30 DEM at 30m resolution for each candidate.

### Stage 2: Terrain Analysis
For each site:
1. Compute floor elevation (15th percentile) and rim elevation (85th percentile)
2. Calculate relief (rim - floor)
3. Compute floor flatness using slope gradient
4. Identify rim cells with suitable grandstand slopes (10–30°)

### Stage 3: Viewshed Analysis
Sample viewpoints around the rim and compute line-of-sight visibility to floor cells:
- Observer height: 2m above ground
- Target height: 1m above ground (track surface)
- Best viewpoint: highest floor visibility percentage

### Stage 4: Exclusion Check
Query OSM Overpass API for national parks and towns within buffer distance. Sites with blocking exclusions are removed.

### Stage 5: Geographic Diversity
Select final sites to maximize geographic spread across Japan while prioritizing viewshed quality.

## Pipeline Usage

```bash
# Run the full pipeline (downloads DEM, analyzes, generates viewer data)
make site-scout

# Generate viewer data for already-committed sites
make site-scout-data

# Verify all site metrics
make site-scout-verify
```

## Viewer

View selected sites in the 3D terrain viewer:

```bash
make serve
# Open http://localhost:8000/site.html
# Or directly: http://localhost:8000/site.html?site=yamanashi_basin
```

The viewer supports:
- Site selector dropdown
- "Best Viewpoint" button (positions camera at optimal viewing point)
- Overview button (returns to default camera)
- Vertical exaggeration control

## Excluded Sites

The following pre-researched candidates were excluded:

| Site | Reason |
|------|--------|
| Aso Caldera | Relief too high (230m) - volcanic terrain too steep |
| Hakone Caldera | Relief too high (331m) |
| Bandai Highland | Relief too high (518m) |
| Kirishima Basin | Relief too high (249m) |
| Nasu Highland | Relief too high (556m) |
| Kamikawa Basin (Hokkaido) | Relief too low (10m) - too flat |
| Aizu Basin | Relief too low (11m) |

## Data Sources

| Data | Source | License |
|------|--------|---------|
| Elevation | [Copernicus DEM GLO-30](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model) | Free & open (Copernicus) |
| Imagery | [Sentinel-2 cloudless](https://s2maps.eu) by EOX IT Services GmbH | CC BY 4.0 |
| Exclusions | [OpenStreetMap](https://www.openstreetmap.org/copyright) | ODbL 1.0 |

## Caveats

1. **DEM Resolution**: 30m resolution means small-scale terrain features are smoothed. Actual site surveys would reveal more detail.

2. **Viewshed Simplification**: The viewshed algorithm uses a simplified line-of-sight calculation without considering vegetation or structures that may not be in the DEM.

3. **OSM Coverage**: OSM exclusion data coverage varies by region. Some national parks or towns may not be fully mapped.

4. **Land Ownership**: This analysis does not consider land ownership, access rights, or development restrictions. Actual site selection would require legal and administrative review.

5. **Geologic Suitability**: Terrain shape does not indicate soil stability, drainage, or other construction factors.

## What I'd Do Differently With More Time

1. **Broader Scan**: Use GLO-90 (90m resolution) to scan all of Japan systematically, then refine promising areas at 30m.

2. **Multi-scale Analysis**: Implement proper topographic position index (TPI) at multiple scales to find enclosed depressions algorithmically.

3. **Better Locality Names**: Use reverse geocoding APIs for accurate place names instead of OSM node queries.

4. **Infrastructure Proximity**: Consider proximity to highways, rail stations, and airports for accessibility.

5. **Climate Data**: Incorporate precipitation and temperature data for race calendar planning.

6. **3D Viewshed**: Full 3D viewshed analysis with proper obstruction modeling (trees, buildings from OSM).

7. **Interactive Selection**: Allow users to click on the map to analyze custom locations in real-time.
