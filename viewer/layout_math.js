// Corner and gradient maths for the layout viewer.
// Line-for-line port of scripts/corner_detection.py and scripts/road_profile.py
// (and the centripetal Catmull-Rom + chainage used by scripts/layout_check.py).
// `make layout-math-verify` runs this module under Node and checks it agrees
// with the Python checker. Change both together.

export const RESAMPLE_SPACING_M = 5.0;
export const HALF_WINDOW_M = 20.0;
export const RADIUS_THRESHOLD_M = 175.0;
export const MERGE_GAP_M = 30.0;
export const MIN_RUN_LENGTH_M = 20.0;

export const ROAD_PROFILE_SMOOTH_M = 15.0;
export const GRADIENT_WINDOW_M = 100.0;
export const SPLINE_SAMPLE_SPACING_M = 2.0;
export const MAX_GRADIENT_PCT = 12;
export const LAP_LENGTH_MIN_M = 3500;
export const LAP_LENGTH_MAX_M = 4500;
export const MIN_CORNER_RADIUS_M = 15;

function mod(d, m) {
    return ((d % m) + m) % m;
}

// NumPy searchsorted side='left' / 'right'.
export function searchSorted(arr, value, side = 'left') {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (side === 'right' ? arr[mid] > value : arr[mid] >= value) hi = mid;
        else lo = mid + 1;
    }
    return lo;
}

// NumPy arange for a positive step, exclusive of stop, including the length correction
// NumPy applies when the last sample would land on or beyond stop.
export function npArange(start, stop, step) {
    if (!(step > 0) || !(stop > start)) return [];
    let n = Math.ceil((stop - start) / step);
    if (n > 0) {
        const last = start + (n - 1) * step;
        if (last >= stop) n--;
    }
    const out = new Array(Math.max(0, n));
    for (let i = 0; i < out.length; i++) out[i] = start + i * step;
    return out;
}

// NumPy interp for a scalar x and increasing xp.
function interp(x, xp, fp) {
    const n = xp.length;
    if (x <= xp[0]) return fp[0];
    if (x >= xp[n - 1]) return fp[n - 1];
    const i1 = searchSorted(xp, x, 'left');
    const i0 = i1 - 1;
    const span = xp[i1] - xp[i0];
    const t = span === 0 ? 0 : (x - xp[i0]) / span;
    return fp[i0] * (1 - t) + fp[i1] * t;
}

// scipy.ndimage gaussian_filter1d, mode='reflect', truncate=4.
// Reflect duplicates the edge sample: index -1 maps to 0, index n maps to n-1.
function reflectIndex(i, n) {
    if (n <= 1) return 0;
    const period = 2 * n;
    i = i % period;
    if (i < 0) i += period;
    if (i >= n) i = period - i - 1;
    return i;
}

export function gaussianFilter1d(input, sigma, truncate = 4.0) {
    const n = input.length;
    const out = new Array(n);
    if (n === 0) return out;
    const radius = Math.trunc(truncate * sigma + 0.5);
    const sigma2 = sigma * sigma;
    const phi = new Array(2 * radius + 1);
    let sum = 0;
    for (let k = -radius; k <= radius; k++) {
        const w = Math.exp(-0.5 / sigma2 * k * k);
        phi[k + radius] = w;
        sum += w;
    }
    for (let k = 0; k < phi.length; k++) phi[k] /= sum;
    for (let i = 0; i < n; i++) {
        let acc = 0;
        for (let k = -radius; k <= radius; k++) {
            acc += input[reflectIndex(i + k, n)] * phi[k + radius];
        }
        out[i] = acc;
    }
    return out;
}

export function smoothRoadProfile(elevations, sampleSpacingM, smoothSigmaM = ROAD_PROFILE_SMOOTH_M) {
    const sigmaSamples = Math.max(smoothSigmaM / Math.max(sampleSpacingM, 1e-6), 0.5);
    return gaussianFilter1d(elevations, sigmaSamples);
}

export function meanSampleSpacing(distances, closedTotalLength = null) {
    if (distances.length < 2) return 5.0;
    if (closedTotalLength != null && closedTotalLength > 0) {
        return closedTotalLength / distances.length;
    }
    let sum = 0;
    let count = 0;
    for (let i = 1; i < distances.length; i++) {
        const d = distances[i] - distances[i - 1];
        if (d > 0) {
            sum += d;
            count++;
        }
    }
    return count ? sum / count : 5.0;
}

