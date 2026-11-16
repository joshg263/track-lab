// Quasi-static driving profile for the layout viewer.
// A smoothed racing line inside the track edges, plus a corner-limited speed
// with a forward acceleration pass and a backward braking pass.
// Generic car limits only: no tyre model and no vehicle dynamics.
// Testable from Node the same way as layout_math.js.

export const GRAVITY_MPS2 = 9.80665;

export const DEFAULT_CAR = {
    aLatG: 1.5,
    brakeG: 1.2,
    accelG: 0.5,
    topSpeedKmh: 250,
};

export const DEFAULT_EDGE_MARGIN_M = 1;
export const DEFAULT_LOOKAHEAD_S = 1.5;

// Stations about this far apart. Curvature is minimised on those offsets,
// then blended back onto every centreline sample.
const LINE_STATION_M = 8;
const LINE_RIDGE = 1e-7;
const LINE_CORNER_RADIUS_M = 170;
const LINE_LONG_STRAIGHT_M = 180;
const LINE_LATE_APEX_BOOST = 10;
const LINE_MIN_LATE_TURN_RAD = 20 * Math.PI / 180;
const LINE_OFFSET_SMOOTH = 5;
const RADIUS_HALF_WINDOW_M = 10;
const CURVATURE_SMOOTH_SAMPLES = 3;
const SPEED_ITERS = 32;
const STATE_ACCEL_MPS2 = 0.3;

function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
}

function wrap(d, lap) {
    if (!(lap > 0)) return 0;
    let x = d % lap;
    if (x < 0) x += lap;
    return x;
}

function finiteOr(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
}

export function normalizeCar(options = {}) {
    return {
        aLatG: clamp(finiteOr(options.aLatG, DEFAULT_CAR.aLatG), 0.2, 3),
        brakeG: clamp(finiteOr(options.brakeG, DEFAULT_CAR.brakeG), 0.2, 3),
        accelG: clamp(finiteOr(options.accelG, DEFAULT_CAR.accelG), 0.05, 2),
        topSpeedKmh: clamp(finiteOr(options.topSpeedKmh, DEFAULT_CAR.topSpeedKmh), 40, 400),
        marginM: clamp(finiteOr(options.marginM, DEFAULT_EDGE_MARGIN_M), 0.05, 3),
        trackWidthM: clamp(finiteOr(options.trackWidthM, 14), 4, 40),
    };
}

function circularSmooth(values, radius) {
    const n = values.length;
    if (n === 0 || radius < 1) return values.slice();
    const out = new Array(n);
    const span = radius * 2 + 1;
    for (let i = 0; i < n; i++) {
        let sum = 0;
        for (let k = -radius; k <= radius; k++) sum += values[(i + k + n) % n];
        out[i] = sum / span;
    }
    return out;
}

function centrelineFrame(points, lapLengthM) {
    const n = points.length;
    const cx = new Array(n);
    const cy = new Array(n);
    const cz = new Array(n);
    const distance = new Array(n);
    const nx = new Array(n);
    const ny = new Array(n);
    for (let i = 0; i < n; i++) {
        cx[i] = points[i].x;
        cy[i] = points[i].y;
        cz[i] = points[i].z;
        distance[i] = points[i].distance;
    }
    for (let i = 0; i < n; i++) {
        const prev = (i - 1 + n) % n;
        const next = (i + 1) % n;
        let tx = cx[next] - cx[prev];
        let ty = cy[next] - cy[prev];
        const len = Math.hypot(tx, ty) || 1;
        tx /= len;
        ty /= len;
        nx[i] = -ty;
        ny[i] = tx;
    }
    const lap = lapLengthM > 0 ? lapLengthM : (distance[n - 1] + Math.hypot(cx[0] - cx[n - 1], cy[0] - cy[n - 1]));
    return { n, cx, cy, cz, distance, nx, ny, lap };
}

// Racing line inside the usable half-width. Offsets minimise path curvature,
// so a real corner is outside, then apex, then outside, and a flat-out kink
// is cut straighter than the centreline. A corner that opens onto a long
// straight is weighted so the apex sits later and the exit stays wide.
// Linked corners share one swing: the first does not get a full exit.
function signedCurvature(x, y) {
    const n = x.length;
    const kappa = new Array(n);
    for (let i = 0; i < n; i++) {
        const im = (i - 1 + n) % n;
        const ip = (i + 1) % n;
        const v1x = x[i] - x[im];
        const v1y = y[i] - y[im];
        const v2x = x[ip] - x[i];
        const v2y = y[ip] - y[i];
        const phi = Math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y);
        const ds = 0.5 * (Math.hypot(v1x, v1y) + Math.hypot(v2x, v2y));
        kappa[i] = phi / Math.max(ds, 1e-4);
    }
    return kappa;
}

