#!/usr/bin/env python3
"""
Pre-researched candidate sites in Japan for F1-style circuit.

These locations were identified through terrain analysis of Japan's
topography, looking for natural bowls, valleys, and enclosed depressions
with suitable dimensions (2-3km across, 50-150m relief).

Updated to include gentler terrain types:
- Highland plateaus and basins
- Agricultural valleys with surrounding hills  
- Coastal plains with backing ridges
"""

JAPAN_CANDIDATES = [
    # Volcanic regions (may be too steep, included for completeness)
    {
        "name": "Aso Caldera West",
        "prefecture": "Kumamoto",
        "lat": 32.88,
        "lon": 131.02,
        "notes": "Western rim of Aso caldera, volcanic bowl terrain",
    },
    # Highland basins with gentler relief
    {
        "name": "Sado Highland",
        "prefecture": "Niigata",
        "lat": 38.05,
        "lon": 138.38,
        "notes": "Island highland with enclosed valley",
    },
    {
        "name": "Iwaki Basin",
        "prefecture": "Aomori",
        "lat": 40.65,
        "lon": 140.25,
        "notes": "Northern highland bowl",
    },
    # Gentler terrain candidates
    {
        "name": "Ina Valley North",
        "prefecture": "Nagano",
        "lat": 35.85,
        "lon": 137.95,
        "notes": "Central Alps foothill valley",
    },
    {
        "name": "Suwa Basin",
        "prefecture": "Nagano",
        "lat": 36.05,
        "lon": 138.12,
        "notes": "Lake Suwa basin with surrounding hills",
    },
    {
        "name": "Yamanashi Basin",
        "prefecture": "Yamanashi",
        "lat": 35.68,
        "lon": 138.55,
        "notes": "Kofu basin edge with gentle slopes",
    },
    {
        "name": "Tono Basin",
        "prefecture": "Iwate",
        "lat": 39.32,
        "lon": 141.52,
        "notes": "Rural basin surrounded by low hills",
    },
    {
        "name": "Mogami Valley",
        "prefecture": "Yamagata",
        "lat": 38.75,
        "lon": 140.32,
        "notes": "River valley with enclosing ridges",
    },
    {
        "name": "Kitakami Basin",
        "prefecture": "Iwate",
        "lat": 39.45,
        "lon": 141.10,
        "notes": "Agricultural basin with gentle surroundings",
    },
    {
        "name": "Oshu Highland",
        "prefecture": "Iwate",
        "lat": 39.15,
        "lon": 141.15,
        "notes": "Highland plateau with bowl features",
    },
    {
        "name": "Semboku Plain",
        "prefecture": "Akita",
        "lat": 39.70,
        "lon": 140.52,
        "notes": "Interior basin with surrounding hills",
    },
    {
        "name": "Yonezawa Basin",
        "prefecture": "Yamagata",
        "lat": 38.00,
        "lon": 140.12,
        "notes": "Mountain-enclosed agricultural basin",
    },
    {
        "name": "Aizu Basin",
        "prefecture": "Fukushima",
        "lat": 37.50,
        "lon": 139.90,
        "notes": "Historic basin with gentle rim slopes",
    },
    {
        "name": "Ueda Basin",
        "prefecture": "Nagano",
        "lat": 36.40,
        "lon": 138.25,
        "notes": "Chikuma River basin",
    },
    {
        "name": "Matsumoto Plain Edge",
        "prefecture": "Nagano",
        "lat": 36.25,
        "lon": 138.00,
        "notes": "Edge of Matsumoto basin",
    },
    {
        "name": "Saku Basin",
        "prefecture": "Nagano",
        "lat": 36.25,
        "lon": 138.48,
        "notes": "Highland basin in eastern Nagano",
    },
    {
        "name": "Hitoyoshi Basin",
        "prefecture": "Kumamoto",
        "lat": 32.22,
        "lon": 130.78,
        "notes": "River basin surrounded by mountains",
    },
    {
        "name": "Miyakonojo Basin",
        "prefecture": "Miyazaki",
        "lat": 31.72,
        "lon": 131.05,
        "notes": "Southern Kyushu basin",
    },
    {
        "name": "Tsugaru Plain North",
        "prefecture": "Aomori",
        "lat": 40.85,
        "lon": 140.45,
        "notes": "Northern plain with hill backdrop",
    },
    {
        "name": "Kamikawa Basin",
        "prefecture": "Hokkaido",
        "lat": 43.75,
        "lon": 142.35,
        "notes": "Central Hokkaido basin",
    },
]


def load_candidates() -> list[dict]:
    """Load the pre-researched candidate sites."""
    return JAPAN_CANDIDATES.copy()


if __name__ == "__main__":
    print("Pre-researched Japan site candidates:")
    for i, site in enumerate(JAPAN_CANDIDATES, 1):
        print(f"  {i}. {site['name']}, {site['prefecture']}")
        print(f"     {site['lat']:.2f}°N, {site['lon']:.2f}°E")
        print(f"     {site['notes']}")