export function computeLongitudinalGradients(distances, elevations, windowM = GRADIENT_WINDOW_M, closedTotalLength = null) {
    const n = distances.length;
    const gradients = new Array(n).fill(0);
    const half = windowM / 2;
    if (closedTotalLength != null && closedTotalLength > 0) {
        const distExt = new Array(n * 3);
        const elevExt = new Array(n * 3);
        for (let i = 0; i < n; i++) {
            distExt[i] = distances[i] - closedTotalLength;
            elevExt[i] = elevations[i];
            distExt[n + i] = distances[i];
            elevExt[n + i] = elevations[i];
            distExt[2 * n + i] = distances[i] + closedTotalLength;
            elevExt[2 * n + i] = elevations[i];
        }
        for (let i = 0; i < n; i++) {
            const i0 = searchSorted(distExt, distances[i] - half, 'left');
            let i1 = searchSorted(distExt, distances[i] + half, 'left');
            if (i1 > distExt.length - 1) i1 = distExt.length - 1;
            if (i1 > i0) {
                const deltaDist = distExt[i1] - distExt[i0];
                if (deltaDist > 0) gradients[i] = (elevExt[i1] - elevExt[i0]) / deltaDist * 100;
            }
        }
        return gradients;
    }
    const total = n ? distances[n - 1] : 0;
    for (let i = 0; i < n; i++) {
        const d0 = Math.max(0, distances[i] - half);
        const d1 = Math.min(total, distances[i] + half);
        const i0 = searchSorted(distances, d0, 'left');
        let i1 = searchSorted(distances, d1, 'left');
        if (i1 > n - 1) i1 = n - 1;
        if (i1 > i0) {
            const deltaDist = distances[i1] - distances[i0];
            if (deltaDist > 0) gradients[i] = (elevations[i1] - elevations[i0]) / deltaDist * 100;
        }
    }
    return gradients;
}

export function gradeProfile(distances, elevationsRaw, sampleSpacingM, options = {}) {
    const smoothSigmaM = options.smoothSigmaM ?? ROAD_PROFILE_SMOOTH_M;
    const windowM = options.windowM ?? GRADIENT_WINDOW_M;
    const closedTotalLength = options.closedTotalLength ?? null;
    const graded = smoothRoadProfile(elevationsRaw, sampleSpacingM, smoothSigmaM);
    const gradients = computeLongitudinalGradients(distances, graded, windowM, closedTotalLength);
    return { graded, gradients };
}

export function maxAbsGradientPct(gradients) {
    let max = 0;
    for (let i = 0; i < gradients.length; i++) {
        const a = Math.abs(gradients[i]);
        if (a > max) max = a;
    }
    return max;
}

// Centripetal Catmull-Rom. Matches layout_check.catmull_rom_spline (floor samples, endpoint=False).
export function catmullRomSpline(points, numSamples, closed = true) {
    const n = points.length;
    if (n < 2) return points.map(p => ({ x: p.x, y: p.y }));
    const pts = closed
        ? [points[n - 1], ...points, points[0], points[1 % n]]
        : [points[0], ...points, points[n - 1]];
    const numSegments = closed ? n : n - 1;
    const samplesPerSegment = Math.floor(numSamples / numSegments);
    const result = [];
    const tj = (ti, pi, pj) => ti + Math.sqrt(Math.hypot(pj.x - pi.x, pj.y - pi.y));
    for (let i = 0; i < numSegments; i++) {
        const p0 = pts[i];
        const p1 = pts[i + 1];
        const p2 = pts[i + 2];
        const p3 = pts[i + 3];
        const t0 = 0;
        const t1 = tj(t0, p0, p1);
        const t2 = tj(t1, p1, p2);
        const t3 = tj(t2, p2, p3);
        if (samplesPerSegment <= 0) continue;
        const step = (t2 - t1) / samplesPerSegment;
        for (let s = 0; s < samplesPerSegment; s++) {
            const t = t1 + step * s;
            const a1x = (t1 - t) / (t1 - t0 + 1e-10) * p0.x + (t - t0) / (t1 - t0 + 1e-10) * p1.x;
            const a1y = (t1 - t) / (t1 - t0 + 1e-10) * p0.y + (t - t0) / (t1 - t0 + 1e-10) * p1.y;
            const a2x = (t2 - t) / (t2 - t1 + 1e-10) * p1.x + (t - t1) / (t2 - t1 + 1e-10) * p2.x;
            const a2y = (t2 - t) / (t2 - t1 + 1e-10) * p1.y + (t - t1) / (t2 - t1 + 1e-10) * p2.y;
            const a3x = (t3 - t) / (t3 - t2 + 1e-10) * p2.x + (t - t2) / (t3 - t2 + 1e-10) * p3.x;
            const a3y = (t3 - t) / (t3 - t2 + 1e-10) * p2.y + (t - t2) / (t3 - t2 + 1e-10) * p3.y;
            const b1x = (t2 - t) / (t2 - t0 + 1e-10) * a1x + (t - t0) / (t2 - t0 + 1e-10) * a2x;
            const b1y = (t2 - t) / (t2 - t0 + 1e-10) * a1y + (t - t0) / (t2 - t0 + 1e-10) * a2y;
            const b2x = (t3 - t) / (t3 - t1 + 1e-10) * a2x + (t - t1) / (t3 - t1 + 1e-10) * a3x;
            const b2y = (t3 - t) / (t3 - t1 + 1e-10) * a2y + (t - t1) / (t3 - t1 + 1e-10) * a3y;
            const cx = (t2 - t) / (t2 - t1 + 1e-10) * b1x + (t - t1) / (t2 - t1 + 1e-10) * b2x;
            const cy = (t2 - t) / (t2 - t1 + 1e-10) * b1y + (t - t1) / (t2 - t1 + 1e-10) * b2y;
            result.push({ x: cx, y: cy });
        }
    }
    return result;
}

