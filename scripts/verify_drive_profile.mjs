// Node check of the quasi-static racing line and speed profile.
//   node scripts/verify_drive_profile.mjs
//
// Layout A, the committed edit version-1, and the fixture
// scripts/fixtures/version-2.json (not a saved edit). Also a one-point
// unsaved nudge of layout A. Prints lap-time estimate, speed range, and
// corner counts. Fails if a tight corner is not slower than the longest
// straight, if the accel/brake passes exceed their limits by more than 1%,
// or if the racing line leaves the track edges.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeLayout, makeDemSampler } from '../viewer/layout_math.js';
import {
    assessDriveProfile,
    buildDriveProfile,
    formatLapEstimate,
    stepCriticallyDamped,
} from '../viewer/drive_profile.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const siteDir = join(root, 'data/sites/nishiwaki_basin');

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

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

function runCase(name, layout) {
    const metrics = analyzeLayout(layout.control_points, sampleZ);
    const profile = buildDriveProfile(metrics.points, {
        lapLengthM: metrics.lapLengthM,
        trackWidthM: layout.track_width_m,
        sampleZ,
    });
    const report = assessDriveProfile(profile, metrics.corners);
    const minKmh = profile.minSpeedMps * 3.6;
    const maxKmh = profile.maxSpeedMps * 3.6;
    console.log(
        `${name}: lap estimate ${formatLapEstimate(profile.lapTimeS)} `
        + `(${profile.lapTimeS.toFixed(1)} s), `
        + `speed ${minKmh.toFixed(1)}–${maxKmh.toFixed(1)} km/h, `
        + `${report.numCorners} corners (${report.tightCorners} with R < 100 m), `
        + `longest straight ${report.straight.lengthM.toFixed(0)} m `
        + `peak ${(report.straight.peakMps * 3.6).toFixed(1)} km/h, `
        + `max offset ${profile.maxAbsOffsetM.toFixed(2)} / ${profile.limitM.toFixed(2)} m usable `
        + `(${profile.car.marginM.toFixed(2)} m margin inside the edges), `
        + `worst accel overshoot ${(report.worstAccelFrac * 100).toFixed(3)}%`,
    );
    if (profile.maxAbsOffsetM < 1.5) {
        report.ok = false;
        report.failures.push(`racing line barely leaves the centreline (max offset ${profile.maxAbsOffsetM.toFixed(2)} m)`);
    }
    if (!report.ok) {
        for (const failure of report.failures) console.log('  FAIL ' + failure);
    }
    return report.ok;
}

const cases = [
    ['layout A', readJson(join(siteDir, 'layouts/layout_a_straight_hairpin.json'))],
    ['layout B', readJson(join(siteDir, 'layouts/layout_b_elevation_sweep.json'))],
    ['layout C', readJson(join(siteDir, 'layouts/layout_c_arena.json'))],
    ['version-1', readJson(join(siteDir, 'layouts/edits/version-1.json'))],
    ['version-2', readJson(join(root, 'scripts/fixtures/version-2.json'))],
];

let ok = true;
for (const [name, layout] of cases) {
    if (!runCase(name, layout)) ok = false;
}

const nudged = readJson(join(siteDir, 'layouts/layout_a_straight_hairpin.json'));
nudged.control_points = nudged.control_points.map(p => ({ ...p }));
nudged.control_points[4].x += 40;
nudged.control_points[4].y -= 25;
const unsaved = analyzeLayout(nudged.control_points, sampleZ);
const unsavedProfile = buildDriveProfile(unsaved.points, {
    lapLengthM: unsaved.lapLengthM,
    trackWidthM: nudged.track_width_m,
    sampleZ,
});
const unsavedReport = assessDriveProfile(unsavedProfile, unsaved.corners);
if (unsavedProfile.maxAbsOffsetM < 1.5) {
    unsavedReport.ok = false;
    unsavedReport.failures.push('unsaved edit racing line did not use the width');
}
console.log(
    `layout A unsaved nudge: lap estimate ${formatLapEstimate(unsavedProfile.lapTimeS)}, `
    + `speed ${(unsavedProfile.minSpeedMps * 3.6).toFixed(1)}–${(unsavedProfile.maxSpeedMps * 3.6).toFixed(1)} km/h, `
    + `${unsavedReport.numCorners} corners, `
    + `max offset ${unsavedProfile.maxAbsOffsetM.toFixed(2)} / ${unsavedProfile.limitM.toFixed(2)} m usable, `
    + `accel overshoot ${(unsavedReport.worstAccelFrac * 100).toFixed(3)}%`,
);
if (!unsavedReport.ok) {
    ok = false;
    for (const failure of unsavedReport.failures) console.log('  FAIL ' + failure);
}

const snapped = stepCriticallyDamped(0, 0, Math.PI, 0, 5, 0.55);
const stepped = stepCriticallyDamped(0, 0, 1.2, 1 / 60, 5, 0.55);
if (snapped.rate !== 0 || Math.abs(stepped.rate) > 0.55 + 1e-9 || stepped.rate <= 0) {
    ok = false;
    console.log('FAIL critically damped look step', snapped, stepped);
} else {
    console.log(
        `look smoother: snap rate ${snapped.rate}, `
        + `one frame rate ${stepped.rate.toFixed(3)} rad/s (cap 0.55)`,
    );
}

if (!ok) {
    console.error('drive profile check failed');
    process.exit(1);
}
console.log('drive profile check passed');
