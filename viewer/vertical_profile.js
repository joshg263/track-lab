// Designed vertical profile with limited cut and fill.
// Line-for-line port of scripts/vertical_profile.py. Change both together.
// The grid is periodic, so the profile agrees with itself at s = 0 and s = L.

export const PROFILE_SPACING_M = 4.0;
export const PROFILE_MIN_SAMPLES = 64;
export const PROFILE_MAX_SAMPLES = 2048;

export const DEFAULT_MAX_CUT_FILL_M = 6.0;
export const DEFAULT_MIN_CREST_RADIUS_M = 1000.0;
export const DEFAULT_MIN_DIP_RADIUS_M = 600.0;
export const LIMIT_TIGHTEN = 1.04;

export const ROUNDS = 25;
export const CURV_REPS = 25;
export const GRAD_REPS = 40;
export const PULL_STEPS = 8;
export const PULL_ALPHA = 0.35;
export const FINAL_CURV_REPS = 60;

export const SIDE_SLOPE = 0.5;
export const CATCH_STEP_M = 2.0;
export const MAX_CATCH_M = 80.0;

export const GRADIENT_MARGIN = 0.0015;
export const RADIUS_FRACTION = 0.98;
export const DEPTH_MARGIN_M = 0.005;
export const DEPTH_ACTIVE_M = 0.05;

export function sampleCountForLap(lapM) {
    let n = Math.floor(lapM / PROFILE_SPACING_M + 0.5);
    if (n < PROFILE_MIN_SAMPLES) n = PROFILE_MIN_SAMPLES;
    if (n > PROFILE_MAX_SAMPLES) n = PROFILE_MAX_SAMPLES;
    return n;
}

function searchHi(xp, x) {
    let lo = 0;
    let hi = xp.length - 1;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (xp[mid] <= x) lo = mid;
        else hi = mid;
    }
    return [lo, hi];
}

export function interpClosed(distances, values, lapM, sampleAt) {
    const xp = distances.concat([lapM]);
    const fp = values.concat([values[0]]);
    const out = new Array(sampleAt.length);
    for (let k = 0; k < sampleAt.length; k++) {
        const x = sampleAt[k];
        if (x <= xp[0]) {
            out[k] = fp[0];
            continue;
        }
        if (x >= xp[xp.length - 1]) {
            out[k] = fp[fp.length - 1];
            continue;
        }
        const [lo, hi] = searchHi(xp, x);
        const span = xp[hi] - xp[lo];
        const u = span === 0 ? 0 : (x - xp[lo]) / span;
        out[k] = fp[lo] * (1 - u) + fp[hi] * u;
    }
    return out;
}

export function resampleClosed(distances, values, lapM, sampleCount) {
    const n = sampleCount;
    const spacing = lapM / n;
    const stations = new Array(n);
    for (let i = 0; i < n; i++) stations[i] = i * spacing;
    return {
        stations,
        values: interpClosed(distances, values, lapM, stations),
        spacing,
    };
}

function curvaturePass(z, boxLo, boxHi, cLo, cHi) {
    const n = z.length;
    const zp = new Array(n);
    const zm = new Array(n);
    for (let i = 0; i < n - 1; i++) zp[i] = z[i + 1];
    zp[n - 1] = z[0];
    for (let i = 1; i < n; i++) zm[i] = z[i - 1];
    zm[0] = z[n - 1];
    const target = new Array(n);
    for (let i = 0; i < n; i++) {
        const loC = (zp[i] + zm[i] - cLo) * 0.5;
        const hiC = (zp[i] + zm[i] + cHi) * 0.5;
        const lo = loC > boxLo[i] ? loC : boxLo[i];
        const hi = hiC < boxHi[i] ? hiC : boxHi[i];
        let value = z[i];
        if (value < lo) value = lo;
        if (value > hi) value = hi;
        if (hiC < boxLo[i]) value = boxLo[i];
        if (loC > boxHi[i]) value = boxHi[i];
        target[i] = value;
    }
    return target;
}

