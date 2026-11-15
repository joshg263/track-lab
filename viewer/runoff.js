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
export const OVERLAP_MARGIN_M = 0.05;

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

function zoneOverlaps(poly, trackX, trackY, trackD, lapM, startM, endM, halfWidth, overlapGapM) {
    const limit = halfWidth - OVERLAP_MARGIN_M;
    const n = trackD.length;
    for (let i = 0; i < n; i++) {
        const gap = chainageGap(trackD[i], startM, endM, lapM);
        if (gap <= overlapGapM) continue;
        const dist = distPointPolygon(trackX[i], trackY[i], poly);
        if (dist < limit) return true;
    }
    return false;
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
        const [ex, ey] = escapeDir(tx, ty, sign);
        const [cx, cy] = sampleXY(resampled, resampledD, resampleLap, startM);
        const ax = cx + nx * halfWidth;
        const ay = cy + ny * halfWidth;
        const bx = ax + tx * widthM;
        const by = ay + ty * widthM;
        const dx = ax + ex * depth;
        const dy = ay + ey * depth;
        const cpx = bx + ex * depth;
        const cpy = by + ey * depth;
        const poly = [[ax, ay], [bx, by], [cpx, cpy], [dx, dy]];

        const zEdge = interpClosed(distances, designZ, lapM, startM);
        const raw = interiorSamples(poly, SAMPLE_SPACING_M, sampleGround);
        const usable = [];
        const edgeZ = [];
        for (const sample of raw) {
            const zRef = roadEdgeZ(
                distances, designZ, lapM, poly, sample[2], sample[3], startM, widthM,
            );
            const sz = Number.isFinite(sample[4]) ? sample[4] : zRef - stepM - 1;
            usable.push([sample[0], sample[1], sample[2], sample[3], sz]);
            edgeZ.push(zRef);
        }
        const px = -ey;
        const py = ex;
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
        const overlap = zoneOverlaps(
            poly, x, y, distances, lapM, startM, endM, halfWidth, overlapGapM,
        );
        if (overlap) reasons.push('overlap');
        const terrainFail = reasons.some((r) => r !== 'overlap');
        corners.push({
            index,
            position_m: run.positionM,
            radius_m: radiusM,
            turn_in_m: startM,
            turn_sign: sign,
            speed_mps: speed,
            speed_source: speedSource,
            depth_m: depth,
            width_m: widthM,
            passed: reasons.length === 0,
            reasons,
            max_cross_slope: crossSlope,
            max_long_slope: longSlope,
            max_drop_m: maxDrop,
            max_wall_m: maxWall,
            overlap,
            cut_m3: cutM3,
            fill_m3: fillM3,
            earthworks_m3: terrainFail ? cutM3 + fillM3 : 0,
            z_edge_m: zEdge,
            area_m2: area,
            polygon: poly,
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
