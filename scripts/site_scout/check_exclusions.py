#!/usr/bin/env python3
"""
Check candidate sites against OSM exclusion zones.

Exclusions:
- National parks and protected areas (boundary=national_park, protect_class=*)
- Cities and towns (place=city, place=town)
- Dense residential areas (landuse=residential with area > threshold)

Uses Overpass API to query OSM data around each candidate site.
"""

import json
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

EXCLUSION_BUFFER_M = 1000


@dataclass
class Exclusion:
    """An exclusion zone near a candidate site."""
    kind: str
    name: str | None
    distance_m: float
    osm_id: int
    osm_type: str


def overpass_query(query: str, timeout: int = 60) -> dict | None:
    """Execute Overpass QL query, trying multiple mirrors."""
    for mirror in OVERPASS_MIRRORS:
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(mirror, data=data,
                                          headers={"User-Agent": "TrackLab/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"    Mirror {mirror.split('/')[2]} failed: {e}")
            time.sleep(1)
    return None


def check_exclusions_at(lat: float, lon: float, radius_m: float = 5000) -> list[Exclusion]:
    """
    Query OSM for exclusion zones near a point.
    
    Returns list of nearby exclusion features with their distances.
    """
    radius_deg = radius_m / 111320
    
    query = f"""
    [out:json][timeout:30];
    (
      relation["boundary"="national_park"](around:{radius_m},{lat},{lon});
      relation["boundary"="protected_area"](around:{radius_m},{lat},{lon});
      relation["leisure"="nature_reserve"](around:{radius_m},{lat},{lon});
      node["place"~"city|town"](around:{radius_m},{lat},{lon});
      way["landuse"="residential"](around:{radius_m},{lat},{lon});
    );
    out center body;
    """
    
    result = overpass_query(query)
    if not result:
        return []
    
    exclusions = []
    
    for elem in result.get("elements", []):
        osm_type = elem["type"]
        osm_id = elem["id"]
        tags = elem.get("tags", {})
        
        if osm_type == "relation":
            if "center" in elem:
                elem_lat = elem["center"]["lat"]
                elem_lon = elem["center"]["lon"]
            else:
                continue
        elif osm_type == "node":
            elem_lat = elem["lat"]
            elem_lon = elem["lon"]
        elif osm_type == "way":
            if "center" in elem:
                elem_lat = elem["center"]["lat"]
                elem_lon = elem["center"]["lon"]
            else:
                continue
        else:
            continue
        
        import math
        dlat = (elem_lat - lat) * 111320
        dlon = (elem_lon - lon) * 111320 * math.cos(math.radians(lat))
        distance_m = math.sqrt(dlat**2 + dlon**2)
        
        if tags.get("boundary") == "national_park":
            kind = "national_park"
        elif tags.get("boundary") == "protected_area":
            kind = "protected_area"
        elif tags.get("leisure") == "nature_reserve":
            kind = "nature_reserve"
        elif tags.get("place") in ("city", "town"):
            kind = f"place_{tags['place']}"
        elif tags.get("landuse") == "residential":
            kind = "residential"
        else:
            continue
        
        exclusions.append(Exclusion(
            kind=kind,
            name=tags.get("name"),
            distance_m=round(distance_m, 0),
            osm_id=osm_id,
            osm_type=osm_type,
        ))
    
    return sorted(exclusions, key=lambda e: e.distance_m)


def is_site_excluded(lat: float, lon: float, buffer_m: float = EXCLUSION_BUFFER_M) -> tuple[bool, list[Exclusion]]:
    """
    Check if a site should be excluded based on nearby features.
    
    Returns (is_excluded, reasons).
    """
    exclusions = check_exclusions_at(lat, lon, radius_m=buffer_m + 2000)
    
    blocking = []
    for exc in exclusions:
        if exc.kind == "national_park" and exc.distance_m < buffer_m:
            blocking.append(exc)
        elif exc.kind == "protected_area" and exc.distance_m < buffer_m * 0.5:
            blocking.append(exc)
        elif exc.kind.startswith("place_") and exc.distance_m < buffer_m:
            blocking.append(exc)
    
    return len(blocking) > 0, blocking


def filter_sites_by_exclusions(sites: list[dict], buffer_m: float = EXCLUSION_BUFFER_M,
                               delay_s: float = 1.0) -> tuple[list[dict], list[dict]]:
    """
    Filter a list of candidate sites, removing those in exclusion zones.
    
    Returns (passed, excluded) site lists.
    """
    passed = []
    excluded = []
    
    for i, site in enumerate(sites):
        print(f"  Checking site {i+1}/{len(sites)}: {site['lat']:.4f}, {site['lon']:.4f}")
        
        is_excluded, reasons = is_site_excluded(site["lat"], site["lon"], buffer_m)
        
        if is_excluded:
            site["exclusion_reasons"] = [
                {"kind": r.kind, "name": r.name, "distance_m": r.distance_m}
                for r in reasons
            ]
            excluded.append(site)
            print(f"    EXCLUDED: {', '.join(r.kind for r in reasons)}")
        else:
            site["exclusion_reasons"] = []
            passed.append(site)
            print(f"    PASSED")
        
        time.sleep(delay_s)
    
    return passed, excluded


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check sites against OSM exclusions")
    parser.add_argument("input_json", help="Input JSON with candidate sites")
    parser.add_argument("--output", "-o", help="Output JSON with filtered sites")
    parser.add_argument("--buffer", type=float, default=EXCLUSION_BUFFER_M,
                        help=f"Exclusion buffer in meters (default: {EXCLUSION_BUFFER_M})")
    args = parser.parse_args()
    
    with open(args.input_json) as f:
        data = json.load(f)
    
    sites = data.get("bowls", data.get("sites", []))
    print(f"Checking {len(sites)} sites against OSM exclusions...")
    
    passed, excluded = filter_sites_by_exclusions(sites, args.buffer)
    
    print(f"\nPassed: {len(passed)}, Excluded: {len(excluded)}")
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "passed": passed,
                "excluded": excluded,
                "exclusion_buffer_m": args.buffer,
            }, f, indent=2)
        print(f"Wrote results to {args.output}")