export function chordLengthClosed(points) {
    let total = 0;
    for (let i = 0; i < points.length; i++) {
        const j = (i + 1) % points.length;
        total += Math.hypot(points[j].x - points[i].x, points[j].y - points[i].y);
    }
    return total;
}

// Chainage matching layout_check.interpolate_layout: distances exclude the closing segment.
export function chainageClosed(xy) {
    const n = xy.length;
    const distances = new Array(n);
    let acc = 0;
    let lap = 0;
    for (let i = 0; i < n; i++) {
        distances[i] = acc;
        const j = (i + 1) % n;
        const seg = Math.hypot(xy[j].x - xy[i].x, xy[j].y - xy[i].y);
        acc += seg;
        lap += seg;
    }
    return { distances, lapLength: lap };
}

export function resamplePolylineXY(xy, distances, spacingM = RESAMPLE_SPACING_M, closed = false) {
    const n = xy.length;
    if (n < 2) {
        return {
            xy: xy.map(p => ({ x: p.x, y: p.y })),
            distances: distances.slice(),
        };
    }
    const seg = [];
    for (let i = 0; i < n - 1; i++) {
        seg.push(Math.hypot(xy[i + 1].x - xy[i].x, xy[i + 1].y - xy[i].y));
    }
    if (closed) seg.push(Math.hypot(xy[0].x - xy[n - 1].x, xy[0].y - xy[n - 1].y));
    const cumulative = new Array(seg.length + 1);
    cumulative[0] = 0;
    for (let i = 0; i < seg.length; i++) cumulative[i + 1] = cumulative[i] + seg[i];
    const total = cumulative[cumulative.length - 1];
    if (total < spacingM) {
        return {
            xy: xy.map(p => ({ x: p.x, y: p.y })),
            distances: distances.slice(),
        };
    }
    const newD = npArange(0, total, spacingM);
    if (newD.length === 0 || newD[newD.length - 1] < total - 1e-6) newD.push(total);
    const xpX = xy.map(p => p.x);
    const xpY = xy.map(p => p.y);
    if (closed) {
        xpX.push(xy[0].x);
        xpY.push(xy[0].y);
    }
    const newXY = newD.map(d => ({ x: interp(d, cumulative, xpX), y: interp(d, cumulative, xpY) }));
    return { xy: newXY, distances: newD };
}

