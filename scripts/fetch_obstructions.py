#!/usr/bin/env python3
"""
Fetch line-of-sight obstructions (buildings, grandstands, woodland, trees)
from OpenStreetMap and write a small normalised GeoJSON fixture.

Sources, tried in order (all free, no account or key):
  1. Overpass API mirrors (with retries)
  2. OSM main API /api/0.6/map, tiled into small bboxes, plus
     /relation/<id>/full for multipolygons

Output features carry:
  kind           building | grandstand | wood | tree_row | tree
  height_m       from `height`, else `building:levels` x 3 m, else a default
  height_source  height | levels | default
  osm_type, osm_id, name

The committed output (data/obstructions/red_bull_ring_osm.geojson) is what
`make verify` and the viewer export use; this script only needs re-running to
refresh it.

Data (c) OpenStreetMap contributors, ODbL.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping
from shapely.ops import linemerge, polygonize, unary_union

PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_OUT = PROJECT_DIR / "data" / "obstructions" / "red_bull_ring_osm.geojson"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_API = "https://api.openstreetmap.org/api/0.6"
USER_AGENT = "TrackLab/1.0 (track-lab spike; one-off obstruction fetch)"

DEFAULT_HEIGHTS = {"building": 8.0, "grandstand": 12.0, "wood": 15.0,
                   "tree_row": 15.0, "tree": 15.0}
LEVEL_HEIGHT_M = 3.0
TREE_ROW_HALF_WIDTH_M = 3.0
TREE_RADIUS_M = 4.0


def http(url: str, data: bytes = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Raw OSM element store (shared by both sources)
# ---------------------------------------------------------------------------

class OsmStore:
    def __init__(self):
        self.nodes = {}       # id -> (lon, lat, tags)
        self.ways = {}        # id -> (node_ids, tags)
        self.relations = {}   # id -> (members[(type, ref, role)], tags)

    def add_overpass_json(self, payload: dict):
        for el in payload.get("elements", []):
            t, i, tags = el["type"], el["id"], el.get("tags", {})
            if t == "node":
                self.nodes[i] = (el["lon"], el["lat"], tags)
            elif t == "way":
                self.ways[i] = (el["nodes"], tags)
            elif t == "relation":
                self.relations[i] = ([(m["type"], m["ref"], m.get("role", ""))
                                      for m in el["members"]], tags)

    def add_osm_xml(self, xml_bytes: bytes):
        root = ET.fromstring(xml_bytes)
        for el in root:
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            i = int(el.get("id", 0))
            if el.tag == "node":
                self.nodes[i] = (float(el.get("lon")), float(el.get("lat")), tags)
            elif el.tag == "way":
                self.ways[i] = ([int(n.get("ref")) for n in el.findall("nd")], tags)
            elif el.tag == "relation":
                self.relations[i] = ([(m.get("type"), int(m.get("ref")), m.get("role", ""))
                                      for m in el.findall("member")], tags)

    def way_coords(self, way_id):
        if way_id not in self.ways:
            return None
        ids = self.ways[way_id][0]
        if any(n not in self.nodes for n in ids):
            return None
        return [(self.nodes[n][0], self.nodes[n][1]) for n in ids]


def overpass_query(bbox) -> str:
    w, s, e, n = bbox
    b = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:90];
(
  way["building"]{b};
  relation["building"]["type"="multipolygon"]{b};
  way["landuse"="forest"]{b};
  relation["landuse"="forest"]["type"="multipolygon"]{b};
  way["natural"="wood"]{b};
  relation["natural"="wood"]["type"="multipolygon"]{b};
  way["natural"="tree_row"]{b};
  node["natural"="tree"]{b};
);
(._;>;);
out body qt;"""