function gradientPass(z, boxLo, boxHi, limit) {
    const n = z.length;
    for (let i = 0; i < n; i++) {
        const j = i + 1 === n ? 0 : i + 1;
        const dz = z[j] - z[i];
        const ad = dz >= 0 ? dz : -dz;
        if (ad <= limit) continue;
        const sign = dz > 0 ? 1 : -1;
        const excess = ad - limit;
        let roomI;
        let roomJ;
        if (sign > 0) {
            roomI = boxHi[i] - z[i];
            roomJ = z[j] - boxLo[j];
        } else {
            roomI = z[i] - boxLo[i];
            roomJ = boxHi[j] - z[j];
        }
        if (roomI < 0) roomI = 0;
        if (roomJ < 0) roomJ = 0;
        let giveI = excess * 0.5;
        if (giveI > roomI) giveI = roomI;
        let giveJ = excess - giveI;
        if (giveJ > roomJ) giveJ = roomJ;
        let left = excess - giveI - giveJ;
        if (left > 0) {
            let extra = roomI - giveI;
            if (extra < 0) extra = 0;
            if (extra > left) extra = left;
            giveI += extra;
            left = excess - giveI - giveJ;
            extra = roomJ - giveJ;
            if (extra < 0) extra = 0;
            if (extra > left) extra = left;
            giveJ += extra;
        }
        z[i] += sign * giveI;
        z[j] -= sign * giveJ;
    }
}

function clipBox(z, boxLo, boxHi) {
    const out = new Array(z.length);
    for (let i = 0; i < z.length; i++) {
        let v = z[i];
        if (v < boxLo[i]) v = boxLo[i];
        if (v > boxHi[i]) v = boxHi[i];
        out[i] = v;
    }
    return out;
}

export function fitProfile(terrainZ, spacingM, options = {}) {
    const terrain = terrainZ;
    const n = terrain.length;
    if (n < 4) throw new Error('profile needs at least 4 stations');
    if (!(spacingM > 0)) throw new Error('spacing must be positive');
    const depth = options.maxCutFillM ?? DEFAULT_MAX_CUT_FILL_M;
    const crestR = options.minCrestRadiusM ?? DEFAULT_MIN_CREST_RADIUS_M;
    const dipR = options.minDipRadiusM ?? DEFAULT_MIN_DIP_RADIUS_M;
    const maxGrade = options.maxGrade;
    if (!(depth >= 0)) throw new Error('cut/fill cap must be non-negative');
    if (!(crestR > 0) || !(dipR > 0)) throw new Error('vertical-curve radii must be positive');
    const gradeLimit = maxGrade / LIMIT_TIGHTEN;
    if (!(gradeLimit >= 0)) throw new Error('max grade must be non-negative');
    const kc = 1 / (crestR * LIMIT_TIGHTEN);
    const kd = 1 / (dipR * LIMIT_TIGHTEN);
    const ds = spacingM;
    const limit = gradeLimit * ds;
    const cLo = kd * ds * ds;
    const cHi = kc * ds * ds;
    const boxLo = new Array(n);
    const boxHi = new Array(n);
    for (let i = 0; i < n; i++) {
        boxLo[i] = terrain[i] - depth;
        boxHi[i] = terrain[i] + depth;
    }
    let z = terrain.slice();

    const curvature = (reps) => {
        for (let r = 0; r < reps; r++) z = curvaturePass(z, boxLo, boxHi, cLo, cHi);
    };
    const gradient = (reps) => {
        for (let r = 0; r < reps; r++) gradientPass(z, boxLo, boxHi, limit);
    };

    for (let r = 0; r < ROUNDS; r++) {
        curvature(CURV_REPS);
        gradient(GRAD_REPS);
    }
    for (let s = 0; s < PULL_STEPS; s++) {
        const pulled = new Array(n);
        for (let i = 0; i < n; i++) pulled[i] = z[i] + PULL_ALPHA * (terrain[i] - z[i]);
        z = clipBox(pulled, boxLo, boxHi);
        curvature(CURV_REPS);
        gradient(GRAD_REPS);
    }
    curvature(FINAL_CURV_REPS);
    gradient(GRAD_REPS);
    curvature(FINAL_CURV_REPS);
    z = clipBox(z, boxLo, boxHi);
    return z;
}