export function computeCurvatureXY(x, y, distances, halfWindowM = HALF_WINDOW_M, loopLength = null) {
    const n = x.length;
    const curvature = new Array(n).fill(0);
    const sampleXY = (d) => {
        let dd = d;
        if (loopLength != null && loopLength > 0) dd = mod(d, loopLength);
        let idx = searchSorted(distances, dd, 'right') - 1;
        if (idx < 0) idx = 0;
        if (idx > n - 1) idx = n - 1;
        const idx1 = Math.min(idx + 1, n - 1);
        const d0 = distances[idx];
        const d1 = distances[idx1];
        const t = (idx1 === idx || d1 <= d0 + 1e-9) ? 0 : (dd - d0) / (d1 - d0);
        return [x[idx] + t * (x[idx1] - x[idx]), y[idx] + t * (y[idx1] - y[idx])];
    };
    for (let i = 0; i < n; i++) {
        let dBack = distances[i] - halfWindowM;
        let dFwd = distances[i] + halfWindowM;
        if (loopLength != null && loopLength > 0) {
            dBack = mod(dBack, loopLength);
            dFwd = mod(dFwd, loopLength);
        } else {
            dBack = Math.max(dBack, distances[0]);
            dFwd = Math.min(dFwd, distances[n - 1]);
        }
        if (Math.abs(dFwd - dBack) < 1e-6) continue;
        const [x1, y1] = sampleXY(dBack);
        const x2 = x[i];
        const y2 = y[i];
        const [x3, y3] = sampleXY(dFwd);
        const a = Math.hypot(x2 - x1, y2 - y1);
        const b = Math.hypot(x3 - x2, y3 - y2);
        const c = Math.hypot(x3 - x1, y3 - y1);
        if (a < 1e-6 || b < 1e-6 || c < 1e-6) continue;
        const s = (a + b + c) / 2;
        const areaSq = s * (s - a) * (s - b) * (s - c);
        if (areaSq <= 0) continue;
        const radius = (a * b * c) / (4 * Math.sqrt(areaSq));
        if (radius > 1e-6) curvature[i] = 1 / radius;
    }
    return curvature;
}

export function radiiFromCurvature(curvature) {
    return curvature.map(k => (k > 1e-10 ? 1 / k : Infinity));
}

export function findCorners(curvature, distances, options = {}) {
    const radiusThreshold = options.radiusThresholdM ?? RADIUS_THRESHOLD_M;
    const mergeGap = options.mergeGapM ?? MERGE_GAP_M;
    const minRun = options.minRunLengthM ?? MIN_RUN_LENGTH_M;
    const radius = radiiFromCurvature(curvature);
    const n = curvature.length;
    const runs = [];
    let inRun = false;
    let runStart = 0;
    for (let i = 0; i < n; i++) {
        if (radius[i] < radiusThreshold) {
            if (!inRun) {
                inRun = true;
                runStart = i;
            }
        } else if (inRun) {
            runs.push([runStart, i - 1]);
            inRun = false;
        }
    }
    if (inRun) runs.push([runStart, n - 1]);
    if (!runs.length) return [];
    const merged = [[runs[0][0], runs[0][1]]];
    for (let r = 1; r < runs.length; r++) {
        const start = runs[r][0];
        const end = runs[r][1];
        const prev = merged[merged.length - 1];
        const gap = distances[start] - distances[prev[1]];
        if (gap < mergeGap) prev[1] = end;
        else merged.push([start, end]);
    }
    const corners = [];
    for (const [start, end] of merged) {
        if (distances[end] - distances[start] < minRun) continue;
        let minRadius = Infinity;
        for (let i = start; i <= end; i++) {
            if (radius[i] < minRadius) minRadius = radius[i];
        }
        if (!(minRadius < Infinity)) continue;
        corners.push({
            positionM: (distances[start] + distances[end]) / 2,
            radiusM: minRadius,
        });
    }
    corners.sort((a, b) => a.positionM - b.positionM);
    return corners;
}

export function analyzeCornersXY(xy, distances, options = {}) {
    const spacingM = options.spacingM ?? RESAMPLE_SPACING_M;
    const closed = options.closed ?? false;
    const n = xy.length;
    const closing = closed && n > 1 ? Math.hypot(xy[0].x - xy[n - 1].x, xy[0].y - xy[n - 1].y) : 0;
    const loopLength = closed ? distances[distances.length - 1] + closing : null;
    const resampled = resamplePolylineXY(xy, distances, spacingM, closed);
    const x = resampled.xy.map(p => p.x);
    const y = resampled.xy.map(p => p.y);
    const curvature = computeCurvatureXY(x, y, resampled.distances, HALF_WINDOW_M, loopLength);
    const radius = radiiFromCurvature(curvature);
    let minRadiusM = null;
    for (const r of radius) {
        if (r < Infinity && (minRadiusM == null || r < minRadiusM)) minRadiusM = r;
    }
    const corners = findCorners(curvature, resampled.distances);
    const cornerRadii = corners.map(c => c.radiusM);
    return {
        xy: resampled.xy,
        distances: resampled.distances,
        curvature,
        radius,
        corners,
        cornersDict: corners.map(c => ({ position_m: c.positionM, radius_m: c.radiusM })),
        cornerCount: corners.length,
        minRadiusM,
        minCornerRadiusM: cornerRadii.length ? Math.min(...cornerRadii) : null,
    };
}