function factorBand(d0, d1, d2) {
    const n = d0.length;
    const l0 = new Float64Array(n);
    const l1 = new Float64Array(Math.max(n - 1, 0));
    const l2 = new Float64Array(Math.max(n - 2, 0));
    for (let i = 0; i < n; i++) {
        let s = d0[i];
        if (i >= 1) s -= l1[i - 1] * l1[i - 1];
        if (i >= 2) s -= l2[i - 2] * l2[i - 2];
        if (!(s > 1e-18)) return null;
        l0[i] = Math.sqrt(s);
        if (i + 1 < n) {
            let s1 = d1[i];
            if (i >= 1) s1 -= l2[i - 1] * l1[i - 1];
            l1[i] = s1 / l0[i];
        }
        if (i + 2 < n) l2[i] = d2[i] / l0[i];
    }
    return { l0, l1, l2 };
}

function solveBand(fac, rhs) {
    const { l0, l1, l2 } = fac;
    const n = l0.length;
    const y = new Float64Array(n);
    for (let i = 0; i < n; i++) {
        let s = rhs[i];
        if (i >= 1) s -= l1[i - 1] * y[i - 1];
        if (i >= 2) s -= l2[i - 2] * y[i - 2];
        y[i] = s / l0[i];
    }
    const x = new Float64Array(n);
    for (let i = n - 1; i >= 0; i--) {
        let s = y[i];
        if (i + 1 < n) s -= l1[i] * x[i + 1];
        if (i + 2 < n) s -= l2[i] * x[i + 2];
        x[i] = s / l0[i];
    }
    return x;
}

function emptyBand(n) {
    return {
        d0: new Float64Array(n),
        d1: new Float64Array(Math.max(n - 1, 0)),
        d2: new Float64Array(Math.max(n - 2, 0)),
        c01: 0,
        c02: 0,
        c1n: 0,
    };
}

function copyBand(band) {
    return {
        d0: band.d0.slice(),
        d1: band.d1.slice(),
        d2: band.d2.slice(),
        c01: band.c01,
        c02: band.c02,
        c1n: band.c1n,
    };
}

function addEntry(band, i, j, value) {
    const n = band.d0.length;
    if (i === j) {
        band.d0[i] += value;
        return;
    }
    let a = i;
    let b = j;
    if (a > b) [a, b] = [b, a];
    const forward = b - a;
    const back = n - forward;
    if (forward === 1 && forward <= back) band.d1[a] += value;
    else if (forward === 2 && forward <= back) band.d2[a] += value;
    else if (back === 1) band.c01 += value;
    else if (a === 0 && b === n - 2) band.c02 += value;
    else if (a === 1 && b === n - 1) band.c1n += value;
}

function applyFull(band, x) {
    const n = band.d0.length;
    const y = new Float64Array(n);
    const { d0, d1, d2 } = band;
    for (let i = 0; i < n; i++) {
        let s = d0[i] * x[i];
        if (i + 1 < n) s += d1[i] * x[i + 1];
        if (i - 1 >= 0) s += d1[i - 1] * x[i - 1];
        if (i + 2 < n) s += d2[i] * x[i + 2];
        if (i - 2 >= 0) s += d2[i - 2] * x[i - 2];
        y[i] = s;
    }
    if (n > 4) {
        y[0] += band.c01 * x[n - 1] + band.c02 * x[n - 2];
        y[n - 1] += band.c01 * x[0] + band.c1n * x[1];
        y[n - 2] += band.c02 * x[0];
        y[1] += band.c1n * x[n - 1];
    }
    return y;
}

function solveCyclic(band, rhs) {
    const fac = factorBand(band.d0, band.d1, band.d2);
    if (!fac) return null;
    const n = rhs.length;
    const x = new Float64Array(n);
    let r = rhs.slice();
    let z = solveBand(fac, r);
    let p = z.slice();
    let rz = 0;
    for (let i = 0; i < n; i++) rz += r[i] * z[i];
    const scale = Math.sqrt(rhs.reduce((s, v) => s + v * v, 0)) || 1;
    for (let iter = 0; iter < 24; iter++) {
        const Hp = applyFull(band, p);
        let pHp = 0;
        for (let i = 0; i < n; i++) pHp += p[i] * Hp[i];
        if (!(Math.abs(pHp) > 1e-30)) break;
        const alpha = rz / pHp;
        let r2 = 0;
        for (let i = 0; i < n; i++) {
            x[i] += alpha * p[i];
            r[i] -= alpha * Hp[i];
            r2 += r[i] * r[i];
        }
        if (Math.sqrt(r2) / scale < 1e-8) break;
        z = solveBand(fac, r);
        let rzNew = 0;
        for (let i = 0; i < n; i++) rzNew += r[i] * z[i];
        const beta = rz === 0 ? 0 : rzNew / rz;
        rz = rzNew;
        for (let i = 0; i < n; i++) p[i] = z[i] + beta * p[i];
    }
    return x;
}

