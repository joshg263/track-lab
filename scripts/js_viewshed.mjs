// Runs viewer/viewshed.js under Node on exported viewer data and prints the
// preset visible percentages as JSON (used by scripts/verify.py).
// Usage: node scripts/js_viewshed.mjs <viewer-data-dir>

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Terrain, Obstructions, computeViewshed, LOS_SAMPLES, TARGET_HEIGHT_M, EYE_ABOVE_TIER_M }
    from '../viewer/viewshed.js';

const dir = process.argv[2];
const read = (f) => readFileSync(join(dir, f));
const terrainMeta = JSON.parse(read('terrain.json'));
const tb = read(terrainMeta.file);
const terrain = new Terrain(terrainMeta, new Float32Array(tb.buffer, tb.byteOffset, tb.byteLength / 4));
const scene = JSON.parse(read('scene.json'));
const ob = read(scene.obstructions.file);
const obstructions = new Obstructions(scene.obstructions, new Uint8Array(ob.buffer, ob.byteOffset, ob.byteLength));
const track = JSON.parse(read('track.json'));
const coords = track.points.map(p => [p.lon, p.lat]);

const seats = track.preset_seats.map(s => ({
    id: s.id,
    visible_pct: computeViewshed(terrain, obstructions, coords, s.lon, s.lat, s.eye_height_m).visible_pct,
    visible_pct_bare: computeViewshed(terrain, null, coords, s.lon, s.lat, s.eye_height_m).visible_pct,
}));

console.log(JSON.stringify({
    params: { los_samples: LOS_SAMPLES, target_height_m: TARGET_HEIGHT_M, eye_above_tier_m: EYE_ABOVE_TIER_M },
    seats,
}));
