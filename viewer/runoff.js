// Run-off zones as a layout constraint.
// Starting estimates, not FIA figures. Line-for-line port of scripts/runoff.py.

import { analyzeCornersXY } from './layout_math.js';

export const GRAVITY_MPS2 = 9.80665;

export const DEFAULT_DECEL_G = 0.5;
export const TARMAC_DECEL_G = 0.9;
export const DEFAULT_WIDTH_M = 50.0;
export const DEFAULT_SLOPE_LIMIT = 0.10;
export const DEFAULT_STEP_M = 3.0;
export const DEFAULT_OVERLAP_GAP_M = 100.0;
export const PROXY_LAT_G = 1.5;

export const ESCAPE_OUTWARD = 0.5;
export const TANGENT_LOOK_M = 4.0;
export const SAMPLE_SPACING_M = 5.0;
// 5 m chords left a hairpin edge more than 0.5 m off the track.
export const EDGE_STEP_M = 2.5;
export const LEAD_MIN_M = 20.0;
export const LEAD_FRAC = 0.15;
export const LEAD_MAX_M = 60.0;
export const TAPER_MIN_M = 30.0;
export const TAPER_FRAC = 0.45;
export const TAPER_MAX_M = 140.0;
export const CLIP_BUFFER_M = 3.0;
export const CLIP_SEARCH_STEPS = 18;

export function speedFromTrace(trace, lapM, distanceM) {
    if (!trace || !trace.length || !(lapM > 0)) return null;
    const n = trace.length;
    let d = distanceM % lapM;
    if (d < 0) d += lapM;
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (trace[mid].distance <= d) lo = mid;
        else hi = mid - 1;
    }
    const i = lo;
    const j = (i + 1) % n;
    const d0 = trace[i].distance;
    const d1 = j === 0 ? lapM : trace[j].distance;
    const span = d1 - d0;
    let t = 0;
    if (span > 1e-6) {
        t = (d - d0) / span;
        if (t < 0) t = 0;
        else if (t > 1) t = 1;
    }
    const speed = trace[i].speed_mps + (trace[j].speed_mps - trace[i].speed_mps) * t;
    if (!Number.isFinite(speed) || speed <= 0) return null;
    return speed;
}

function unit(x, y) {
    const length = Math.hypot(x, y);
    if (length < 1e-9) return [1, 0];
    return [x / length, y / length];
}

function sampleXY(xy, distances, lapM, distanceM) {
    const n = distances.length;
    let d = lapM > 0 ? distanceM % lapM : distanceM;
    if (d < 0) d += lapM;
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (distances[mid] <= d) lo = mid;
        else hi = mid - 1;
    }
    const i = lo;
    let d0;
    let d1;
    let v1x;
    let v1y;
    if (i >= n - 1) {
        d0 = distances[i];
        d1 = lapM;
        v1x = xy[0].x;
        v1y = xy[0].y;
    } else {
        d0 = distances[i];
        d1 = distances[i + 1];
        v1x = xy[i + 1].x;
        v1y = xy[i + 1].y;
    }
    const span = d1 - d0;
    let t = 0;
    if (span > 1e-9) {
        t = (d - d0) / span;
        if (t < 0) t = 0;
        else if (t > 1) t = 1;
    }
    return [
        xy[i].x + t * (v1x - xy[i].x),
        xy[i].y + t * (v1y - xy[i].y),
    ];
}

function approachTangent(xy, distances, lapM, distanceM) {
    const [x0, y0] = sampleXY(xy, distances, lapM, distanceM - TANGENT_LOOK_M);
    const [x1, y1] = sampleXY(xy, distances, lapM, distanceM);
    return unit(x1 - x0, y1 - y0);
}

function travelTangent(xy, distances, lapM, distanceM) {
    const [x0, y0] = sampleXY(xy, distances, lapM, distanceM);
    const [x1, y1] = sampleXY(xy, distances, lapM, distanceM + TANGENT_LOOK_M);
    return unit(x1 - x0, y1 - y0);
}