function coupledIndices(n, i) {
    const out = [];
    const push = (j) => {
        if (j >= 0 && j < n && j !== i && !out.includes(j)) out.push(j);
    };
    push(i - 1);
    push(i + 1);
    push(i - 2);
    push(i + 2);
    if (i === 0) { push(n - 1); push(n - 2); }
    if (i === 1) push(n - 1);
    if (i === n - 1) { push(0); push(1); }
    if (i === n - 2) push(0);
    return out;
}

function bandEntry(band, i, j) {
    const n = band.d0.length;
    if (i === j) return band.d0[i];
    let a = i;
    let b = j;
    if (a > b) [a, b] = [b, a];
    const forward = b - a;
    const back = n - forward;
    if (forward === 1 && forward <= back) return band.d1[a];
    if (forward === 2 && forward <= back) return band.d2[a];
    if (back === 1) return band.c01;
    if (a === 0 && b === n - 2) return band.c02;
    if (a === 1 && b === n - 1) return band.c1n;
    return 0;
}

function zeroBandEntry(band, i, j) {
    const n = band.d0.length;
    if (i === j) {
        band.d0[i] = 0;
        return;
    }
    let a = i;
    let b = j;
    if (a > b) [a, b] = [b, a];
    const forward = b - a;
    const back = n - forward;
    if (forward === 1 && forward <= back) band.d1[a] = 0;
    else if (forward === 2 && forward <= back) band.d2[a] = 0;
    else if (back === 1) band.c01 = 0;
    else if (a === 0 && b === n - 2) band.c02 = 0;
    else if (a === 1 && b === n - 1) band.c1n = 0;
}

// Bound-constrained convex QP, min 0.5 n·H·n + g·n, |n| <= limit.
// H is cyclic pentadiagonal. Points stay on a bound only while the gradient
// still pushes them outward, so a corner can use the apex without pinning
// the whole straight.
function solveBoundedOffsets(band, g, limit) {
    const n = g.length;
    const fixed = new Int8Array(n);
    const boundValue = new Float64Array(n);
    const first = solveCyclic(band, Float64Array.from(g, (v) => -v));
    const x = first ? first.slice() : new Float64Array(n);
    for (let i = 0; i < n; i++) x[i] = clamp(x[i], -limit, limit);
    for (let iter = 0; iter < 12; iter++) {
        const grad = applyFull(band, x);
        for (let i = 0; i < n; i++) grad[i] += g[i];
        for (let i = 0; i < n; i++) {
            const atHi = x[i] >= limit - 1e-6;
            const atLo = x[i] <= -limit + 1e-6;
            if (atHi && !atLo && grad[i] >= -1e-7) {
                fixed[i] = 1;
                boundValue[i] = limit;
                x[i] = limit;
            } else if (atLo && !atHi && grad[i] <= 1e-7) {
                fixed[i] = -1;
                boundValue[i] = -limit;
                x[i] = -limit;
            } else {
                fixed[i] = 0;
            }
        }
        const reduced = copyBand(band);
        const rhs = Float64Array.from(g, (v) => -v);
        for (let i = 0; i < n; i++) {
            if (fixed[i] === 0) continue;
            const v = boundValue[i];
            for (const j of coupledIndices(n, i)) {
                const hij = bandEntry(reduced, i, j);
                if (hij !== 0) rhs[j] -= hij * v;
            }
            for (const j of coupledIndices(n, i)) zeroBandEntry(reduced, i, j);
            reduced.d0[i] = 1;
            rhs[i] = v;
        }
        const solved = solveCyclic(reduced, rhs);
        if (!solved) break;
        let viol = 0;
        let moved = 0;
        for (let i = 0; i < n; i++) {
            let v = fixed[i] !== 0 ? boundValue[i] : solved[i];
            if (v > limit) { v = limit; viol++; }
            else if (v < -limit) { v = -limit; viol++; }
            moved = Math.max(moved, Math.abs(v - x[i]));
            x[i] = v;
        }
        if (viol === 0 && moved < 1e-3) break;
    }
    for (let i = 0; i < n; i++) x[i] = clamp(x[i], -limit, limit);
    return x;
}