export function stationGeometry(z, spacingM) {
    const n = z.length;
    const ds = spacingM;
    const forward = new Array(n);
    const centred = new Array(n);
    const curvature = new Array(n);
    const radiusM = new Array(n);
    for (let i = 0; i < n; i++) {
        const nxt = i + 1 === n ? 0 : i + 1;
        const prv = i === 0 ? n - 1 : i - 1;
        forward[i] = (z[nxt] - z[i]) / ds;
        centred[i] = (z[nxt] - z[prv]) / (2 * ds);
        curvature[i] = (z[nxt] - 2 * z[i] + z[prv]) / (ds * ds);
        const g = centred[i];
        radiusM[i] = (1 + g * g) ** 1.5 / Math.max(Math.abs(curvature[i]), 1e-12);
    }
    return { forwardGrade: forward, centredGrade: centred, curvature, radiusM };
}

function runsFromMask(mask, distance, values, limit, kind, status, peak) {
    const n = mask.length;
    let any = false;
    let all = n > 0;
    for (let i = 0; i < n; i++) {
        if (mask[i]) any = true;
        else all = false;
    }
    if (!any) return [];
    if (all) {
        let idx = 0;
        if (peak === 'min') {
            for (let i = 1; i < n; i++) if (values[i] < values[idx]) idx = i;
        } else {
            for (let i = 1; i < n; i++) if (Math.abs(values[i]) > Math.abs(values[idx])) idx = i;
        }
        return [{
            kind,
            status,
            start_m: 0,
            end_m: distance[n - 1],
            wraps: true,
            peak: values[idx],
            peak_at_m: distance[idx],
            limit,
            samples: n,
        }];
    }
    let start0 = 0;
    for (let i = 0; i < n; i++) {
        if (!mask[i]) {
            start0 = (i + 1) % n;
            break;
        }
    }
    const runs = [];
    let i = start0;
    let seen = 0;
    while (seen < n) {
        if (mask[i]) {
            const a = i;
            let count = 0;
            while (mask[i] && count < n) {
                i = (i + 1) % n;
                count += 1;
                seen += 1;
            }
            const b = (i - 1 + n) % n;
            const idx = [];
            if (a <= b) {
                for (let k = a; k <= b; k++) idx.push(k);
            } else {
                for (let k = a; k < n; k++) idx.push(k);
                for (let k = 0; k <= b; k++) idx.push(k);
            }
            let local = idx[0];
            if (peak === 'min') {
                for (const k of idx) if (values[k] < values[local]) local = k;
            } else {
                for (const k of idx) if (Math.abs(values[k]) > Math.abs(values[local])) local = k;
            }
            runs.push({
                kind,
                status,
                start_m: distance[a],
                end_m: distance[b],
                wraps: a > b,
                peak: values[local],
                peak_at_m: distance[local],
                limit,
                samples: idx.length,
            });
        } else {
            i = (i + 1) % n;
            seen += 1;
        }
    }
    return runs;
}

export function bindingReport(terrainZ, designZ, spacingM, options) {
    const n = designZ.length;
    const ds = spacingM;
    const distance = new Array(n);
    for (let i = 0; i < n; i++) distance[i] = i * ds;
    const geom = stationGeometry(designZ, ds);
    const grade = geom.forwardGrade;
    const curvature = geom.curvature;
    const radius = geom.radiusM;
    const maxGrade = options.maxGrade;
    const cap = options.maxCutFillM;
    const crestLim = options.minCrestRadiusM;
    const dipLim = options.minDipRadiusM;
    const gradeBad = new Array(n);
    const crestBad = new Array(n);
    const dipBad = new Array(n);
    const depthBad = new Array(n);
    const depthActive = new Array(n);
    const depth = new Array(n);
    for (let i = 0; i < n; i++) {
        depth[i] = designZ[i] - terrainZ[i];
        gradeBad[i] = Math.abs(grade[i]) > maxGrade + GRADIENT_MARGIN;
        crestBad[i] = curvature[i] < -1e-8 && radius[i] < crestLim * RADIUS_FRACTION;
        dipBad[i] = curvature[i] > 1e-8 && radius[i] < dipLim * RADIUS_FRACTION;
        depthBad[i] = Math.abs(depth[i]) > cap + DEPTH_MARGIN_M;
        depthActive[i] = Math.abs(depth[i]) >= cap - DEPTH_ACTIVE_M && !depthBad[i];
    }
    return [
        ...runsFromMask(gradeBad, distance, grade, maxGrade, 'gradient', 'violated', 'abs'),
        ...runsFromMask(crestBad, distance, radius, crestLim, 'crest_radius', 'violated', 'min'),
        ...runsFromMask(dipBad, distance, radius, dipLim, 'dip_radius', 'violated', 'min'),
        ...runsFromMask(depthBad, distance, depth, cap, 'depth', 'violated', 'abs'),
        ...runsFromMask(depthActive, distance, depth, cap, 'depth', 'active', 'abs'),
    ];
}