function turnSign(xy, distances, lapM, startIndex, endIndex) {
    let total = 0;
    let prev = null;
    let i = startIndex | 0;
    endIndex = endIndex | 0;
    let guard = 0;
    const limit = Math.max(1, endIndex - i + 1);
    while (guard <= limit) {
        guard += 1;
        const [tx, ty] = travelTangent(xy, distances, lapM, distances[i]);
        if (prev) total += prev[0] * ty - prev[1] * tx;
        prev = [tx, ty];
        if (i >= endIndex) break;
        i += 1;
    }
    if (total < -1e-8) return -1;
    return 1;
}

function outsideNormal(tx, ty, sign) {
    if (sign >= 0) return [ty, -tx];
    return [-ty, tx];
}

function escapeDir(tx, ty, sign) {
    const [nx, ny] = outsideNormal(tx, ty, sign);
    return unit(tx + ESCAPE_OUTWARD * nx, ty + ESCAPE_OUTWARD * ny);
}

function interpClosed(distances, values, lapM, distanceM) {
    const n = distances.length;
    if (n === 0) return 0;
    if (!(lapM > 0)) return values[0];
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
    let d0;
    let d1;
    let v1;
    if (i + 1 >= n) {
        d0 = distances[i];
        d1 = lapM;
        v1 = values[0];
    } else {
        d0 = distances[i];
        d1 = distances[i + 1];
        v1 = values[i + 1];
    }
    const span = d1 - d0;
    let t = 0;
    if (span > 1e-9) {
        t = (d - d0) / span;
        if (t < 0) t = 0;
        else if (t > 1) t = 1;
    }
    const v0 = values[i];
    return v0 + t * (v1 - v0);
}

function pointInPolygon(x, y, poly) {
    let inside = false;
    let j = poly.length - 1;
    for (let i = 0; i < poly.length; i++) {
        const xi = poly[i][0];
        const yi = poly[i][1];
        const xj = poly[j][0];
        const yj = poly[j][1];
        if ((yi > y) !== (yj > y)) {
            const xint = (xj - xi) * (y - yi) / (yj - yi) + xi;
            if (x < xint) inside = !inside;
        }
        j = i;
    }
    return inside;
}

function distPointSegment(px, py, ax, ay, bx, by) {
    const abx = bx - ax;
    const aby = by - ay;
    const ab2 = abx * abx + aby * aby;
    if (ab2 < 1e-12) return Math.hypot(px - ax, py - ay);
    let t = ((px - ax) * abx + (py - ay) * aby) / ab2;
    if (t < 0) t = 0;
    else if (t > 1) t = 1;
    return Math.hypot(px - (ax + abx * t), py - (ay + aby * t));
}

function distPointPolygon(px, py, poly) {
    if (pointInPolygon(px, py, poly)) return 0;
    let best = Infinity;
    const n = poly.length;
    for (let i = 0; i < n; i++) {
        const a = poly[i];
        const b = poly[(i + 1) % n];
        const dist = distPointSegment(px, py, a[0], a[1], b[0], b[1]);
        if (dist < best) best = dist;
    }
    return best;
}

function chainageGap(distanceM, startM, endM, lapM) {
    if (!(lapM > 0)) return 0;
    let length = endM - startM;
    if (length < 0) length = 0;
    if (length >= lapM - 1e-6) return 0;
    let x = (distanceM - startM) % lapM;
    if (x < 0) x += lapM;
    if (x <= length) return 0;
    return Math.min(x - length, lapM - x);
}

function alongEdge(poly, sx, sy) {
    const [ax, ay] = poly[0];
    const [bx, by] = poly[1];
    const [dx, dy] = poly[3];
    const ux = bx - ax;
    const uy = by - ay;
    const vx = dx - ax;
    const vy = dy - ay;
    const det = ux * vy - uy * vx;
    if (Math.abs(det) < 1e-9) return 0;
    let u = ((sx - ax) * vy - (sy - ay) * vx) / det;
    if (u < 0) return 0;
    if (u > 1) return 1;
    return u;
}

function roadEdgeZ(distances, designZ, lapM, poly, sx, sy, startM, widthM) {
    // Designed deck height on the outside edge beside this sample. The deck
    // is flat from centreline to edge, so this is z(s), not a mesh sample.
    const u = alongEdge(poly, sx, sy);
    return interpClosed(distances, designZ, lapM, startM + u * widthM);
}