function lateApexWeights(kappa, ds) {
    const n = kappa.length;
    const thresh = 1 / LINE_CORNER_RADIUS_M;
    const hot = kappa.map((k) => Math.abs(k) >= thresh);
    const runs = [];
    let i = 0;
    while (i < n) {
        if (!hot[i]) { i++; continue; }
        let j = i;
        while (j < n && hot[j]) j++;
        runs.push({ s: i, e: j - 1, wrap: false });
        i = j;
    }
    if (runs.length >= 2 && hot[0] && hot[n - 1]) {
        const last = runs.pop();
        runs[0].s = last.s;
        runs[0].wrap = true;
    }
    const weight = new Array(n).fill(1);
    const runLen = (run) => (run.wrap ? (n - run.s) + run.e + 1 : run.e - run.s + 1);
    for (let r = 0; r < runs.length; r++) {
        const run = runs[r];
        const len = runLen(run);
        const next = runs[(r + 1) % runs.length];
        let gap = next.s - (run.e + 1);
        if (gap < 0) gap += n;
        const straightM = gap * ds;
        let turn = 0;
        for (let k = 0; k < len; k++) turn += kappa[(run.s + k) % n] * ds;
        if (straightM < LINE_LONG_STRAIGHT_M || Math.abs(turn) < LINE_MIN_LATE_TURN_RAD) continue;
        for (let k = 0; k < len; k++) {
            const idx = (run.s + k) % n;
            const along = len <= 1 ? 1 : k / (len - 1);
            weight[idx] *= 1 + LINE_LATE_APEX_BOOST * along;
        }
        const tail = Math.min(gap, Math.max(1, Math.round(40 / ds)));
        for (let k = 1; k <= tail; k++) {
            weight[(run.e + k) % n] *= 1 + LINE_LATE_APEX_BOOST;
        }
    }
    return weight;
}

function stationOffsets(kappaRaw, ds, limit) {
    const kappa = circularSmooth(kappaRaw, 1);
    const n = kappa.length;
    const weight = lateApexWeights(kappa, ds);
    const a = 1 / (ds * ds);
    const band = emptyBand(n);
    const g = new Float64Array(n);
    for (let s = 0; s < n; s++) {
        const im = (s - 1 + n) % n;
        const ip = (s + 1) % n;
        const idx = [im, s, ip];
        const coef = [a, -2 * a, a];
        const w = ds * weight[s];
        for (let p = 0; p < 3; p++) {
            for (let q = p; q < 3; q++) {
                addEntry(band, idx[p], idx[q], 2 * w * coef[p] * coef[q]);
            }
            g[idx[p]] += 2 * w * coef[p] * kappa[s];
        }
    }
    for (let s = 0; s < n; s++) band.d0[s] += 2 * ds * LINE_RIDGE;
    return solveBoundedOffsets(band, g, limit);
}

function valueAtDistance(distance, values, target, lap) {
    const n = values.length;
    let d = target % lap;
    if (d < 0) d += lap;
    if (d <= distance[0]) return values[0];
    if (d >= distance[n - 1]) {
        const span = lap - distance[n - 1];
        const t = span > 1e-9 ? (d - distance[n - 1]) / span : 0;
        return values[n - 1] * (1 - t) + values[0] * t;
    }
    let lo = 0;
    let hi = n - 1;
    while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (distance[mid] <= d) lo = mid;
        else hi = mid;
    }
    const span = distance[hi] - distance[lo];
    const t = span > 1e-9 ? (d - distance[lo]) / span : 0;
    return values[lo] * (1 - t) + values[hi] * t;
}

function minimumCurvatureOffsets(frame, limitM) {
    const { n, cx, cy, distance, lap } = frame;
    const kappaFine = signedCurvature(cx, cy);
    const nSta = Math.min(n, Math.max(24, Math.round(lap / LINE_STATION_M)));
    const ds = lap / nSta;
    const stationKappa = new Array(nSta);
    for (let s = 0; s < nSta; s++) {
        stationKappa[s] = valueAtDistance(distance, kappaFine, s * ds, lap);
    }
    const solved = stationOffsets(stationKappa, ds, limitM);
    // A short moving average keeps the corner entry from kinking. The curvature
    // term on its own can cancel a bend with one sharp offset change.
    const stationN = circularSmooth(solved, LINE_OFFSET_SMOOTH).map((v) => clamp(v, -limitM, limitM));
    const offsets = new Array(n);
    for (let i = 0; i < n; i++) {
        let u = distance[i] / ds;
        if (u >= nSta) u = 0;
        const s0 = Math.floor(u) % nSta;
        const s1 = (s0 + 1) % nSta;
        const t = u - Math.floor(u);
        const blend = t * t * (3 - 2 * t);
        offsets[i] = clamp(stationN[s0] * (1 - blend) + stationN[s1] * blend, -limitM, limitM);
    }
    return offsets;
}

