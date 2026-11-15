// Run viewer/runoff.js under Node.
//   node scripts/js_runoff.mjs assess     JSON on stdin
//   node scripts/js_runoff.mjs speeds     JSON on stdin: centreline with design z
//   node scripts/js_runoff.mjs layouts    Nishiwaki A, version-1, version-2 fixture

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeDemSampler } from '../viewer/layout_math.js';
import { buildDriveProfile } from '../viewer/drive_profile.js';
import { assessRunoff } from '../viewer/runoff.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const mode = process.argv[2];

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

function readStdin() {
    return JSON.parse(readFileSync(0, 'utf8'));
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

function groundFromSpec(spec, sampleZ) {
    if (!spec || spec.kind === 'constant') {
        const z = spec && Number.isFinite(spec.z) ? spec.z : 0;
        return () => z;
    }
    if (spec.kind === 'step') {
        const high = spec.z;
        const low = spec.low;
        const cut = spec.cut;
        const axis = spec.axis === 'y' ? 'y' : 'x';
        return (x, y) => ((axis === 'x' ? x : y) > cut ? low : high);
    }
    if (spec.kind === 'dem') return sampleZ;
    throw new Error('unknown ground ' + spec.kind);
}

// Nearest centreline design elevation. Matches the viewer's deckElevation.
function deckElevation(points, x, y) {
    const n = points.length;
    let best = Infinity;
    let bestZ = points[0].z;
    for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const ax = points[i].x;
        const ay = points[i].y;
        const dx = points[j].x - ax;
        const dy = points[j].y - ay;
        const len2 = dx * dx + dy * dy;
        let t = len2 > 1e-8 ? ((x - ax) * dx + (y - ay) * dy) / len2 : 0;
        if (t < 0) t = 0;
        else if (t > 1) t = 1;
        const px = ax + dx * t;
        const py = ay + dy * t;
        const d2 = (x - px) * (x - px) + (y - py) * (y - py);
        if (d2 < best) {
            best = d2;
            bestZ = points[i].z + (points[j].z - points[i].z) * t;
        }
    }
    return bestZ;
}

function speedTrace(x, y, z, distances, lapM, trackWidthM) {
    const points = x.map((vx, i) => ({
        x: vx,
        y: y[i],
        z: z[i],
        distance: distances[i],
    }));
    const profile = buildDriveProfile(points, {
        lapLengthM: lapM,
        trackWidthM,
        sampleZ: (px, py) => deckElevation(points, px, py),
    });
    if (!profile) return [];
    return profile.samples.map((s) => ({ distance: s.distance, speed_mps: s.speedMps }));
}

function runAssess(body, sampleZ) {
    const ground = groundFromSpec(body.ground, sampleZ);
    return assessRunoff(
        body.x,
        body.y,
        body.distances,
        body.designZ,
        body.lapM,
        ground,
        {
            trackWidthM: body.trackWidthM,
            speedTrace: body.speedTrace || null,
            decelG: body.decelG,
            widthM: body.widthM,
            slopeLimit: body.slopeLimit,
            stepM: body.stepM,
            overlapGapM: body.overlapGapM,
            closed: body.closed !== false,
        },
    );
}

if (mode === 'assess') {
    const body = readStdin();
    const sampleZ = body.ground && body.ground.kind === 'dem' ? demSampler().sampleZ : null;
    console.log(JSON.stringify(runAssess(body, sampleZ)));
} else if (mode === 'speeds') {
    const body = readStdin();
    const trace = speedTrace(body.x, body.y, body.z, body.distances, body.lapM, body.trackWidthM || 14);
    console.log(JSON.stringify({ trace }));
} else if (mode === 'layouts') {
    const { siteDir, sampleZ } = demSampler();
    const names = [
        ['layout_a_straight_hairpin', join(siteDir, 'layouts/layout_a_straight_hairpin.json')],
        ['version-1', join(siteDir, 'layouts/edits/version-1.json')],
        ['version-2', join(root, 'scripts/fixtures/version-2.json')],
    ];
    const body = readStdin();
    const layouts = {};
    for (const [name] of names) {
        const spec = body[name];
        const trace = speedTrace(spec.x, spec.y, spec.designZ, spec.distances, spec.lapM, spec.trackWidthM);
        const report = runAssess({ ...spec, speedTrace: trace, ground: { kind: 'dem' } }, sampleZ);
        layouts[name] = { trace, report };
    }
    console.log(JSON.stringify({ layouts }));
} else {
    console.error('usage: js_runoff.mjs assess|speeds|layouts');
    process.exit(2);
}