def fetch_overpass(bbox, retries: int) -> OsmStore:
    query = overpass_query(bbox).encode()
    body = urllib.parse.urlencode({"data": query}).encode()
    for url in OVERPASS_MIRRORS:
        for attempt in range(retries):
            try:
                print(f"  Overpass {url} (attempt {attempt + 1})...")
                payload = json.loads(http(url, body, timeout=120))
                store = OsmStore()
                store.add_overpass_json(payload)
                print(f"    ok: {len(store.ways)} ways, {len(store.relations)} relations")
                return store, url
            except Exception as exc:  # network errors, 429/504, bad JSON
                print(f"    failed: {exc}")
                time.sleep(2 ** attempt)
    return None, None


def fetch_osm_api(bbox, tile_deg: float = 0.01) -> OsmStore:
    w, s, e, n = bbox
    store = OsmStore()
    lon = w
    while lon < e:
        lat = s
        while lat < n:
            tb = (lon, lat, min(lon + tile_deg, e), min(lat + tile_deg, n))
            url = f"{OSM_API}/map?bbox={tb[0]:.6f},{tb[1]:.6f},{tb[2]:.6f},{tb[3]:.6f}"
            for attempt in range(4):
                try:
                    store.add_osm_xml(http(url, timeout=120))
                    break
                except Exception as exc:
                    print(f"    tile {tb} failed ({exc}), retrying")
                    time.sleep(2 ** (attempt + 2))
            else:
                raise RuntimeError(f"OSM API tile {tb} failed after retries")
            lat += tile_deg
        lon += tile_deg
    print(f"  OSM API tiles: {len(store.ways)} ways, {len(store.relations)} relations")

    for rid, (_, tags) in list(store.relations.items()):
        if tags.get("type") == "multipolygon" and classify(tags):
            store.add_osm_xml(http(f"{OSM_API}/relation/{rid}/full", timeout=120))
    return store


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def classify(tags: dict):
    if tags.get("building") == "grandstand":
        return "grandstand"
    if "building" in tags and tags["building"] != "no":
        return "building"
    if tags.get("landuse") == "forest" or tags.get("natural") == "wood":
        return "wood"
    if tags.get("natural") == "tree_row":
        return "tree_row"
    if tags.get("natural") == "tree":
        return "tree"
    return None


def parse_metres(value):
    if value is None:
        return None
    try:
        return float(str(value).lower().replace("m", "").replace(",", ".").strip())
    except ValueError:
        return None


def height_for(kind: str, tags: dict):
    h = parse_metres(tags.get("height"))
    if h and h > 0:
        return h, "height"
    levels = parse_metres(tags.get("building:levels"))
    if kind in ("building", "grandstand") and levels and levels > 0:
        return levels * LEVEL_HEIGHT_M, "levels"
    return DEFAULT_HEIGHTS[kind], "default"


def assemble_multipolygon(store: OsmStore, members):
    outers, inners = [], []
    for mtype, ref, role in members:
        if mtype != "way":
            continue
        coords = store.way_coords(ref)
        if not coords or len(coords) < 2:
            continue
        (inners if role == "inner" else outers).append(LineString(coords))

    def rings(lines):
        if not lines:
            return None
        return unary_union(list(polygonize(linemerge(lines))))

    outer = rings(outers)
    if outer is None or outer.is_empty:
        return None
    inner = rings(inners)
    return outer.difference(inner) if inner is not None and not inner.is_empty else outer


def local_metres_per_deg(lat):
    import math
    return 111320.0 * math.cos(math.radians(lat)), 111320.0


def buffer_metres(geom, metres, lat):
    """Buffer a lon/lat geometry by a distance in metres (local equirectangular)."""
    from shapely import affinity
    sx, sy = local_metres_per_deg(lat)
    g = affinity.scale(geom, xfact=sx, yfact=sy, origin=(0, 0))
    g = g.buffer(metres, quad_segs=4, cap_style="flat" if isinstance(geom, LineString) else "round")
    return affinity.scale(g, xfact=1 / sx, yfact=1 / sy, origin=(0, 0))


def round_geom(geom, ndigits=7):
    def r(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], ndigits), round(c[1], ndigits)]
        return [r(x) for x in c]
    m = mapping(geom)
    return {"type": m["type"], "coordinates": r(m["coordinates"])}