function mengerRadius(x1, y1, x2, y2, x3, y3) {
    const a = Math.hypot(x2 - x1, y2 - y1);
    const b = Math.hypot(x3 - x2, y3 - y2);
    const c = Math.hypot(x3 - x1, y3 - y1);
    if (a < 1e-4 || b < 1e-4 || c < 1e-4) return 1e6;
    const s = (a + b + c) / 2;
    const areaSq = s * (s - a) * (s - b) * (s - c);
    if (!(areaSq > 0)) return 1e6;
    const radius = (a * b * c) / (4 * Math.sqrt(areaSq));
    return Number.isFinite(radius) ? radius : 1e6;
}

function radiiAlong(x, y, lineDistance, lap) {
    const n = x.length;
    const raw = new Array(n);
    const at = (dist) => {
        let d = wrap(dist, lap);
        let lo = 0;
        let hi = n - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (lineDistance[mid] <= d) lo = mid;
            else hi = mid - 1;
        }
        const i0 = lo;
        const i1 = (i0 + 1) % n;
        const d0 = lineDistance[i0];
        const d1 = i1 === 0 ? lap : lineDistance[i1];
        const span = d1 - d0;
        const t = span > 1e-6 ? (d - d0) / span : 0;
        return [x[i0] + (x[i1] - x[i0]) * t, y[i0] + (y[i1] - y[i0]) * t];
    };
    for (let i = 0; i < n; i++) {
        const [x1, y1] = at(lineDistance[i] - RADIUS_HALF_WINDOW_M);
        const [x3, y3] = at(lineDistance[i] + RADIUS_HALF_WINDOW_M);
        raw[i] = 1 / mengerRadius(x1, y1, x[i], y[i], x3, y3);
    }
    const smooth = circularSmooth(raw, CURVATURE_SMOOTH_SAMPLES);
    return smooth.map(k => (k > 1e-6 ? 1 / k : 1e6));
}

function solveSpeed(ds, sinTheta, radius, car) {
    const n = radius.length;
    const aLat = car.aLatG * GRAVITY_MPS2;
    const aAcc = car.accelG * GRAVITY_MPS2;
    const aBrk = car.brakeG * GRAVITY_MPS2;
    const vMax = car.topSpeedKmh / 3.6;
    const vLim = radius.map(r => Math.min(vMax, Math.sqrt(aLat * Math.max(r, 1))));
    const v = vLim.slice();
    for (let iter = 0; iter < SPEED_ITERS; iter++) {
        let changed = 0;
        for (let i = 0; i < n; i++) {
            const j = (i + 1) % n;
            const aMax = aAcc - GRAVITY_MPS2 * sinTheta[i];
            let v2 = v[i] * v[i] + 2 * aMax * ds[i];
            if (v2 < 0) v2 = 0;
            const pred = Math.min(Math.sqrt(v2), vLim[j]);
            if (pred < v[j] - 1e-8) {
                changed = Math.max(changed, v[j] - pred);
                v[j] = pred;
            }
        }
        for (let i = n - 1; i >= 0; i--) {
            const j = (i + 1) % n;
            const aMag = aBrk + GRAVITY_MPS2 * sinTheta[i];
            let v2 = v[j] * v[j] + 2 * aMag * ds[i];
            if (v2 < 0) v2 = 0;
            const pred = Math.min(Math.sqrt(v2), vLim[i]);
            if (pred < v[i] - 1e-8) {
                changed = Math.max(changed, v[i] - pred);
                v[i] = pred;
            }
        }
        if (changed < 1e-4) break;
    }
    return { v, vLim, aAcc, aBrk, vMax };
}

function stateFor(propulsion) {
    if (propulsion > STATE_ACCEL_MPS2) return 'throttle';
    if (propulsion < -STATE_ACCEL_MPS2) return 'brake';
    return 'coast';
}

// Throttle and brake are what the car applies. longAccel is the net change
// along the road, so an uphill can slow the car while it is still on throttle.
export function driveState(longAccel, sinTheta) {
    const sinT = Number.isFinite(sinTheta) ? sinTheta : 0;
    return stateFor(longAccel + GRAVITY_MPS2 * sinT);
}

