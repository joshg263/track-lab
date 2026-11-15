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
    GRAVITY_MPS2,
    advanceDistance,
    assessDriveProfile,
    buildDriveProfile,
    driveState,
    formatLapEstimate,
    lookAheadMetres,
    sampleProfile,
    stepCriticallyDamped,
    stepOnboardLook,
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
    return { ok: report.ok, profile };
}

const cases = [
    ['layout A', readJson(join(siteDir, 'layouts/layout_a_straight_hairpin.json'))],
    ['layout B', readJson(join(siteDir, 'layouts/layout_b_elevation_sweep.json'))],
    ['layout C', readJson(join(siteDir, 'layouts/layout_c_arena.json'))],
    ['version-1', readJson(join(siteDir, 'layouts/edits/version-1.json'))],
    ['version-2', readJson(join(root, 'scripts/fixtures/version-2.json'))],
];

let ok = true;
const profiles = {};
for (const [name, layout] of cases) {
    const result = runCase(name, layout);
    profiles[name] = result.profile;
    if (!result.ok) ok = false;
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

const sinUp = 0.355 / Math.hypot(1, 0.355);
if (driveState(-0.56, sinUp) !== 'throttle') {
    ok = false;
    console.log('FAIL uphill net deceleration is still throttle');
}
if (driveState(0, -sinUp) !== 'brake') {
    ok = false;
    console.log('FAIL holding speed downhill should be brake');
}
if (driveState(0, 0) !== 'coast' || driveState(2, 0) !== 'throttle' || driveState(-2, 0) !== 'brake') {
    ok = false;
    console.log('FAIL flat-road drive state');
}
let uphillThrottle = 0;
for (const s of profiles['version-2'].samples) {
    const propulsion = s.longAccel + GRAVITY_MPS2 * s.sinTheta;
    const expect = propulsion > 0.3 ? 'throttle' : propulsion < -0.3 ? 'brake' : 'coast';
    if (s.state !== expect) {
        ok = false;
        console.log(`FAIL version-2 state ${s.state} at ${s.distance.toFixed(0)} m, want ${expect}`);
        break;
    }
    if (s.gradientPct > 12 && s.longAccel < -0.25 && s.state === 'throttle') uphillThrottle++;
}
console.log(`version-2 uphill throttle samples (net slowing, still throttle): ${uphillThrottle}`);
if (uphillThrottle < 1) {
    ok = false;
    console.log('FAIL expected an uphill where gravity slows the car while it stays on throttle');
}

function wrapAngle(angle) {
    let d = angle;
    while (d > Math.PI) d -= Math.PI * 2;
    while (d < -Math.PI) d += Math.PI * 2;
    return d;
}

function headingAt(profile, distance) {
    const here = sampleProfile(profile, distance);
    const nose = sampleProfile(profile, distance + 8);
    return Math.atan2(-(nose.x - here.x), (nose.y - here.y));
}

function lookTarget(profile, distance) {
    const here = sampleProfile(profile, distance);
    const ahead = sampleProfile(profile, distance + lookAheadMetres(here.speedMps, 1.5));
    const dx = ahead.x - here.x;
    const dy = ahead.y - here.y;
    const dz = ahead.z - here.z;
    return {
        heading: headingAt(profile, distance),
        yaw: Math.atan2(-dx, dy),
        pitch: Math.atan2(dz, Math.hypot(dx, dy) || 1),
    };
}

function maxLookLag(profile, playbackMul) {
    let distance = 0;
    let state = null;
    let maxLag = 0;
    let maxOffHeading = 0;
    let worstAt = 0;
    const dt = 1 / 60;
    const duration = profile.lapTimeS / playbackMul;
    for (let t = 0; t < duration; t += dt) {
        const target = lookTarget(profile, distance);
        state = stepOnboardLook(state, target, dt, playbackMul);
        const lag = Math.abs(wrapAngle(state.yaw - target.yaw));
        const offHeading = Math.abs(wrapAngle(state.yaw - target.heading));
        if (lag > maxLag) {
            maxLag = lag;
            worstAt = distance;
        }
        if (offHeading > maxOffHeading) maxOffHeading = offHeading;
        distance = advanceDistance(profile, distance, dt * playbackMul);
    }
    return { maxLag, maxOffHeading, worstAt };
}

const snapped = stepCriticallyDamped(0, 0, Math.PI, 0, 5, 0.55);
const stepped = stepCriticallyDamped(0, 0, 1.2, 1 / 60, 5, 0.55);
if (snapped.rate !== 0 || Math.abs(stepped.rate) > 0.55 + 1e-9 || stepped.rate <= 0) {
    ok = false;
    console.log('FAIL critically damped look step', snapped, stepped);
}

for (const [name, mul] of [['version-1', 1], ['version-1', 4]]) {
    const lag = maxLookLag(profiles[name], mul);
    const ahead = lag.maxLag * 180 / Math.PI;
    const nose = lag.maxOffHeading * 180 / Math.PI;
    console.log(
        `${name} onboard look at ${mul}×: ${nose.toFixed(1)}° off the car heading, `
        + `${ahead.toFixed(1)}° off the look-ahead, worst at ${lag.worstAt.toFixed(0)} m`,
    );
    if (!(nose < 40)) {
        ok = false;
        console.log(`FAIL camera leaves the car heading by ${nose.toFixed(1)}° at ${mul}×`);
    }
    if (!(ahead < 90)) {
        ok = false;
        console.log(`FAIL camera looks away from the road ahead by ${ahead.toFixed(1)}° at ${mul}×`);
    }
}

if (!ok) {
    console.error('drive profile check failed');
    process.exit(1);
}
console.log('drive profile check passed');