function polygonArea(poly) {
    let area = 0;
    const n = poly.length;
    for (let i = 0; i < n; i++) {
        const p = poly[i];
        const q = poly[(i + 1) % n];
        area += p[0] * q[1] - q[0] * p[1];
    }
    return Math.abs(area) * 0.5;
}

function interiorSamples(poly, spacing, sampleGround) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const p of poly) {
        if (p[0] < minX) minX = p[0];
        if (p[0] > maxX) maxX = p[0];
        if (p[1] < minY) minY = p[1];
        if (p[1] > maxY) maxY = p[1];
    }
    const x0 = Math.floor(minX / spacing) * spacing;
    const y0 = Math.floor(minY / spacing) * spacing;
    const samples = [];
    let ix = 0;
    while (true) {
        const x = x0 + ix * spacing;
        if (x > maxX + 1e-9) break;
        let iy = 0;
        while (true) {
            const y = y0 + iy * spacing;
            if (y > maxY + 1e-9) break;
            if (pointInPolygon(x, y, poly)) {
                let z = Number(sampleGround(x, y));
                if (!Number.isFinite(z)) z = NaN;
                samples.push([ix, iy, x, y, z]);
            }
            iy += 1;
        }
        ix += 1;
    }
    if (samples.length) return samples;
    let cx = 0;
    let cy = 0;
    for (const p of poly) {
        cx += p[0];
        cy += p[1];
    }
    cx /= poly.length;
    cy /= poly.length;
    return [[-1, -1, cx, cy, Number(sampleGround(cx, cy))]];
}

function axisPartial(byKey, ix, iy, x, y, z, axis) {
    const plus = axis === 0 ? `${ix + 1}:${iy}` : `${ix}:${iy + 1}`;
    const minus = axis === 0 ? `${ix - 1}:${iy}` : `${ix}:${iy - 1}`;
    const fwd = byKey.get(plus);
    const back = byKey.get(minus);
    const coord = axis === 0 ? 2 : 3;
    const here = axis === 0 ? x : y;
    if (fwd && back) {
        const span = fwd[coord] - back[coord];
        if (Math.abs(span) < 1e-9) return null;
        return (fwd[4] - back[4]) / span;
    }
    if (fwd) {
        const span = fwd[coord] - here;
        if (Math.abs(span) < 1e-9) return null;
        return (fwd[4] - z) / span;
    }
    if (back) {
        const span = here - back[coord];
        if (Math.abs(span) < 1e-9) return null;
        return (z - back[4]) / span;
    }
    return null;
}

function maxSlopes(samples, ex, ey, px, py) {
    // A neighbour pair is only one partial derivative. Both components are
    // rebuilt at a sample and then projected, so a diagonal grade is not
    // split between the axes and under-counted.
    const byKey = new Map();
    for (const sample of samples) {
        if (sample[0] < 0) continue;
        byKey.set(`${sample[0]}:${sample[1]}`, sample);
    }
    let maxLong = 0;
    let maxCross = 0;
    for (const sample of byKey.values()) {
        const ix = sample[0];
        const iy = sample[1];
        const x = sample[2];
        const y = sample[3];
        const z = sample[4];
        const gx = axisPartial(byKey, ix, iy, x, y, z, 0);
        const gy = axisPartial(byKey, ix, iy, x, y, z, 1);
        if (gx == null || gy == null) continue;
        const longSlope = Math.abs(gx * ex + gy * ey);
        const crossSlope = Math.abs(gx * px + gy * py);
        if (longSlope > maxLong) maxLong = longSlope;
        if (crossSlope > maxCross) maxCross = crossSlope;
    }
    return [maxLong, maxCross];
}

function wrap(distanceM, lapM) {
    if (!(lapM > 0)) return distanceM;
    let d = distanceM % lapM;
    if (d < 0) d += lapM;
    return d;
}

function forward(startM, endM, lapM) {
    if (!(lapM > 0)) return endM - startM;
    let d = (endM - startM) % lapM;
    if (d < 0) d += lapM;
    return d;
}