export function buildDriveProfile(points, options = {}) {
    if (!points || points.length < 8) return null;
    const car = normalizeCar(options);
    const limitM = Math.max(0.4, car.trackWidthM / 2 - car.marginM);
    const frame = centrelineFrame(points, options.lapLengthM || 0);
    const { n, cx, cy, distance, nx, ny, lap } = frame;
    const offsets = minimumCurvatureOffsets(frame, limitM);
    const x = new Array(n);
    const y = new Array(n);
    const z = new Array(n);
    let maxAbsOffset = 0;
    for (let i = 0; i < n; i++) {
        x[i] = cx[i] + offsets[i] * nx[i];
        y[i] = cy[i] + offsets[i] * ny[i];
        z[i] = options.sampleZ ? options.sampleZ(x[i], y[i]) : frame.cz[i];
        const abs = Math.abs(offsets[i]);
        if (abs > maxAbsOffset) maxAbsOffset = abs;
    }

    const lineDistance = new Array(n);
    const dsLine = new Array(n);
    const dsCentre = new Array(n);
    const sinTheta = new Array(n);
    lineDistance[0] = 0;
    for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const dx = x[j] - x[i];
        const dy = y[j] - y[i];
        const dz = z[j] - z[i];
        const horiz = Math.hypot(dx, dy);
        const ds = Math.hypot(horiz, dz);
        dsLine[i] = Math.max(ds, 1e-4);
        const d0 = distance[i];
        const d1 = j === 0 ? lap : distance[j];
        dsCentre[i] = Math.max(d1 - d0, 1e-4);
        sinTheta[i] = dz / dsLine[i];
        if (j !== 0) lineDistance[j] = lineDistance[i] + dsLine[i];
    }
    const lineLength = lineDistance[n - 1] + dsLine[n - 1];
    const radius = radiiAlong(x, y, lineDistance, lineLength);
    const solved = solveSpeed(dsLine, sinTheta, radius, car);

    const samples = new Array(n);
    let minSpeed = Infinity;
    let maxSpeed = 0;
    let lapTime = 0;
    for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const speed = solved.v[i];
        const next = solved.v[j];
        const longAccel = (next * next - speed * speed) / (2 * dsLine[i]);
        if (speed < minSpeed) minSpeed = speed;
        if (speed > maxSpeed) maxSpeed = speed;
        lapTime += dsLine[i] / Math.max(speed, 0.05);
        const sinT = sinTheta[i];
        const cosT = Math.sqrt(Math.max(0, 1 - sinT * sinT));
        samples[i] = {
            distance: distance[i],
            lineDistance: lineDistance[i],
            x: x[i],
            y: y[i],
            z: z[i],
            offsetM: offsets[i],
            radiusM: radius[i],
            speedMps: speed,
            speedKmh: speed * 3.6,
            dsLine: dsLine[i],
            dsCentre: dsCentre[i],
            sinTheta: sinT,
            gradientPct: cosT > 1e-6 ? (sinT / cosT) * 100 : 0,
            longAccel,
            state: driveState(longAccel, sinT),
        };
    }

    return {
        car,
        limitM,
        lapLengthM: lap,
        lineLengthM: lineLength,
        lapTimeS: lapTime,
        minSpeedMps: minSpeed,
        maxSpeedMps: maxSpeed,
        maxAbsOffsetM: maxAbsOffset,
        accelMps2: solved.aAcc,
        brakeMps2: solved.aBrk,
        samples,
    };
}

export function sampleProfile(profile, distance) {
    if (!profile?.samples?.length) return null;
    const samples = profile.samples;
    const n = samples.length;
    const lap = profile.lapLengthM;
    const d = wrap(distance, lap);
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (samples[mid].distance <= d) lo = mid;
        else hi = mid - 1;
    }
    const i = lo;
    const j = (i + 1) % n;
    const d0 = samples[i].distance;
    const d1 = j === 0 ? lap : samples[j].distance;
    const span = d1 - d0;
    const t = span > 1e-6 ? clamp((d - d0) / span, 0, 1) : 0;
    const a = samples[i];
    const b = samples[j];
    const speed = a.speedMps + (b.speedMps - a.speedMps) * t;
    return {
        distance: d,
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        z: a.z + (b.z - a.z) * t,
        speedMps: speed,
        speedKmh: speed * 3.6,
        state: a.state,
        gradientPct: a.gradientPct,
        offsetM: a.offsetM + (b.offsetM - a.offsetM) * t,
        radiusM: a.radiusM,
    };
}