export function geometrySummary(z, spacingM) {
    const geom = stationGeometry(z, spacingM);
    let maxGrade = 0;
    let minCrest = null;
    let minDip = null;
    for (let i = 0; i < z.length; i++) {
        const a = Math.abs(geom.forwardGrade[i]);
        if (a > maxGrade) maxGrade = a;
        if (geom.curvature[i] < -1e-8 && (minCrest == null || geom.radiusM[i] < minCrest)) {
            minCrest = geom.radiusM[i];
        }
        if (geom.curvature[i] > 1e-8 && (minDip == null || geom.radiusM[i] < minDip)) {
            minDip = geom.radiusM[i];
        }
    }
    return { max_grade: maxGrade, min_crest_radius_m: minCrest, min_dip_radius_m: minDip };
}

function catchSide(sampleGround, x, y, nx, ny, side, halfM, zDesign) {
    const edge = side * halfM;
    const groundAt = (lateral) => sampleGround(x + nx * lateral, y + ny * lateral);
    const edgeGround = groundAt(edge);
    if (Math.abs(edgeGround - zDesign) <= 1e-3) {
        return { reach_m: halfM, slope_sign: 0, daylight: true };
    }
    const slopeSign = edgeGround > zDesign ? 1 : -1;
    let prevS = 0;
    let prevGround = edgeGround;
    let prevSlope = zDesign;
    let s = CATCH_STEP_M;
    while (s <= MAX_CATCH_M + 1e-9) {
        const ground = groundAt(side * (halfM + s));
        const slope = zDesign + slopeSign * (s * SIDE_SLOPE);
        const crossed = (slopeSign > 0 && slope >= ground) || (slopeSign < 0 && slope <= ground);
        if (crossed) {
            const dslope = slope - prevSlope;
            const dground = ground - prevGround;
            const denom = dslope - dground;
            let frac = Math.abs(denom) < 1e-12 ? 0 : (prevGround - prevSlope) / denom;
            if (frac < 0) frac = 0;
            if (frac > 1) frac = 1;
            const hit = prevS + frac * (s - prevS);
            return { reach_m: halfM + hit, slope_sign: slopeSign, daylight: true };
        }
        prevS = s;
        prevGround = ground;
        prevSlope = slope;
        s += CATCH_STEP_M;
    }
    return { reach_m: halfM + MAX_CATCH_M, slope_sign: slopeSign, daylight: false };
}

function sectionAreas(sampleGround, x, y, nx, ny, halfM, zDesign, left, right) {
    const groundAt = (lateral) => sampleGround(x + nx * lateral, y + ny * lateral);
    const surfaceAt = (lateral) => {
        const ad = Math.abs(lateral);
        if (ad <= halfM + 1e-9) return zDesign;
        const info = lateral >= 0 ? left : right;
        const outward = ad - halfM;
        const reachOut = info.reach_m - halfM;
        if (outward >= reachOut - 1e-6) return groundAt(lateral);
        return zDesign + info.slope_sign * outward * SIDE_SLOPE;
    };
    const latMin = -right.reach_m;
    const latMax = left.reach_m;
    const lats = [latMin];
    let cursor = latMin;
    while (cursor < latMax - 1e-9) {
        cursor = Math.min(latMax, cursor + CATCH_STEP_M);
        lats.push(cursor);
    }
    for (const mark of [-halfM, 0, halfM]) {
        if (latMin < mark && mark < latMax && lats.every((v) => Math.abs(mark - v) > 1e-6)) {
            lats.push(mark);
        }
    }
    lats.sort((a, b) => a - b);
    let cut = 0;
    let fill = 0;
    let prev = lats[0];
    let prevGap = groundAt(prev) - surfaceAt(prev);
    for (let k = 1; k < lats.length; k++) {
        const lat = lats[k];
        const gap = groundAt(lat) - surfaceAt(lat);
        const width = lat - prev;
        if (prevGap === 0 && gap === 0) {
            prev = lat;
            prevGap = gap;
            continue;
        }
        if (prevGap * gap < 0) {
            const frac = Math.abs(prevGap) / (Math.abs(prevGap) + Math.abs(gap));
            const mid = prev + frac * width;
            const w0 = mid - prev;
            const w1 = lat - mid;
            cut += (Math.max(prevGap, 0) * w0) * 0.5;
            fill += (Math.max(-prevGap, 0) * w0) * 0.5;
            cut += (Math.max(gap, 0) * w1) * 0.5;
            fill += (Math.max(-gap, 0) * w1) * 0.5;
        } else {
            const avg = 0.5 * (prevGap + gap);
            if (avg >= 0) cut += avg * width;
            else fill += -avg * width;
        }
        prev = lat;
        prevGap = gap;
    }
    return [cut, fill];
}

