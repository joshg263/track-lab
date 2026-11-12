#!/usr/bin/env python3
"""
Export Japan site data for the Three.js viewer.

Creates viewer-compatible files in data/sites/<slug>/:
- terrain.json + terrain.bin (heightmap)
- scene.json (frame, imagery, attribution)

The viewer loads these to display the terrain and imagery.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_DIR = Path(__file__).parent.parent.parent
SITES_DIR = PROJECT_DIR / "data" / "sites"


def export_site_viewer_data(slug: str) -> dict:
    """Export viewer data for a single Japan site."""
    site_dir = SITES_DIR / slug
    
    site_json = site_dir / "site.json"
    with open(site_json) as f:
        site = json.load(f)
    
    dem_path = site_dir / "dem.tif"
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        if src.nodata is not None:
            dem = np.where(dem == src.nodata, np.nan, dem)
    
    dem = np.nan_to_num(dem, nan=float(np.nanmean(dem)))
    
    rows, cols = dem.shape
    left = transform.c
    top = transform.f
    res_x = transform.a
    res_y = -transform.e
    right = left + res_x * cols
    bottom = top - res_y * rows
    
    terrain_meta = {
        "type": "heightmap",
        "file": "terrain.bin",
        "left": left,
        "top": top,
        "res_x": res_x,
        "res_y": res_y,
        "rows": rows,
        "cols": cols,
        "bbox": {"min_lon": left, "min_lat": bottom, "max_lon": right, "max_lat": top},
        "min_elev": round(float(np.min(dem)), 1),
        "max_elev": round(float(np.max(dem)), 1),
    }
    
    dem.astype("<f4").tofile(site_dir / "terrain.bin")
    with open(site_dir / "terrain.json", "w") as f:
        json.dump(terrain_meta, f, indent=2)
    
    imagery_json = site_dir / "imagery.json"
    with open(imagery_json) as f:
        imagery = json.load(f)
    
    lat = site["lat"]
    lon = site["lon"]
    
    scene = {
        "frame": {"lon0": lon, "lat0": lat},
        "imagery": {
            "file": "imagery.jpg",
            "bbox": imagery["bbox"],
            "attribution": imagery["attribution"],
        },
        "site": {
            "name": site["name"],
            "prefecture": site["prefecture"],
            "lat": lat,
            "lon": lon,
            "metrics": site.get("metrics", {}),
            "nearest_town": site.get("nearest_town", {}),
            "why_it_fits": site.get("why_it_fits", ""),
        },
        "best_viewpoint": site.get("metrics", {}).get("best_viewpoint", {
            "lat": lat,
            "lon": lon,
            "visibility_pct": 0,
            "elevation_m": terrain_meta["min_elev"] + 50,
        }),
        "attribution": site.get("attribution", []),
    }
    
    with open(site_dir / "scene.json", "w") as f:
        json.dump(scene, f, separators=(",", ":"))
    
    total = sum((site_dir / f).stat().st_size for f in 
                ["terrain.json", "terrain.bin", "scene.json", "imagery.jpg"])
    
    print(f"  {slug}: terrain {rows}x{cols}, total {total/1024:.0f} KB")
    
    return {"slug": slug, "terrain_meta": terrain_meta, "scene": scene}


def export_all_sites() -> list[str]:
    """Export viewer data for all Japan sites."""
    slugs = []
    
    for site_dir in sorted(SITES_DIR.iterdir()):
        if not site_dir.is_dir():
            continue
        if not (site_dir / "site.json").exists():
            continue
        
        try:
            export_site_viewer_data(site_dir.name)
            slugs.append(site_dir.name)
        except Exception as e:
            print(f"  ERROR exporting {site_dir.name}: {e}")
    
    return slugs


def create_sites_index() -> Path:
    """Create an index of all available sites for the viewer."""
    sites = []
    
    for site_dir in sorted(SITES_DIR.iterdir()):
        if not site_dir.is_dir():
            continue
        
        scene_json = site_dir / "scene.json"
        if not scene_json.exists():
            continue
        
        with open(scene_json) as f:
            scene = json.load(f)
        
        site_info = scene.get("site", {})
        sites.append({
            "slug": site_dir.name,
            "name": site_info.get("name", site_dir.name),
            "prefecture": site_info.get("prefecture", "Japan"),
            "lat": site_info.get("lat", 0),
            "lon": site_info.get("lon", 0),
            "metrics": site_info.get("metrics", {}),
            "best_viewpoint": scene.get("best_viewpoint", {}),
        })
    
    index_path = SITES_DIR / "index.json"
    with open(index_path, "w") as f:
        json.dump({"sites": sites}, f, indent=2)
    
    return index_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export Japan sites for viewer")
    parser.add_argument("--site", help="Export single site by slug")
    args = parser.parse_args()
    
    if args.site:
        export_site_viewer_data(args.site)
    else:
        print("Exporting viewer data for all Japan sites...")
        slugs = export_all_sites()
        print(f"\nExported {len(slugs)} sites")
        
        print("\nCreating sites index...")
        index_path = create_sites_index()
        print(f"Wrote {index_path}")