function gapToWindow(distanceM, s0, s2, lapM) {
    if (!(lapM > 0)) return 0;
    const span = forward(s0, s2, lapM);
    if (span >= lapM - 1e-6) return 0;
    const x = forward(s0, distanceM, lapM);
    if (x <= span) return 0;
    const back = x - span;
    const other = lapM - x;
    return back < other ? back : other;
}

function insertChain(samples, s0, lapM, target) {
    const fwdT = forward(s0, target, lapM);
    const wrapped = wrap(target, lapM);
    for (let i = 0; i < samples.length; i++) {
        const fwd = forward(s0, samples[i], lapM);
        if (Math.abs(fwd - fwdT) < 0.05) return;
        if (fwd > fwdT) {
            samples.splice(i, 0, wrapped);
            return;
        }
    }
    samples.push(wrapped);
}

function chainSamples(s0, s2, lapM, step, extras) {
    const span = forward(s0, s2, lapM);
    if (span < 1e-3) return [lapM > 0 ? s0 % lapM : s0];
    const n = Math.max(1, Math.ceil(span / step));
    const samples = [];
    for (let i = 0; i <= n; i++) {
        const s = s0 + span * i / n;
        samples.push(wrap(s, lapM));
    }
    for (const extra of extras) insertChain(samples, s0, lapM, extra);
    return samples;
}

function edgeFrame(xy, distances, lapM, distanceM, sign, halfWidth) {
    const [px, py] = sampleXY(xy, distances, lapM, distanceM);
    const [tx, ty] = travelTangent(xy, distances, lapM, distanceM);
    const [nx, ny] = outsideNormal(tx, ty, sign);
    return [px + nx * halfWidth, py + ny * halfWidth, tx, ty, nx, ny];
}

function windowLengths(depth, cornerLen, lapM) {
    let lead = Math.min(LEAD_MAX_M, Math.max(LEAD_MIN_M, LEAD_FRAC * depth));
    let taper = Math.min(TAPER_MAX_M, Math.max(TAPER_MIN_M, TAPER_FRAC * depth));
    const room = Math.max(0, lapM - cornerLen);
    if (lead + taper > room * 0.85 && lead + taper > 1e-6) {
        const scale = room * 0.85 / (lead + taper);
        lead *= scale;
        taper *= scale;
    }
    if (room > 8) {
        lead = lead > 0 ? Math.max(8, lead) : 8;
        if (lead + taper > room * 0.85) taper = Math.max(0, room * 0.85 - lead);
    }
    return [lead, taper];
}

function blockersOf(trackX, trackY, trackD, lapM, s0, s2, overlapGapM) {
    const found = [];
    for (let i = 0; i < trackD.length; i++) {
        const gap = gapToWindow(trackD[i], s0, s2, lapM);
        if (gap <= overlapGapM) continue;
        found.push([trackX[i], trackY[i], trackD[i]]);
    }
    return found;
}

function segmentHit(ax, ay, bx, by, blockers, radius) {
    let best = null;
    let bestD = radius;
    for (const blocker of blockers) {
        const dist = distPointSegment(blocker[0], blocker[1], ax, ay, bx, by);
        if (dist < bestD) {
            bestD = dist;
            best = blocker[2];
        }
    }
    return best;
}

function segmentsCross(ax, ay, bx, by, cx, cy, dx, dy) {
    const rx = bx - ax;
    const ry = by - ay;
    const sx = dx - cx;
    const sy = dy - cy;
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-9) return false;
    const qx = cx - ax;
    const qy = cy - ay;
    const t = (qx * sy - qy * sx) / den;
    const u = (qx * ry - qy * rx) / den;
    return t > 1e-4 && t < 1 - 1e-4 && u > 1e-4 && u < 1 - 1e-4;
}

function inTri(px, py, ax, ay, bx, by, cx, cy) {
    const c1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
    const c2 = (cx - bx) * (py - by) - (cy - by) * (px - bx);
    const c3 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx);
    if (c1 >= -1e-6 && c2 >= -1e-6 && c3 >= -1e-6) return true;
    return c1 <= 1e-6 && c2 <= 1e-6 && c3 <= 1e-6;
}

