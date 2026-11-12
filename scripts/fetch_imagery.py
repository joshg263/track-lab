#!/usr/bin/env python3
"""
Bake a Sentinel-2 cloudless texture for the viewer's terrain crop.

Source: EOX "Sentinel-2 cloudless" WMTS (https://s2maps.eu), 2016 layer,
CC BY 4.0 (attribution only, no key). Tiles are fetched once, stitched and
resampled onto a linear lon/lat grid so the viewer can map UVs directly
from longitude/latitude. The baked JPEG and its bounds are committed in
data/imagery/, so the viewer needs no imagery service at runtime.

Attribution: Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH
(Contains modified Copernicus Sentinel data 2016 & 2017)
"""

import argparse
import io
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from compute_viewshed import TERRAIN_BUFFER_DEG  # noqa: E402

PROJECT_DIR = Path(__file__).parent.parent
OUT_DIR = PROJECT_DIR / "data" / "imagery"
TILE_URL = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg"
ATTRIBUTION = ("Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH "
               "(Contains modified Copernicus Sentinel data 2016 & 2017), CC BY 4.0")


def lonlat_to_pixel(lon, lat, z):
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(z, x, y):
    url = TILE_URL.format(z=z, x=x, y=y)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TrackLab/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception as exc:
            print(f"  tile {z}/{x}/{y} failed ({exc}), retrying")
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}")


def main():
    parser = argparse.ArgumentParser(description="Bake S2 cloudless texture for the track crop")
    parser.add_argument("--analysis", default=str(PROJECT_DIR / "data" / "analysis.json"))
    parser.add_argument("--zoom", type=int, default=15)
    parser.add_argument("--metres-per-pixel", type=float, default=3.0)
    parser.add_argument("--quality", type=int, default=82)
    parser.add_argument("--name", default="red_bull_ring_s2cloudless")
    args = parser.parse_args()

    with open(args.analysis) as f:
        coords = json.load(f)["profile"]["coords"]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    w = round(min(lons) - TERRAIN_BUFFER_DEG, 4)
    e = round(max(lons) + TERRAIN_BUFFER_DEG, 4)
    s = round(min(lats) - TERRAIN_BUFFER_DEG, 4)
    n = round(max(lats) + TERRAIN_BUFFER_DEG, 4)
    z = args.zoom

    px0, py0 = lonlat_to_pixel(w, n, z)
    px1, py1 = lonlat_to_pixel(e, s, z)
    tx0, ty0 = int(px0 // 256), int(py0 // 256)
    tx1, ty1 = int(px1 // 256), int(py1 // 256)
    print(f"Fetching {(tx1 - tx0 + 1) * (ty1 - ty0 + 1)} tiles at z{z} for bbox {(w, s, e, n)}")

    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            mosaic.paste(fetch_tile(z, tx, ty), ((tx - tx0) * 256, (ty - ty0) * 256))
    src = np.asarray(mosaic, dtype=np.float32)

    mid_lat = (s + n) / 2
    width_m = (e - w) * 111320 * math.cos(math.radians(mid_lat))
    height_m = (n - s) * 111320
    out_w = int(round(width_m / args.metres_per_pixel))
    out_h = int(round(height_m / args.metres_per_pixel))

    lon = w + (np.arange(out_w) + 0.5) / out_w * (e - w)
    lat = n - (np.arange(out_h) + 0.5) / out_h * (n - s)
    sx, _ = lonlat_to_pixel(lon, mid_lat, z)
    _, sy = lonlat_to_pixel(lon[0], lat, z)
    sx = sx - tx0 * 256 - 0.5
    sy = sy - ty0 * 256 - 0.5
    x0 = np.clip(np.floor(sx).astype(int), 0, src.shape[1] - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, src.shape[0] - 2)
    fx = (sx - x0)[None, :, None]
    fy = (sy - y0)[:, None, None]
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    out = (src[Y0, X0] * (1 - fx) * (1 - fy) + src[Y0, X0 + 1] * fx * (1 - fy) +
           src[Y0 + 1, X0] * (1 - fx) * fy + src[Y0 + 1, X0 + 1] * fx * fy)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jpg = OUT_DIR / f"{args.name}.jpg"
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(jpg, quality=args.quality, optimize=True)
    meta = {"image": jpg.name, "bbox": [w, s, e, n], "width": out_w, "height": out_h,
            "zoom": z, "source": TILE_URL, "attribution": ATTRIBUTION}
    with open(OUT_DIR / f"{args.name}.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {jpg} ({out_w}x{out_h}, {jpg.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
