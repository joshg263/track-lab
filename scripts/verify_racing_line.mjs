// Headless check of the minimum-curvature racing line.
//   node scripts/verify_racing_line.mjs
//
// On layout A, version-1 and version-2: the line stays inside the usable
// width, real corners are outside then apex then outside, a corner onto a
// long straight apexes after its geometric midpoint, a linked pair gives the
// exit to the second corner, and flat-out kinks are straighter than the
// centreline. Lap estimates are rough and should be faster than the old
// shortest-path line.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeLayout, computeCurvatureXY, makeDemSampler } from '../viewer/layout_math.js';
import { buildDriveProfile, formatLapEstimate } from '../viewer/drive_profile.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const siteDir = join(root, 'data/sites/nishiwaki_basin');

// Shortest-path estimates from before this line, margin 0.6 m.
const PREVIOUS_LAP_S = {
    'layout A': 71.2,
    'version-1': 133.4,
    'version-2': 133.4,
};

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

function circ(a, b, lap) {
    const d = Math.abs(a - b);
    return Math.min(d, lap - d);
}

function wrapForward(from, to, lap) {
    let d = to - from;
    if (d < 0) d += lap;
    return d;
}

function nearestIndex(samples, lap, distance) {
    let best = 0;
    let bestD = Infinity;
    for (let i = 0; i < samples.length; i++) {
        const d = circ(samples[i].distance, distance, lap);
        if (d < bestD) {
            bestD = d;
            best = i;
        }
    }
    return best;
}

function signedCurvatureAt(points, index) {
    const n = points.length;
    const i = index;
    const im = (i - 1 + n) % n;
    const ip = (i + 1) % n;
    const v1x = points[i].x - points[im].x;
    const v1y = points[i].y - points[im].y;
    const v2x = points[ip].x - points[i].x;
    const v2y = points[ip].y - points[i].y;
    const phi = Math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y);
    const ds = 0.5 * (Math.hypot(v1x, v1y) + Math.hypot(v2x, v2y));
    return phi / Math.max(ds, 1e-4);
}

function sampleAt(samples, lap, distance) {
    return samples[nearestIndex(samples, lap, distance)];
}

// Inside-positive offset: positive means toward the inside of this corner.
function insideOffset(sample, turnSign) {
    return sample.offsetM * turnSign;
}

function fmt(n) {
    return (n >= 0 ? '+' : '') + n.toFixed(1);
}

const failures = [];