export function designSections(x, y, designZ, widthM, sampleGround) {
    const n = designZ.length;
    const half = widthM * 0.5;
    if (!(half > 0)) throw new Error('track width must be positive');
    const sections = new Array(n);
    for (let i = 0; i < n; i++) {
        const prev = i === 0 ? n - 1 : i - 1;
        const nxt = i + 1 === n ? 0 : i + 1;
        let tx = x[nxt] - x[prev];
        let ty = y[nxt] - y[prev];
        let length = Math.hypot(tx, ty);
        if (length < 1e-9) {
            tx = 1;
            ty = 0;
            length = 1;
        }
        tx /= length;
        ty /= length;
        const nx = -ty;
        const ny = tx;
        const zx = x[i];
        const zy = y[i];
        const zd = designZ[i];
        sections[i] = {
            x: zx,
            y: zy,
            z_design: zd,
            nx,
            ny,
            half_m: half,
            left: catchSide(sampleGround, zx, zy, nx, ny, 1, half, zd),
            right: catchSide(sampleGround, zx, zy, nx, ny, -1, half, zd),
        };
    }
    return sections;
}

export function earthworks(x, y, terrainZ, designZ, spacingM, widthM, sampleGround) {
    const n = designZ.length;
    const ds = spacingM;
    const sections = designSections(x, y, designZ, widthM, sampleGround);
    let cutM3 = 0;
    let fillM3 = 0;
    let maxCut = 0;
    let maxFill = 0;
    const daylightMiss = [];
    for (let i = 0; i < n; i++) {
        const section = sections[i];
        const centreCut = terrainZ[i] - designZ[i];
        if (centreCut > maxCut) maxCut = centreCut;
        if (-centreCut > maxFill) maxFill = -centreCut;
        if (!section.left.daylight || !section.right.daylight) daylightMiss.push(i * ds);
        const [areaCut, areaFill] = sectionAreas(
            sampleGround, section.x, section.y, section.nx, section.ny,
            section.half_m, section.z_design, section.left, section.right,
        );
        cutM3 += areaCut * ds;
        fillM3 += areaFill * ds;
    }
    return {
        max_cut_m: maxCut,
        max_fill_m: maxFill,
        cut_m3: cutM3,
        fill_m3: fillM3,
        total_m3: cutM3 + fillM3,
        daylight_miss_m: daylightMiss,
        side_slope: '1:2',
        sections,
    };
}

// Elevation of the flat deck (no camber) at a lateral offset from a section.
// Null means the point is outside the catch and the terrain is left alone.
export function surfaceAt(section, lateral) {
    const ad = Math.abs(lateral);
    if (ad <= section.half_m + 1e-9) return section.z_design;
    const info = lateral >= 0 ? section.left : section.right;
    const outward = ad - section.half_m;
    const reachOut = info.reach_m - section.half_m;
    if (outward >= reachOut - 1e-6) return null;
    return section.z_design + info.slope_sign * outward * SIDE_SLOPE;
}