// Local-metre control points -> draped centreline metrics (layout_check.check_layout).
export function analyzeLayout(controlPoints, sampleZ) {
    const approx = chordLengthClosed(controlPoints);
    const numSamples = Math.max(100, Math.trunc(approx / SPLINE_SAMPLE_SPACING_M));
    const xy = catmullRomSpline(controlPoints, numSamples, true);
    const z = xy.map(p => sampleZ(p.x, p.y));
    const { distances, lapLength } = chainageClosed(xy);
    const cornerAnalysis = analyzeCornersXY(xy, distances, { closed: true });
    const spacing = meanSampleSpacing(distances, lapLength);
    const { gradients } = gradeProfile(distances, z, spacing, { closedTotalLength: lapLength });
    const maxGradientPct = maxAbsGradientPct(gradients);
    const points = xy.map((p, i) => ({ x: p.x, y: p.y, z: z[i], distance: distances[i] }));
    return {
        points,
        lapLengthM: lapLength,
        numCorners: cornerAnalysis.cornerCount,
        minRadiusM: cornerAnalysis.minRadiusM,
        minCornerRadiusM: cornerAnalysis.minCornerRadiusM,
        corners: cornerAnalysis.cornersDict,
        maxGradientPct,
        maxGradientPass: maxGradientPct <= MAX_GRADIENT_PCT,
        sampleSpacingM: spacing,
    };
}

// Open centreline (Red Bull Ring analysis): lon/lat + geodesic chainage + raw elevations.
export function analyzeOpenLonLat(lons, lats, distances, elevations) {
    const n = lons.length;
    let latSum = 0;
    let lonSum = 0;
    for (let i = 0; i < n; i++) {
        latSum += lats[i];
        lonSum += lons[i];
    }
    const centerLat = latSum / n;
    const centerLon = lonSum / n;
    const latScale = 111320;
    const lonScale = 111320 * Math.cos(centerLat * Math.PI / 180);
    const x = new Array(n);
    const y = new Array(n);
    for (let i = 0; i < n; i++) {
        x[i] = (lons[i] - centerLon) * lonScale;
        y[i] = (lats[i] - centerLat) * latScale;
    }
    const curvature = computeCurvatureXY(x, y, distances, HALF_WINDOW_M, null);
    const corners = findCorners(curvature, distances);
    const spacing = meanSampleSpacing(distances, null);
    const { gradients } = gradeProfile(distances, elevations, spacing, { closedTotalLength: null });
    let maxUp = -Infinity;
    let maxDown = Infinity;
    for (const g of gradients) {
        if (g > maxUp) maxUp = g;
        if (g < maxDown) maxDown = g;
    }
    return {
        cornerCount: corners.length,
        minCornerRadiusM: corners.length ? Math.min(...corners.map(c => c.radiusM)) : null,
        corners: corners.map(c => ({ position_m: c.positionM, radius_m: c.radiusM })),
        maxUphillGradientPct: maxUp,
        maxDownhillGradientPct: maxDown,
        maxGradientPct: maxAbsGradientPct(gradients),
        sampleSpacingM: spacing,
    };
}

export function makeDemSampler(terrain, frame) {
    const { heights, rows, cols, left, top, resX, resY } = terrain;
    const { lon0, lat0, sx, sy } = frame;
    return function sampleZ(x, y) {
        const lon = lon0 + x / sx;
        const lat = lat0 + y / sy;
        let col = (lon - left) / resX - 0.5;
        let row = (top - lat) / resY - 0.5;
        if (col < 0) col = 0;
        else if (col > cols - 1.001) col = cols - 1.001;
        if (row < 0) row = 0;
        else if (row > rows - 1.001) row = rows - 1.001;
        let c0 = Math.floor(col);
        let r0 = Math.floor(row);
        if (c0 > cols - 2) c0 = cols - 2;
        if (r0 > rows - 2) r0 = rows - 2;
        const dc = col - c0;
        const dr = row - r0;
        const h = heights;
        const w = cols;
        return h[r0 * w + c0] * (1 - dc) * (1 - dr) +
            h[r0 * w + c0 + 1] * dc * (1 - dr) +
            h[(r0 + 1) * w + c0] * (1 - dc) * dr +
            h[(r0 + 1) * w + c0 + 1] * dc * dr;
    };
}