function check(name, layout) {
    const metrics = analyzeLayout(layout.control_points, sampleZ);
    const profile = buildDriveProfile(metrics.points, {
        lapLengthM: metrics.lapLengthM,
        trackWidthM: layout.track_width_m,
        sampleZ,
    });
    const samples = profile.samples;
    const lap = profile.lapLengthM;
    const limit = profile.limitM;
    const points = metrics.points;
    let widest = 0;
    for (const s of samples) {
        if (Math.abs(s.offsetM) > widest) widest = Math.abs(s.offsetM);
        if (Math.abs(s.offsetM) > limit + 1e-3) {
            failures.push(`${name} line leaves the usable width at ${s.distance.toFixed(0)} m (${s.offsetM.toFixed(2)} m, limit ±${limit.toFixed(2)})`);
            break;
        }
    }

    const byPosition = [...metrics.corners].sort((a, b) => a.position_m - b.position_m);
    const corners = byPosition;
    const tight = [...corners].sort((a, b) => a.radius_m - b.radius_m).slice(0, 3);
    console.log(
        `\n${name}: rough lap ${formatLapEstimate(profile.lapTimeS)} `
        + `(${profile.lapTimeS.toFixed(1)} s), was ${formatLapEstimate(PREVIOUS_LAP_S[name])} `
        + `(${PREVIOUS_LAP_S[name].toFixed(1)} s). `
        + `Max offset ${widest.toFixed(2)} / ${limit.toFixed(2)} m.`,
    );
    if (!(profile.lapTimeS < PREVIOUS_LAP_S[name] - 0.2)) {
        failures.push(`${name} lap ${profile.lapTimeS.toFixed(2)} s is not faster than ${PREVIOUS_LAP_S[name].toFixed(1)} s`);
    }

    for (const corner of tight) {
        const i0 = nearestIndex(samples, lap, corner.position_m);
        const turn = Math.sign(signedCurvatureAt(points, i0)) || 1;
        let apexOff = -Infinity;
        let apexAt = corner.position_m;
        for (let dm = -8; dm <= 50; dm += 2) {
            const s = sampleAt(samples, lap, corner.position_m + dm);
            const inside = insideOffset(s, turn);
            if (inside > apexOff) {
                apexOff = inside;
                apexAt = s.distance;
            }
        }
        let entry = null;
        for (let dm = -40; dm >= -200; dm -= 4) {
            const s = sampleAt(samples, lap, corner.position_m + dm);
            const inside = insideOffset(s, turn);
            if (!entry || inside < entry.inside) entry = { at: s.distance, inside, offset: s.offsetM };
        }
        let exit = null;
        for (let dm = 40; dm <= 200; dm += 4) {
            const s = sampleAt(samples, lap, corner.position_m + dm);
            const inside = insideOffset(s, turn);
            if (!exit || inside < exit.inside) exit = { at: s.distance, inside, offset: s.offsetM };
        }
        const ahead = corners
            .map(c => ({ c, gap: wrapForward(corner.position_m, c.position_m, lap) }))
            .filter(x => x.gap > 20)
            .sort((a, b) => a.gap - b.gap)[0];
        const behind = corners
            .map(c => ({ c, gap: wrapForward(c.position_m, corner.position_m, lap) }))
            .filter(x => x.gap > 20)
            .sort((a, b) => a.gap - b.gap)[0];
        const linkedExit = ahead && ahead.gap < 110;
        const linkedEntry = behind && behind.gap < 110;
        const entryText = entry && entry.inside <= -0.2 * limit
            ? `outside ${fmt(entry.offset)} m (${fmt(entry.inside)} inside) ${circ(entry.at, corner.position_m, lap).toFixed(0)} m before the corner`
            : `no separate outside entry (nearest offset ${entry ? fmt(entry.offset) : 'n/a'} m)`;
        const exitText = exit && exit.inside <= -0.2 * limit
            ? `outside ${fmt(exit.offset)} m (${fmt(exit.inside)} inside) ${circ(exit.at, corner.position_m, lap).toFixed(0)} m after the corner`
            : `no separate outside exit (nearest offset ${exit ? fmt(exit.offset) : 'n/a'} m)`;
        console.log(
            `  R${corner.radius_m.toFixed(0)} @${corner.position_m.toFixed(0)}: `
            + `${entryText}; apex ${fmt(sampleAt(samples, lap, apexAt).offsetM)} m `
            + `(${fmt(apexOff)} inside) at ${apexAt.toFixed(0)} m; ${exitText}`
            + (linkedEntry ? ` — previous corner ${behind.gap.toFixed(0)} m earlier, entry is shared` : '')
            + (linkedExit ? ` — next corner ${ahead.gap.toFixed(0)} m later, exit is shared` : ''),
        );
        if ((!entry || !(entry.inside <= -0.2 * limit)) && !linkedEntry) {
            failures.push(`${name} R${corner.radius_m.toFixed(0)} @${corner.position_m.toFixed(0)} is not outside before the apex`);
        }
        if (!(apexOff >= 0.5 * limit)) {
            failures.push(`${name} R${corner.radius_m.toFixed(0)} @${corner.position_m.toFixed(0)} apex is only ${apexOff.toFixed(1)} m inside`);
        }
        if ((!exit || !(exit.inside <= -0.2 * limit)) && !linkedExit) {
            failures.push(`${name} R${corner.radius_m.toFixed(0)} @${corner.position_m.toFixed(0)} is not outside after the apex`);
        }
    }

    const lateCandidates = corners
        .map((c, i) => {
            const next = corners[(i + 1) % corners.length];
            return { c, gap: wrapForward(c.position_m, next.position_m, lap) };
        })
        .filter(x => x.c.radius_m < 120 && x.gap >= 250)
        .sort((a, b) => b.gap - a.gap);
    if (lateCandidates.length) {
        const corner = lateCandidates[0].c;
        const i0 = nearestIndex(samples, lap, corner.position_m);
        const turn = Math.sign(signedCurvatureAt(points, i0)) || 1;
        let maxInside = -Infinity;
        for (let dm = -40; dm <= 140; dm += 2) {
            maxInside = Math.max(maxInside, insideOffset(sampleAt(samples, lap, corner.position_m + dm), turn));
        }
        let latest = corner.position_m;
        for (let dm = -40; dm <= 140; dm += 2) {
            const s = sampleAt(samples, lap, corner.position_m + dm);
            if (insideOffset(s, turn) >= maxInside - 0.35) latest = s.distance;
        }
        const past = wrapForward(corner.position_m, latest, lap);
        const pastSigned = past > lap / 2 ? past - lap : past;
        console.log(
            `  late apex on R${corner.radius_m.toFixed(0)} @${corner.position_m.toFixed(0)} `
            + `(${lateCandidates[0].gap.toFixed(0)} m straight after): `
            + `geometric midpoint ${corner.position_m.toFixed(0)} m, `
            + `racing apex ${latest.toFixed(0)} m, ${fmt(pastSigned)} m`,
        );
        if (!(pastSigned >= 8)) {
            failures.push(`${name} late apex is ${pastSigned.toFixed(1)} m past the geometric midpoint`);
        }
    } else {
        failures.push(`${name} has no corner leading onto a long straight`);
    }

    const pairCandidates = [];
    for (let i = 0; i < corners.length; i++) {
        const a = corners[i];
        const b = corners[(i + 1) % corners.length];
        const gap = wrapForward(a.position_m, b.position_m, lap);
        if (gap < 160 && a.radius_m <= 100 && b.radius_m <= 100) pairCandidates.push({ a, b, gap });
    }
    pairCandidates.sort((a, b) => a.gap - b.gap);
    let bestPair = null;
    let pairScore = null;
    for (const candidate of pairCandidates) {
        const { a, b, gap } = candidate;
        const turnA = Math.sign(signedCurvatureAt(points, nearestIndex(samples, lap, a.position_m))) || 1;
        const turnB = Math.sign(signedCurvatureAt(points, nearestIndex(samples, lap, b.position_m))) || 1;
        const junction = insideOffset(sampleAt(samples, lap, a.position_m + gap / 2), turnA);
        const apexA = insideOffset(sampleAt(samples, lap, a.position_m), turnA);
        let exitB = Infinity;
        for (let dm = 30; dm <= 180; dm += 4) {
            exitB = Math.min(exitB, insideOffset(sampleAt(samples, lap, b.position_m + dm), turnB));
        }
        const ok = apexA > 1.5 && exitB < -0.3 * limit && exitB < junction - 1;
        if (ok) {
            bestPair = candidate;
            pairScore = { junction, apexA, exitB };
            break;
        }
    }
    if (bestPair) {
        const { a, b, gap } = bestPair;
        const { junction, apexA, exitB } = pairScore;
        const turnA = Math.sign(signedCurvatureAt(points, nearestIndex(samples, lap, a.position_m))) || 1;
        const turnB = Math.sign(signedCurvatureAt(points, nearestIndex(samples, lap, b.position_m))) || 1;
        const midDist = a.position_m + gap / 2;
        let exitBAt = b.position_m;
        let exitBOffset = 0;
        let exitSeen = Infinity;
        for (let dm = 30; dm <= 180; dm += 4) {
            const s = sampleAt(samples, lap, b.position_m + dm);
            const inside = insideOffset(s, turnB);
            if (inside < exitSeen) {
                exitSeen = inside;
                exitBAt = s.distance;
                exitBOffset = s.offsetM;
            }
        }
        const junctionSample = sampleAt(samples, lap, midDist);
        console.log(
            `  linked R${a.radius_m.toFixed(0)} @${a.position_m.toFixed(0)} then `
            + `R${b.radius_m.toFixed(0)} @${b.position_m.toFixed(0)} (${gap.toFixed(0)} m): `
            + `first apex ${fmt(sampleAt(samples, lap, a.position_m).offsetM)} m `
            + `(${fmt(apexA)} inside), junction ${fmt(junctionSample.offsetM)} m `
            + `(${fmt(junction)} inside), second exit ${fmt(exitBOffset)} m `
            + `(${fmt(exitB)} inside) at ${exitBAt.toFixed(0)} m`,
        );
        if (!(apexA > 1.5)) failures.push(`${name} linked first corner has no apex (${apexA.toFixed(1)} m inside)`);
        if (!(exitB < -0.3 * limit)) failures.push(`${name} linked second corner does not exit outside (${exitB.toFixed(1)} m inside)`);
        if (!(exitB < junction - 1)) {
            failures.push(`${name} linked pair does not sacrifice the first exit (junction ${junction.toFixed(1)}, second exit ${exitB.toFixed(1)})`);
        }
    } else {
        failures.push(`${name} has no linked-corner pair`);
    }

    const centreK = computeCurvatureXY(points.map(p => p.x), points.map(p => p.y), points.map(p => p.distance), 20, lap);
    const lineK = computeCurvatureXY(samples.map(s => s.x), samples.map(s => s.y), points.map(p => p.distance), 20, lap);
    const kinks = [];
    for (let i = 0; i < centreK.length; i++) {
        const radius = 1 / Math.max(centreK[i], 1e-8);
        if (radius < 150 || radius > 900) continue;
        if (radius > 280 && radius < 400) continue;
        const im = (i - 1 + centreK.length) % centreK.length;
        const ip = (i + 1) % centreK.length;
        if (!(centreK[i] >= centreK[im] && centreK[i] >= centreK[ip])) continue;
        const dist = points[i].distance;
        const nearCorner = radius >= 400
            ? corners.some(c => c.radius_m < 200 && circ(c.position_m, dist, lap) < 180)
            : corners.some(c => c.radius_m < 50 && circ(c.position_m, dist, lap) < 90);
        if (nearCorner) continue;
        let peakC = 0;
        let peakL = 0;
        for (let k = -15; k <= 15; k++) {
            const j = (i + k + centreK.length) % centreK.length;
            peakC = Math.max(peakC, centreK[j]);
            peakL = Math.max(peakL, lineK[j]);
        }
        kinks.push({ dist, radius, ratio: peakL / Math.max(peakC, 1e-8) });
    }
    kinks.sort((a, b) => a.dist - b.dist);
    const distinct = [];
    for (const kink of kinks) {
        const prev = distinct[distinct.length - 1];
        if (prev && circ(prev.dist, kink.dist, lap) < 30) {
            if (kink.ratio < prev.ratio) distinct[distinct.length - 1] = kink;
        } else {
            distinct.push(kink);
        }
    }
    for (const kink of distinct) {
        const limitRatio = kink.radius >= 400 ? 1 : 1.02;
        if (!(kink.ratio < limitRatio)) {
            failures.push(`${name} kink @${kink.dist.toFixed(0)} m is not straighter (ratio ${kink.ratio.toFixed(3)})`);
        }
    }
    const straighter = distinct.filter(k => k.ratio < 0.995);
    if (straighter.length) {
        console.log(
            '  kinks (line peak κ / centreline peak κ): '
            + straighter.map(k => `@${k.dist.toFixed(0)} R${k.radius.toFixed(0)} ×${k.ratio.toFixed(3)}`).join(', '),
        );
    } else if (name === 'layout A') {
        failures.push(`${name} found no isolated flat-out kink`);
    } else {
        console.log('  kinks: no isolated bend clearly straighter than the centreline');
    }
    if (name === 'version-2' && !straighter.some(k => k.ratio < 0.99)) {
        failures.push(`${name} has no kink straighter than the centreline`);
    }
}

const cases = [
    ['layout A', readJson(join(siteDir, 'layouts/layout_a_straight_hairpin.json'))],
    ['version-1', readJson(join(siteDir, 'layouts/edits/version-1.json'))],
    ['version-2', readJson(join(root, 'scripts/fixtures/version-2.json'))],
];

for (const [name, layout] of cases) check(name, layout);

if (failures.length) {
    console.log('\nFAIL');
    for (const failure of failures) console.log('  ' + failure);
    process.exit(1);
}
console.log('\nracing line check passed');
