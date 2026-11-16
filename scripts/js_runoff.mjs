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
    if (spec.kind === 'plane') {
        const z0 = Number.isFinite(spec.z0) ? spec.z0 : 0;
        const gx = Number.isFinite(spec.gx) ? spec.gx : 0;
        const gy = Number.isFinite(spec.gy) ? spec.gy : 0;
        return (x, y) => z0 + gx * x + gy * y;
    }
    if (spec.kind === 'dem') return sampleZ;
    if (spec.kind === 'zones') return zoneGround(spec);
    throw new Error('unknown ground ' + spec.kind);
}

function pointInPolygon(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const xi = poly[i][0];
        const yi = poly[i][1];
        const xj = poly[j][0];
        const yj = poly[j][1];
        const hit = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi);
        if (hit) inside = !inside;
    }
    return inside;
}

function zoneGround(spec) {
    const distances = spec.distances;
    const designZ = spec.designZ;
    const lapM = spec.lapM;
    const zones = spec.zones || [];
    const turnInOnly = spec.mode === 'turn-in';
    const interp = (distanceM) => {
        const n = distances.length;
        let d = distanceM % lapM;
        if (d < 0) d += lapM;
        let lo = 0;
        let hi = n - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (distances[mid] <= d) lo = mid;
            else hi = mid - 1;
        }
        const i = lo;
        const d0 = distances[i];
        const d1 = i + 1 >= n ? lapM : distances[i + 1];
        const v1 = i + 1 >= n ? designZ[0] : designZ[i + 1];
        const span = d1 - d0;
        let t = 0;
        if (span > 1e-9) {
            t = (d - d0) / span;
            if (t < 0) t = 0;
            else if (t > 1) t = 1;
        }
        return designZ[i] + t * (v1 - designZ[i]);
    };
    return (x, y) => {
        for (const zone of zones) {
            const poly = zone.polygon;
            if (!pointInPolygon(x, y, poly)) continue;
            if (turnInOnly) return interp(zone.turnInM);
            const inner = zone.inner || [];
            let bestS = zone.turnInM;
            let bestD = Infinity;
            for (let i = 0; i < inner.length - 1; i++) {
                const ax = inner[i][0];
                const ay = inner[i][1];
                const sa = inner[i][2];
                const bx = inner[i + 1][0];
                const by = inner[i + 1][1];
                const sb = inner[i + 1][2];
                const dx = bx - ax;
                const dy = by - ay;
                const len2 = dx * dx + dy * dy;
                let t = 0;
                if (len2 >= 1e-12) {
                    t = ((x - ax) * dx + (y - ay) * dy) / len2;
                    if (t < 0) t = 0;
                    else if (t > 1) t = 1;
                }
                const qx = ax + dx * t;
                const qy = ay + dy * t;
                const dist = (x - qx) * (x - qx) + (y - qy) * (y - qy);
                if (dist < bestD) {
                    bestD = dist;
                    let span = lapM > 0 ? (sb - sa) % lapM : sb - sa;
                    if (span < 0) span += lapM;
                    let s = sa + t * span;
                    if (lapM > 0) {
                        s %= lapM;
                        if (s < 0) s += lapM;
                    }
                    bestS = s;
                }
            }
            return interp(bestS);
        }
        return 100;
    };
}

function speedTrace(x, y, z, distances, lapM, trackWidthM) {
    const points = x.map((vx, i) => ({
        x: vx,
        y: y[i],
        z: z[i],
        distance: distances[i],
    }));
    // The viewer rides the designed centreline z(s). The deck is flat, so the
    // racing line is at that height rather than a sample off the centreline.
    const profile = buildDriveProfile(points, {
        lapLengthM: lapM,
        trackWidthM,
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