function quadHit(ax, ay, bx, by, cx, cy, dx, dy, blockers, radius) {
    let best = null;
    let bestD = radius;
    for (let i = 0; i < blockers.length; i++) {
        const px = blockers[i][0];
        const py = blockers[i][1];
        const inside = inTri(px, py, ax, ay, bx, by, cx, cy) || inTri(px, py, bx, by, dx, dy, cx, cy);
        let dist = distPointSegment(px, py, bx, by, dx, dy);
        const e1 = distPointSegment(px, py, ax, ay, bx, by);
        const e2 = distPointSegment(px, py, cx, cy, dx, dy);
        if (e1 < dist) dist = e1;
        if (e2 < dist) dist = e2;
        if (inside || dist < bestD) {
            bestD = inside ? 0 : dist;
            best = blockers[i][2];
        }
    }
    return best;
}

function shortenRay(ax, ay, bx, by, blockers, radius) {
    const hit = segmentHit(ax, ay, bx, by, blockers, radius);
    if (hit == null) return [bx, by, null];
    let lo = 0;
    let hi = 1;
    let found = hit;
    for (let step = 0; step < CLIP_SEARCH_STEPS; step++) {
        const mid = (lo + hi) * 0.5;
        const mx = ax + (bx - ax) * mid;
        const my = ay + (by - ay) * mid;
        const stepHit = segmentHit(ax, ay, mx, my, blockers, radius);
        if (stepHit == null) lo = mid;
        else {
            hi = mid;
            found = stepHit;
        }
    }
    return [ax + (bx - ax) * lo, ay + (by - ay) * lo, found];
}