// Move centreline chainage forward by dt seconds at the profiled line speed.
export function advanceDistance(profile, distance, dt) {
    if (!profile?.samples?.length) return distance;
    const lap = profile.lapLengthM;
    if (!(dt > 0) || !(lap > 0)) return wrap(distance, lap);
    const samples = profile.samples;
    const n = samples.length;
    let d = wrap(distance, lap);
    let left = dt;
    for (let guard = 0; guard < 24 && left > 1e-5; guard++) {
        let lo = 0;
        let hi = n - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (samples[mid].distance <= d) lo = mid;
            else hi = mid - 1;
        }
        const i = lo;
        const endD = i + 1 < n ? samples[i + 1].distance : lap;
        let remain = endD - d;
        if (remain < 1e-4) {
            d = i + 1 < n ? samples[i + 1].distance : 0;
            continue;
        }
        const vLine = Math.max(samples[i].speedMps, 0.05);
        const vCentre = vLine * (samples[i].dsCentre / Math.max(samples[i].dsLine, 1e-4));
        const timeToEnd = remain / Math.max(vCentre, 1e-4);
        if (timeToEnd > left) {
            d += vCentre * left;
            left = 0;
        } else {
            left -= timeToEnd;
            d = i + 1 < n ? samples[i + 1].distance : 0;
        }
    }
    return wrap(d, lap);
}

export function lookAheadMetres(speedMps, seconds = DEFAULT_LOOKAHEAD_S) {
    const v = Number.isFinite(speedMps) ? Math.max(speedMps, 0) : 0;
    return clamp(v * seconds, 16, 140);
}