def normalise(store: OsmStore, bbox):
    clip = box(*bbox)
    mid_lat = (bbox[1] + bbox[3]) / 2
    features = []

    def emit(geom, kind, tags, osm_type, osm_id):
        if geom is None or geom.is_empty:
            return
        if not geom.is_valid:
            geom = geom.buffer(0)
        geom = geom.intersection(clip)
        if geom.is_empty:
            return
        if isinstance(geom, (Polygon, MultiPolygon)) and geom.area < 1e-11:
            return
        h, src = height_for(kind, tags)
        props = {"kind": kind, "height_m": round(h, 2), "height_source": src,
                 "osm_type": osm_type, "osm_id": osm_id}
        if tags.get("name"):
            props["name"] = tags["name"]
        features.append({"type": "Feature", "properties": props,
                         "geometry": round_geom(geom)})

    for wid, (nids, tags) in sorted(store.ways.items()):
        kind = classify(tags)
        if not kind or kind == "tree":
            continue
        coords = store.way_coords(wid)
        if not coords:
            continue
        if kind == "tree_row":
            if len(coords) >= 2:
                emit(buffer_metres(LineString(coords), TREE_ROW_HALF_WIDTH_M, mid_lat),
                     kind, tags, "way", wid)
            continue
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue
        emit(Polygon(coords), kind, tags, "way", wid)

    for rid, (members, tags) in sorted(store.relations.items()):
        kind = classify(tags)
        if not kind or tags.get("type") != "multipolygon":
            continue
        emit(assemble_multipolygon(store, members), kind, tags, "relation", rid)

    for nid, (lon, lat, tags) in sorted(store.nodes.items()):
        if classify(tags) == "tree" and clip.contains(Point(lon, lat)):
            emit(buffer_metres(Point(lon, lat), TREE_RADIUS_M, mid_lat), "tree", tags, "node", nid)

    return features


def centreline_bbox(path: Path, margin_deg: float):
    with open(path) as f:
        gj = json.load(f)
    coords = gj["features"][0]["geometry"]["coordinates"]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (round(min(lons) - margin_deg, 4), round(min(lats) - margin_deg, 4),
            round(max(lons) + margin_deg, 4), round(max(lats) + margin_deg, 4))


def main():
    parser = argparse.ArgumentParser(description="Fetch OSM obstructions around a track")
    parser.add_argument("centreline", nargs="?",
                        default=str(PROJECT_DIR / "data" / "centrelines" / "red_bull_ring.geojson"))
    parser.add_argument("--margin", type=float, default=0.008,
                        help="Margin around the track bbox in degrees (default 0.008)")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUT))
    parser.add_argument("--retries", type=int, default=2, help="Attempts per Overpass mirror")
    parser.add_argument("--skip-overpass", action="store_true")
    args = parser.parse_args()

    bbox = centreline_bbox(Path(args.centreline), args.margin)
    print(f"Fetching OSM obstructions for bbox {bbox}")

    store, source = (None, None) if args.skip_overpass else fetch_overpass(bbox, args.retries)
    if store is None:
        print("  Overpass unavailable, falling back to the OSM main API")
        store, source = fetch_osm_api(bbox), OSM_API

    features = normalise(store, bbox)
    counts = {}
    for f in features:
        counts[f["properties"]["kind"]] = counts.get(f["properties"]["kind"], 0) + 1

    out = {
        "type": "FeatureCollection",
        "metadata": {
            "bbox": list(bbox),
            "source": source,
            "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attribution": "© OpenStreetMap contributors, ODbL 1.0",
            "default_heights_m": DEFAULT_HEIGHTS,
            "level_height_m": LEVEL_HEIGHT_M,
            "counts": counts,
        },
        "features": features,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    size_kb = Path(args.output).stat().st_size / 1024
    print(f"Wrote {len(features)} features {counts} to {args.output} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
