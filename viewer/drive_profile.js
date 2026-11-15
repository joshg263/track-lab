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

export const DEFAULT_EDGE_MARGIN_M = 0.6;
export const DEFAULT_LOOKAHEAD_S = 1.5;

const LINE_WINDOW_M = 16;
const LINE_ITERATIONS = 48;
const LINE_RELAX = 0.7;
const OFFSET_SMOOTH_SAMPLES = 4;
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

// Iterative minimum-curvature offset. Each pass moves a station toward the
// straight chord between neighbours a short way ahead and behind, then clamps
// that offset to the usable half-width. The chord of a bend falls to the
// inside, so corners open onto the width instead of hugging the centreline.
function minimumCurvatureOffsets(frame, limitM) {
    const { n, cx, cy, nx, ny, lap } = frame;
    const spacing = lap / n;
    const step = Math.max(2, Math.round(LINE_WINDOW_M / Math.max(spacing, 0.5)));
    let offsets = new Array(n).fill(0);
    const next = new Array(n);
    for (let iter = 0; iter < LINE_ITERATIONS; iter++) {
        for (let i = 0; i < n; i++) {
            const im = (i - step + n) % n;
            const ip = (i + step) % n;
            const ax = cx[im] + offsets[im] * nx[im];
            const ay = cy[im] + offsets[im] * ny[im];
            const bx = cx[ip] + offsets[ip] * nx[ip];
            const by = cy[ip] + offsets[ip] * ny[ip];
            const dx = bx - ax;
            const dy = by - ay;
            const crossND = nx[i] * dy - ny[i] * dx;
            let target;
            if (Math.abs(crossND) < 1e-8) {
                const mx = (ax + bx) / 2;
                const my = (ay + by) / 2;
                target = (mx - cx[i]) * nx[i] + (my - cy[i]) * ny[i];
            } else {
                const acx = ax - cx[i];
                const acy = ay - cy[i];
                target = (acx * dy - acy * dx) / crossND;
            }
            if (!Number.isFinite(target)) target = offsets[i];
            target = clamp(target, -limitM, limitM);
            next[i] = offsets[i] * (1 - LINE_RELAX) + target * LINE_RELAX;
        }
        offsets = next.slice();
    }
    for (let pass = 0; pass < 2; pass++) {
        offsets = circularSmooth(offsets, OFFSET_SMOOTH_SAMPLES);
        for (let i = 0; i < n; i++) offsets[i] = clamp(offsets[i], -limitM, limitM);
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