export function runCovers(run, distanceM, spacingM) {
    const tol = spacingM * 0.51;
    const start = run.start_m;
    const end = run.end_m;
    if (run.wraps) return distanceM >= start - tol || distanceM <= end + tol;
    return start - tol <= distanceM && distanceM <= end + tol;
}

export function violationsAreReported(terrainZ, designZ, spacingM, runs, options) {
    const ds = spacingM;
    const geom = stationGeometry(designZ, ds);
    const problems = [];
    const n = designZ.length;
    const masks = [
        ['gradient', (i) => Math.abs(geom.forwardGrade[i]) > options.maxGrade + GRADIENT_MARGIN],
        ['crest_radius', (i) => geom.curvature[i] < -1e-8 && geom.radiusM[i] < options.minCrestRadiusM * RADIUS_FRACTION],
        ['dip_radius', (i) => geom.curvature[i] > 1e-8 && geom.radiusM[i] < options.minDipRadiusM * RADIUS_FRACTION],
        ['depth', (i) => Math.abs(designZ[i] - terrainZ[i]) > options.maxCutFillM + DEPTH_MARGIN_M],
    ];
    for (const [kind, pred] of masks) {
        const covering = runs.filter((r) => r.kind === kind && r.status === 'violated');
        for (let i = 0; i < n; i++) {
            if (!pred(i)) continue;
            const d = i * ds;
            if (!covering.some((r) => runCovers(r, d, ds))) {
                problems.push(`${kind} at ${d.toFixed(1)} m is not in the binding report`);
                if (problems.length > 8) return problems;
            }
        }
    }
    return problems;
}

export function buildVerticalProfile(x, y, distances, terrainZ, lapM, widthM, sampleGround, options = {}) {
    const maxGrade = options.maxGrade;
    const maxCutFillM = options.maxCutFillM ?? DEFAULT_MAX_CUT_FILL_M;
    const minCrestRadiusM = options.minCrestRadiusM ?? DEFAULT_MIN_CREST_RADIUS_M;
    const minDipRadiusM = options.minDipRadiusM ?? DEFAULT_MIN_DIP_RADIUS_M;
    const n = sampleCountForLap(lapM);
    const terrainGrid = resampleClosed(distances, terrainZ, lapM, n);
    const xGrid = resampleClosed(distances, x, lapM, n);
    const yGrid = resampleClosed(distances, y, lapM, n);
    const limits = {
        maxGrade,
        maxCutFillM,
        minCrestRadiusM,
        minDipRadiusM,
    };
    const designZ = fitProfile(terrainGrid.values, terrainGrid.spacing, limits);
    const designAtPoints = interpClosed(terrainGrid.stations, designZ, lapM, distances);
    const reportOptions = {
        maxGrade,
        maxCutFillM,
        minCrestRadiusM,
        minDipRadiusM,
    };
    const bindings = bindingReport(terrainGrid.values, designZ, terrainGrid.spacing, reportOptions);
    const works = earthworks(
        xGrid.values, yGrid.values, terrainGrid.values, designZ,
        terrainGrid.spacing, widthM, sampleGround,
    );
    const violated = bindings.filter((r) => r.status === 'violated');
    return {
        sample_count: n,
        spacing_m: terrainGrid.spacing,
        lap_m: lapM,
        distance_m: terrainGrid.stations,
        x: xGrid.values,
        y: yGrid.values,
        terrain_z: terrainGrid.values,
        design_z: designZ,
        design_at_points: designAtPoints,
        limits: {
            max_grade: maxGrade,
            max_cut_fill_m: maxCutFillM,
            min_crest_radius_m: minCrestRadiusM,
            min_dip_radius_m: minDipRadiusM,
        },
        terrain: geometrySummary(terrainGrid.values, terrainGrid.spacing),
        design: geometrySummary(designZ, terrainGrid.spacing),
        earthworks: {
            max_cut_m: works.max_cut_m,
            max_fill_m: works.max_fill_m,
            cut_m3: works.cut_m3,
            fill_m3: works.fill_m3,
            total_m3: works.total_m3,
            daylight_miss_m: works.daylight_miss_m,
            side_slope: works.side_slope,
        },
        sections: works.sections,
        bindings,
        feasible: violated.length === 0,
        join_step_m: designZ[0] - designZ[n - 1],
    };
}