function buildFan(xy, distances, lapM, startM, endM, sign, halfWidth, depth, widthM, escapeTx, escapeTy, trackX, trackY, trackD, overlapGapM) {
    let cornerLen = forward(startM, endM, lapM);
    if (cornerLen < 1) cornerLen = 1;
    const [lead, taper] = windowLengths(depth, cornerLen, lapM);
    const s0 = wrap(startM - lead, lapM);
    const s2 = wrap(endM + taper, lapM);
    const samples = chainSamples(s0, s2, lapM, EDGE_STEP_M, [startM, endM]);
    const frame = edgeFrame(xy, distances, lapM, startM, sign, halfWidth);
    const bx = frame[0];
    const by = frame[1];
    const nx = frame[4];
    const ny = frame[5];
    const tx = escapeTx;
    const ty = escapeTy;
    const blockers = blockersOf(trackX, trackY, trackD, lapM, s0, s2, overlapGapM);
    const radius = halfWidth + CLIP_BUFFER_M;
    const inner = [];
    const frames = [];
    for (let index = 0; index < samples.length; index++) {
        const s = samples[index];
        const local = edgeFrame(xy, distances, lapM, s, sign, halfWidth);
        inner.push([local[0], local[1], s]);
        frames.push([local[4], local[5]]);
    }
    let brakeIndex = 0;
    let bestBrake = 1e18;
    for (let index = 0; index < inner.length; index++) {
        const s = inner[index][2];
        const ahead = forward(startM, s, lapM);
        const back = forward(s, startM, lapM);
        const gap = ahead < back ? ahead : back;
        if (gap < bestBrake) {
            bestBrake = gap;
            brakeIndex = index;
        }
    }
    let escapeEnd = brakeIndex;
    for (let index = brakeIndex; index < inner.length; index++) {
        const ex = inner[index][0];
        const ey = inner[index][1];
        const lnx = frames[index][0];
        const lny = frames[index][1];
        const agree = lnx * nx + lny * ny;
        const along = (ex - bx) * tx + (ey - by) * ty;
        if (index > brakeIndex && agree < 0.5) break;
        escapeEnd = index;
        if (index > brakeIndex && along >= depth) break;
    }
    if (escapeEnd === brakeIndex && brakeIndex + 1 < inner.length) escapeEnd = brakeIndex + 1;
    const taperSamples = Math.max(2, Math.round(taper / EDGE_STEP_M));
    let taperLimit = inner.length - 1 - taperSamples;
    if (taperLimit < brakeIndex + 1) {
        taperLimit = brakeIndex + 1 < inner.length ? brakeIndex + 1 : brakeIndex;
    }
    if (escapeEnd > taperLimit) escapeEnd = taperLimit;
    const desired = [];
    for (let index = 0; index < inner.length; index++) {
        const ex = inner[index][0];
        const ey = inner[index][1];
        const s = inner[index][2];
        const lnx = frames[index][0];
        const lny = frames[index][1];
        if (index <= brakeIndex) {
            desired.push([ex, ey]);
            continue;
        }
        if (index <= escapeEnd) {
            const span = escapeEnd - brakeIndex;
            const u = (index - brakeIndex) / span;
            const cross = (ex - bx) * nx + (ey - by) * ny;
            const reach = cross < 0 ? -cross : 0;
            if (reach > widthM) desired.push([ex + nx * widthM, ey + ny * widthM]);
            else desired.push([bx + tx * (depth * u), by + ty * (depth * u)]);
            continue;
        }
        const fromBrake = forward(startM, s, lapM);
        let h;
        if (fromBrake <= cornerLen + 0.2) {
            let u = cornerLen < 1e-6 ? 0 : fromBrake / cornerLen;
            if (u < 0) u = 0;
            else if (u > 1) u = 1;
            h = widthM * u;
        } else {
            const into = fromBrake - cornerLen;
            let u = taper < 1e-6 ? 0 : into / taper;
            if (u < 0) u = 0;
            else if (u > 1) u = 1;
            h = widthM * (1 - u);
        }
        desired.push([ex + lnx * h, ey + lny * h]);
    }
    const ts = [];
    let overlapS = null;
    let apexS = null;
    for (let index = 0; index < inner.length; index++) {
        const ex = inner[index][0];
        const ey = inner[index][1];
        const shortened = shortenRay(ex, ey, desired[index][0], desired[index][1], blockers, radius);
        const dx = desired[index][0] - ex;
        const dy = desired[index][1] - ey;
        const len2 = dx * dx + dy * dy;
        if (len2 < 1e-18) ts.push(0);
        else ts.push(((shortened[0] - ex) * dx + (shortened[1] - ey) * dy) / len2);
        if (shortened[2] != null) {
            if (overlapS == null) overlapS = shortened[2];
            if (index === escapeEnd) apexS = shortened[2];
        }
    }
    const at = (index, scale) => {
        const ex = inner[index][0];
        const ey = inner[index][1];
        return [
            ex + (desired[index][0] - ex) * scale,
            ey + (desired[index][1] - ey) * scale,
        ];
    };
    for (let pass = 0; pass < 6; pass++) {
        let moved = false;
        for (let index = 0; index < inner.length - 1; index++) {
            let a = at(index, ts[index]);
            let b = at(index + 1, ts[index + 1]);
            const iex = inner[index][0];
            const iey = inner[index][1];
            const jex = inner[index + 1][0];
            const jey = inner[index + 1][1];
            if (quadHit(iex, iey, a[0], a[1], jex, jey, b[0], b[1], blockers, radius) == null) continue;
            let lo = 0;
            let hi = 1;
            let found = null;
            for (let step = 0; step < CLIP_SEARCH_STEPS; step++) {
                const mid = (lo + hi) * 0.5;
                a = at(index, ts[index] * mid);
                b = at(index + 1, ts[index + 1] * mid);
                const hit = quadHit(iex, iey, a[0], a[1], jex, jey, b[0], b[1], blockers, radius);
                if (hit == null) lo = mid;
                else {
                    hi = mid;
                    found = hit;
                }
            }
            ts[index] *= lo;
            ts[index + 1] *= lo;
            if (found != null) {
                if (overlapS == null) overlapS = found;
                if (index === escapeEnd || index + 1 === escapeEnd) apexS = found;
            }
            moved = true;
        }
        if (!moved) break;
    }
    const outerCross = (i, j) => {
        if (Math.abs(i - j) <= 1) return false;
        const a = at(i, ts[i]);
        const b = at(i + 1, ts[i + 1]);
        const c = at(j, ts[j]);
        const d = at(j + 1, ts[j + 1]);
        return segmentsCross(a[0], a[1], b[0], b[1], c[0], c[1], d[0], d[1]);
    };
    const last = inner.length - 1;
    for (let round = 0; round < inner.length; round++) {
        let cleared = true;
        for (let i = 0; i < last; i++) {
            for (let j = 0; j < i; j++) {
                if (!outerCross(i, j)) continue;
                cleared = false;
                if (ts[i] > 1e-6 || ts[i + 1] > 1e-6) {
                    ts[i] = 0;
                    ts[i + 1] = 0;
                } else {
                    ts[j] = 0;
                    ts[j + 1] = 0;
                }
            }
        }
        if (cleared) break;
    }
    if (apexS != null) overlapS = apexS;
    const outer = [];
    for (let index = 0; index < inner.length; index++) outer.push(at(index, ts[index]));
    let available = (outer[escapeEnd][0] - bx) * tx + (outer[escapeEnd][1] - by) * ty;
    if (available < 0) available = 0;
    const ring = inner.map((p) => [p[0], p[1]]);
    for (let i = outer.length - 1; i >= 0; i--) {
        const ox = outer[i][0];
        const oy = outer[i][1];
        const last = ring[ring.length - 1];
        if (last && Math.abs(last[0] - ox) < 1e-9 && Math.abs(last[1] - oy) < 1e-9) continue;
        ring.push([ox, oy]);
    }
    if (ring.length > 1) {
        const a = ring[0];
        const b = ring[ring.length - 1];
        if (Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9) ring.pop();
    }
    return {
        inner,
        outer,
        polygon: ring,
        brakeIndex,
        available_depth_m: available,
        overlap_s: overlapS,
        tx,
        ty,
        nx,
        ny,
        s0,
        s2,
    };
}

