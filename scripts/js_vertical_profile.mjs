// Run viewer/vertical_profile.js under Node.
//   node scripts/js_vertical_profile.mjs fit          (JSON on stdin)
//   node scripts/js_vertical_profile.mjs layouts

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeLayout, makeDemSampler } from '../viewer/layout_math.js';
import {
    bindingReport,
    buildVerticalProfile,
    fitProfile,
    geometrySummary,
    violationsAreReported,
} from '../viewer/vertical_profile.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const mode = process.argv[2];

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

function demSampler() {
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
    return { siteDir, sampleZ };
}

if (mode === 'fit') {
    const body = JSON.parse(readFileSync(0, 'utf8'));
    const design = fitProfile(body.terrain, body.spacing, {
        maxGrade: body.maxGrade,
        maxCutFillM: body.maxCutFillM,
        minCrestRadiusM: body.minCrestRadiusM,
        minDipRadiusM: body.minDipRadiusM,
    });
    const summary = geometrySummary(design, body.spacing);
    const runs = bindingReport(body.terrain, design, body.spacing, {
        maxGrade: body.maxGrade,
        maxCutFillM: body.maxCutFillM ?? 6,
        minCrestRadiusM: body.minCrestRadiusM ?? 1000,
        minDipRadiusM: body.minDipRadiusM ?? 600,
    });
    const gaps = violationsAreReported(body.terrain, design, body.spacing, runs, {
        maxGrade: body.maxGrade,
        maxCutFillM: body.maxCutFillM ?? 6,
        minCrestRadiusM: body.minCrestRadiusM ?? 1000,
        minDipRadiusM: body.minDipRadiusM ?? 600,
    });
    console.log(JSON.stringify({ design, summary, runs, gaps }));
} else if (mode === 'layouts') {
    const { siteDir, sampleZ } = demSampler();
    const names = [
        ['layout_a_straight_hairpin', join(siteDir, 'layouts/layout_a_straight_hairpin.json')],
        ['layout_b_elevation_sweep', join(siteDir, 'layouts/layout_b_elevation_sweep.json')],
        ['layout_c_arena', join(siteDir, 'layouts/layout_c_arena.json')],
        ['version-1', join(siteDir, 'layouts/edits/version-1.json')],
        ['version-2', join(root, 'scripts/fixtures/version-2.json')],
    ];
    const layouts = {};
    for (const [name, path] of names) {
        const layout = readJson(path);
        const metrics = analyzeLayout(layout.control_points, sampleZ);
        const points = metrics.points;
        const profile = buildVerticalProfile(
            points.map((p) => p.x),
            points.map((p) => p.y),
            points.map((p) => p.distance),
            points.map((p) => p.z),
            metrics.lapLengthM,
            layout.track_width_m || 14,
            sampleZ,
            { maxGrade: 0.12 },
        );
        layouts[name] = {
            lap_m: profile.lap_m,
            spacing_m: profile.spacing_m,
            sample_count: profile.sample_count,
            design_z: profile.design_z,
            terrain_z: profile.terrain_z,
            design: profile.design,
            terrain: profile.terrain,
            earthworks: profile.earthworks,
            feasible: profile.feasible,
            join_step_m: profile.join_step_m,
            bindings: profile.bindings.map((r) => ({
                kind: r.kind,
                status: r.status,
                start_m: r.start_m,
                end_m: r.end_m,
                wraps: r.wraps,
                peak: r.peak,
                peak_at_m: r.peak_at_m,
                samples: r.samples,
            })),
        };
    }
    console.log(JSON.stringify({ layouts }));
} else {
    console.error('usage: js_vertical_profile.mjs fit|layouts');
    process.exit(2);
}
