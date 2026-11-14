// Run viewer/layout_math.js under Node.
//   node scripts/js_layout_math.mjs layouts
//   node scripts/js_layout_math.mjs profile <profile.json>
//   node scripts/js_layout_math.mjs gaussian   (JSON on stdin: {values, sigma})

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
    analyzeLayout,
    analyzeOpenLonLat,
    gaussianFilter1d,
    makeDemSampler,
} from '../viewer/layout_math.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const mode = process.argv[2];

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

if (mode === 'gaussian') {
    const raw = readFileSync(0, 'utf8');
    const body = JSON.parse(raw);
    const values = gaussianFilter1d(body.values, body.sigma);
    console.log(JSON.stringify({ values }));
} else if (mode === 'layouts') {
    const siteDir = join(root, 'data/sites/nishiwaki_basin');
    const terrainMeta = readJson(join(siteDir, 'terrain.json'));
    const scene = readJson(join(siteDir, 'scene.json'));
    const bin = readFileSync(join(siteDir, terrainMeta.file));
    const heights = new Float32Array(bin.buffer, bin.byteOffset, bin.byteLength / 4);
    const lat0 = scene.frame.lat0;
    const frame = {
        lon0: scene.frame.lon0,
        lat0,
        sx: 111320 * Math.cos(lat0 * Math.PI / 180),
        sy: 111320,
    };
    const sampleZ = makeDemSampler({
        heights,
        rows: terrainMeta.rows,
        cols: terrainMeta.cols,
        left: terrainMeta.left,
        top: terrainMeta.top,
        resX: terrainMeta.res_x,
        resY: terrainMeta.res_y,
    }, frame);
    const names = [
        'layout_a_straight_hairpin',
        'layout_b_elevation_sweep',
        'layout_c_arena',
    ];
    const layouts = {};
    for (const name of names) {
        const layout = readJson(join(siteDir, 'layouts', `${name}.json`));
        const metrics = analyzeLayout(layout.control_points, sampleZ);
        layouts[name] = {
            lapLengthM: metrics.lapLengthM,
            numCorners: metrics.numCorners,
            minRadiusM: metrics.minRadiusM,
            maxGradientPct: metrics.maxGradientPct,
            corners: metrics.corners,
            sampleSpacingM: metrics.sampleSpacingM,
            pointCount: metrics.points.length,
        };
    }
    console.log(JSON.stringify({ layouts }));
} else if (mode === 'profile') {
    const profile = readJson(process.argv[3]);
    const metrics = analyzeOpenLonLat(profile.lons, profile.lats, profile.distances, profile.elevations);
    console.log(JSON.stringify(metrics));
} else {
    console.error('usage: js_layout_math.mjs layouts|profile <file>|gaussian');
    process.exit(2);
}