export function formatLapEstimate(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '—';
    const m = Math.floor(seconds / 60);
    const s = seconds - m * 60;
    return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

export const LOOK_OMEGA = 4.5;
export const YAW_OFFSET_RATE_MAX = 0.5;
export const PITCH_RATE_MAX = 0.28;

function wrapAngle(angle) {
    let d = angle;
    while (d > Math.PI) d -= Math.PI * 2;
    while (d < -Math.PI) d += Math.PI * 2;
    return d;
}

// Critically damped yaw/pitch. Rate is hard-limited so a corner cannot whip the view.
export function stepCriticallyDamped(angle, rate, target, dt, omega, maxRate) {
    if (!(dt > 0) || !(omega > 0)) return { angle: target, rate: 0 };
    let err = target - angle;
    while (err > Math.PI) err -= Math.PI * 2;
    while (err < -Math.PI) err += Math.PI * 2;
    const accel = omega * omega * err - 2 * omega * rate;
    let nextRate = rate + accel * dt;
    if (maxRate > 0) nextRate = clamp(nextRate, -maxRate, maxRate);
    return { angle: angle + nextRate * dt, rate: nextRate };
}

// Glance relative to the car heading. The heading is applied immediately, so a
// tight corner cannot leave the camera looking backwards. The rate cap is on
// the glance only, and it scales with the playback multiplier.
export function stepOnboardLook(state, target, dt, playbackMul = 1) {
    const heading = target.heading;
    const yawOffsetTarget = wrapAngle(target.yaw - heading);
    const pitchTarget = target.pitch;
    const pace = clamp(Number.isFinite(playbackMul) ? playbackMul : 1, 0.5, 4);
    if (!state?.ready || !(dt > 0)) {
        return {
            ready: true,
            yawOffset: yawOffsetTarget,
            yawOffsetRate: 0,
            pitch: pitchTarget,
            pitchRate: 0,
            yaw: wrapAngle(heading + yawOffsetTarget),
        };
    }
    const yawStep = stepCriticallyDamped(
        state.yawOffset,
        state.yawOffsetRate,
        yawOffsetTarget,
        dt,
        LOOK_OMEGA,
        YAW_OFFSET_RATE_MAX * pace,
    );
    const pitchStep = stepCriticallyDamped(
        state.pitch,
        state.pitchRate,
        pitchTarget,
        dt,
        LOOK_OMEGA,
        PITCH_RATE_MAX * pace,
    );
    return {
        ready: true,
        yawOffset: yawStep.angle,
        yawOffsetRate: yawStep.rate,
        pitch: pitchStep.angle,
        pitchRate: pitchStep.rate,
        yaw: wrapAngle(heading + yawStep.angle),
    };
}

function circularDistance(a, b, lap) {
    let d = Math.abs(a - b);
    if (d > lap - d) d = lap - d;
    return d;
}

function minSpeedNear(profile, position, windowM) {
    let best = Infinity;
    for (const s of profile.samples) {
        if (circularDistance(s.distance, position, profile.lapLengthM) <= windowM) {
            if (s.speedMps < best) best = s.speedMps;
        }
    }
    return best;
}

function longestStraight(profile, corners) {
    const lap = profile.lapLengthM;
    const positions = (corners || [])
        .map(c => c.position_m)
        .filter(p => Number.isFinite(p))
        .sort((a, b) => a - b);
    const gaps = [];
    if (positions.length === 0) {
        gaps.push({ start: 0, end: lap, length: lap });
    } else {
        for (let i = 0; i < positions.length; i++) {
            const start = positions[i];
            const end = i + 1 < positions.length ? positions[i + 1] : positions[0] + lap;
            gaps.push({ start, end, length: end - start });
        }
    }
    gaps.sort((a, b) => b.length - a.length);
    const gap = gaps[0];
    // Leave the corner midpoints out of the peak so the straight is the straight.
    const inset = 40;
    let from = gap.start;
    let to = gap.end;
    if (gap.length > inset * 2 + 30) {
        from += inset;
        to -= inset;
    }
    let peak = 0;
    let peakAt = wrap(gap.start, lap);
    for (const s of profile.samples) {
        let at = s.distance;
        if (at < gap.start - 1e-6) at += lap;
        if (at >= from - 1e-6 && at < to - 1e-6 && s.speedMps > peak) {
            peak = s.speedMps;
            peakAt = s.distance;
        }
    }
    return {
        lengthM: gap.length,
        startM: wrap(gap.start, lap),
        endM: wrap(gap.end, lap),
        peakMps: peak,
        peakAtM: peakAt,
    };
}

export function assessDriveProfile(profile, corners, options = {}) {
    const tightRadiusM = options.tightRadiusM ?? 100;
    const tolerance = options.accelTolerance ?? 0.01;
    const windowM = options.cornerWindowM ?? 40;
    const failures = [];
    if (!profile) {
        return { ok: false, failures: ['no profile'], tight: [], straight: null };
    }
    let edgeClearanceM = Infinity;
    for (const s of profile.samples) {
        const room = profile.limitM - Math.abs(s.offsetM);
        if (room < edgeClearanceM) edgeClearanceM = room;
        if (room < -1e-3) {
            failures.push(
                `racing line leaves the track at ${s.distance.toFixed(1)} m `
                + `(offset ${s.offsetM.toFixed(2)} m, limit ±${profile.limitM.toFixed(2)} m)`,
            );
            break;
        }
    }
    let worstAccelFrac = 0;
    const n = profile.samples.length;
    for (let i = 0; i < n; i++) {
        const s = profile.samples[i];
        const j = (i + 1) % n;
        const ds = s.dsLine;
        if (!(ds > 0.05)) continue;
        const v0 = s.speedMps;
        const v1 = profile.samples[j].speedMps;
        const a = (v1 * v1 - v0 * v0) / (2 * ds);
        const aMax = profile.accelMps2 - GRAVITY_MPS2 * s.sinTheta;
        const aMin = -(profile.brakeMps2 + GRAVITY_MPS2 * s.sinTheta);
        const overHi = a - aMax;
        const overLo = aMin - a;
        const allowHi = Math.max(Math.abs(aMax) * tolerance, 1e-3);
        const allowLo = Math.max(Math.abs(aMin) * tolerance, 1e-3);
        const frac = Math.max(overHi / Math.max(Math.abs(aMax), 1e-3), overLo / Math.max(Math.abs(aMin), 1e-3), 0);
        if (frac > worstAccelFrac) worstAccelFrac = frac;
        if (overHi > allowHi || overLo > allowLo) {
            if (failures.length < 6) {
                failures.push(
                    `longitudinal accel ${a.toFixed(3)} m/s² outside `
                    + `[${aMin.toFixed(3)}, ${aMax.toFixed(3)}] at ${s.distance.toFixed(0)} m`,
                );
            }
        }
    }
    const straight = longestStraight(profile, corners);
    const tight = [];
    for (const corner of corners || []) {
        const radius = corner.radius_m;
        if (!(radius < tightRadiusM)) continue;
        const speed = minSpeedNear(profile, corner.position_m, windowM);
        tight.push({
            positionM: corner.position_m,
            radiusM: radius,
            speedMps: speed,
            speedKmh: speed * 3.6,
        });
        if (!(speed < straight.peakMps - 0.05)) {
            failures.push(
                `corner R ${radius.toFixed(1)} m at ${corner.position_m.toFixed(0)} m is `
                + `${(speed * 3.6).toFixed(1)} km/h, not below the longest straight `
                + `(${straight.lengthM.toFixed(0)} m, peak ${(straight.peakMps * 3.6).toFixed(1)} km/h)`,
            );
        }
    }
    return {
        ok: failures.length === 0,
        failures,
        tight,
        straight,
        edgeClearanceM,
        worstAccelFrac,
        numCorners: (corners || []).length,
        tightCorners: tight.length,
    };
}