function nearestEdgeS(inner, px, py, lapM) {
    let bestS = inner[0][2];
    let bestD = 1e18;
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
            t = ((px - ax) * dx + (py - ay) * dy) / len2;
            if (t < 0) t = 0;
            else if (t > 1) t = 1;
        }
        const qx = ax + dx * t;
        const qy = ay + dy * t;
        const dist = (px - qx) * (px - qx) + (py - qy) * (py - qy);
        if (dist < bestD) {
            bestD = dist;
            const span = forward(sa, sb, lapM);
            bestS = wrap(sa + t * span, lapM);
        }
    }
    return bestS;
}

function proxySpeed(radiusM, proxyLatG) {
    const radius = radiusM > 1 ? radiusM : 1;
    return Math.sqrt(proxyLatG * GRAVITY_MPS2 * radius);
}

export function assessRunoff(x, y, distances, designZ, lapM, sampleGround, options = {}) {
    const requestedDecel = options.decelG == null ? DEFAULT_DECEL_G : Number(options.decelG);
    const decelG = requestedDecel > 0.05 ? requestedDecel : 0.05;
    const widthM = Number(options.widthM) >= 5 ? Number(options.widthM) : DEFAULT_WIDTH_M;
    const slopeLimit = options.slopeLimit ?? DEFAULT_SLOPE_LIMIT;
    const stepM = options.stepM ?? DEFAULT_STEP_M;
    const overlapGapM = options.overlapGapM ?? DEFAULT_OVERLAP_GAP_M;
    const proxyLatG = options.proxyLatG ?? PROXY_LAT_G;
    const trackWidthM = options.trackWidthM ?? 14;
    const halfWidth = trackWidthM * 0.5;
    const decel = decelG * GRAVITY_MPS2;
    const closed = options.closed !== false;
    const speedTrace = options.speedTrace || null;

    const xyIn = x.map((vx, i) => ({ x: vx, y: y[i] }));
    const analysis = analyzeCornersXY(xyIn, distances, { closed });
    const resampled = analysis.xy;
    const resampledD = analysis.distances;
    let resampleLap = resampledD.length ? resampledD[resampledD.length - 1] : lapM;
    if (!(resampleLap > 0)) resampleLap = lapM;

    const corners = [];
    const runs = analysis.runs || [];
    for (let index = 0; index < runs.length; index++) {
        const run = runs[index];
        const startM = run.startM;
        const endM = run.endM;
        const radiusM = run.radiusM;
        const traced = speedFromTrace(speedTrace, lapM, startM);
        let speed;
        let speedSource;
        if (traced == null) {
            speed = proxySpeed(radiusM, proxyLatG);
            speedSource = 'radius_proxy';
        } else {
            speed = traced;
            speedSource = 'profile';
        }
        const depth = (speed * speed) / (2 * decel);
        const sign = turnSign(resampled, resampledD, resampleLap, run.startIndex, run.endIndex);
        const [tx, ty] = approachTangent(resampled, resampledD, resampleLap, startM);
        const [nx, ny] = outsideNormal(tx, ty, sign);
        const fan = buildFan(
            resampled, resampledD, resampleLap, startM, endM, sign, halfWidth, depth, widthM,
            tx, ty, x, y, distances, overlapGapM,
        );
        const ex = tx;
        const ey = ty;
        const px = nx;
        const py = ny;
        const poly = fan.polygon;
        const inner = fan.inner;
        const zEdge = interpClosed(distances, designZ, lapM, startM);
        const raw = interiorSamples(poly, SAMPLE_SPACING_M, sampleGround);
        const usable = [];
        const edgeZ = [];
        for (const sample of raw) {
            const edgeS = nearestEdgeS(inner, sample[2], sample[3], lapM);
            const zRef = interpClosed(distances, designZ, lapM, edgeS);
            const sz = Number.isFinite(sample[4]) ? sample[4] : zRef - stepM - 1;
            usable.push([sample[0], sample[1], sample[2], sample[3], sz]);
            edgeZ.push(zRef);
        }
        const [longSlope, crossSlope] = maxSlopes(usable, ex, ey, px, py);
        let maxDrop = 0;
        let maxWall = 0;
        let cutSum = 0;
        let fillSum = 0;
        for (let s = 0; s < usable.length; s++) {
            const delta = usable[s][4] - edgeZ[s];
            if (-delta > maxDrop) maxDrop = -delta;
            if (delta > maxWall) maxWall = delta;
            if (delta > 0) cutSum += delta;
            else fillSum += -delta;
        }
        const count = usable.length;
        const area = polygonArea(poly);
        const cutM3 = count ? (cutSum / count) * area : 0;
        const fillM3 = count ? (fillSum / count) * area : 0;
        const reasons = [];
        if (crossSlope > slopeLimit) reasons.push('cross-slope');
        if (longSlope > slopeLimit) reasons.push('long-slope');
        if (maxDrop > stepM) reasons.push('drop');
        if (maxWall > stepM) reasons.push('wall');
        let overlapS = fan.overlap_s;
        let available = fan.available_depth_m;
        if (available > depth) available = depth;
        const overlap = overlapS != null;
        if (overlap) {
            const at = overlapS == null ? startM : overlapS;
            reasons.push(`overlaps track section at s=${Math.round(at)}`);
        }
        const terrainFail = reasons.some((r) => !r.startsWith('overlaps'));
        corners.push({
            index,
            position_m: run.positionM,
            radius_m: radiusM,
            turn_in_m: startM,
            turn_sign: sign,
            speed_mps: speed,
            speed_source: speedSource,
            depth_m: depth,
            available_depth_m: available,
            width_m: widthM,
            passed: reasons.length === 0,
            reasons,
            max_cross_slope: crossSlope,
            max_long_slope: longSlope,
            max_drop_m: maxDrop,
            max_wall_m: maxWall,
            overlap,
            overlap_s: overlapS == null ? null : overlapS,
            cut_m3: cutM3,
            fill_m3: fillM3,
            earthworks_m3: terrainFail ? cutM3 + fillM3 : 0,
            z_edge_m: zEdge,
            area_m2: area,
            polygon: poly,
            inner,
            outer: fan.outer,
        });
    }

    const passCount = corners.filter((c) => c.passed).length;
    let decelLabel = 'custom';
    if (Math.abs(decelG - DEFAULT_DECEL_G) < 0.05) decelLabel = 'gravel';
    else if (Math.abs(decelG - TARMAC_DECEL_G) < 0.05) decelLabel = 'tarmac';
    return {
        estimate: 'starting estimate, not an FIA figure',
        decel_g: decelG,
        decel_label: decelLabel,
        width_m: widthM,
        slope_limit: slopeLimit,
        step_m: stepM,
        overlap_gap_m: overlapGapM,
        corners,
        pass_count: passCount,
        corner_count: corners.length,
        summary: `${passCount}/${corners.length} corners pass`,
    };
}
