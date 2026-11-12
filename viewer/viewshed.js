// Line-of-sight viewshed over DEM + obstructions.
// Line-for-line port of scripts/surface_model.py (Terrain.sample,
// Obstructions.sample, visibility_from) and compute_viewshed.py; change both
// together. `make verify` runs this module under Node and checks it agrees
// with Python on every preset seat.

export const LOS_SAMPLES = 256;
export const TARGET_HEIGHT_M = 1.0;
export const EYE_ABOVE_TIER_M = 1.2;
export const CLASS_GRANDSTAND = 3;

export class Terrain {
    constructor(meta, heights) {
        this.heights = heights;
        this.rows = meta.rows;
        this.cols = meta.cols;
        this.left = meta.left;
        this.top = meta.top;
        this.resX = meta.res_x;
        this.resY = meta.res_y;
        this.right = this.left + this.resX * this.cols;
        this.bottom = this.top - this.resY * this.rows;
    }

    sample(lon, lat) {
        if (!(lon >= this.left && lon <= this.right && lat >= this.bottom && lat <= this.top)) {
            return NaN;
        }
        const col = Math.min(Math.max((lon - this.left) / this.resX - 0.5, 0), this.cols - 1);
        const row = Math.min(Math.max((this.top - lat) / this.resY - 0.5, 0), this.rows - 1);
        const c0 = Math.min(Math.floor(col), this.cols - 2);
        const r0 = Math.min(Math.floor(row), this.rows - 2);
        const dc = col - c0;
        const dr = row - r0;
        const h = this.heights;
        const n = this.cols;
        return h[r0 * n + c0] * (1 - dc) * (1 - dr) +
               h[r0 * n + c0 + 1] * dc * (1 - dr) +
               h[(r0 + 1) * n + c0] * (1 - dc) * dr +
               h[(r0 + 1) * n + c0 + 1] * dc * dr;
    }
}

export class Obstructions {
    // bytes: three rows*cols uint8 layers: heights (x quantum), classes, stand ids
    constructor(meta, bytes) {
        this.rows = meta.rows;
        this.cols = meta.cols;
        this.left = meta.left;
        this.top = meta.top;
        this.dlon = meta.dlon;
        this.dlat = meta.dlat;
        this.quantum = meta.height_quantum_m;
        const n = this.rows * this.cols;
        this.heightsQ = bytes.subarray(0, n);
        this.classes = bytes.subarray(n, 2 * n);
        this.standIds = bytes.subarray(2 * n, 3 * n);
    }

    index(lon, lat) {
        const c = Math.floor((lon - this.left) / this.dlon);
        const r = Math.floor((this.top - lat) / this.dlat);
        if (c < 0 || c >= this.cols || r < 0 || r >= this.rows) return -1;
        return r * this.cols + c;
    }

    sample(lon, lat) {
        const i = this.index(lon, lat);
        return i < 0 ? 0 : this.heightsQ[i] * this.quantum;
    }

    classAt(lon, lat) {
        const i = this.index(lon, lat);
        return i < 0 ? 0 : this.classes[i];
    }

    standId(lon, lat) {
        const i = this.index(lon, lat);
        return i < 0 ? 0 : this.standIds[i];
    }
}

export function distanceM(lon1, lat1, lon2, lat2) {
    const centerLat = (lat1 + lat2) / 2;
    const latScale = 111320;
    const lonScale = 111320 * Math.cos(centerLat * Math.PI / 180);
    const dx = (lon2 - lon1) * lonScale;
    const dy = (lat2 - lat1) * latScale;
    return Math.sqrt(dx * dx + dy * dy);
}

// Obstructions belonging to the stand the seat is in are ignored.
export function visibilityFrom(terrain, obstructions, coords, seatLon, seatLat, eyeElev,
                               samples = LOS_SAMPLES) {
    const visibility = new Array(coords.length);
    const own = obstructions ? obstructions.standId(seatLon, seatLat) : 0;
    for (let k = 0; k < coords.length; k++) {
        const tlon = coords[k][0];
        const tlat = coords[k][1];
        const targetElev = terrain.sample(tlon, tlat) + TARGET_HEIGHT_M;
        if (Number.isNaN(targetElev)) {
            visibility[k] = false;
            continue;
        }
        let blocked = false;
        for (let i = 1; i < samples; i++) {
            const t = i / samples;
            const slon = seatLon + t * (tlon - seatLon);
            const slat = seatLat + t * (tlat - seatLat);
            let surface = terrain.sample(slon, slat);
            if (Number.isNaN(surface)) continue;
            if (obstructions) {
                const oh = own && obstructions.standId(slon, slat) === own ? 0 : obstructions.sample(slon, slat);
                surface = surface + oh;
            }
            const los = eyeElev + t * (targetElev - eyeElev);
            if (surface > los) {
                blocked = true;
                break;
            }
        }
        visibility[k] = !blocked;
    }
    return visibility;
}

export function computeViewshed(terrain, obstructions, coords, seatLon, seatLat, eyeHeightM) {
    const ground = terrain.sample(seatLon, seatLat);
    const eyeElev = ground + eyeHeightM;
    const visibility = visibilityFrom(terrain, obstructions, coords, seatLon, seatLat, eyeElev);

    const distances = [0];
    for (let i = 1; i < coords.length; i++) {
        distances.push(distances[i - 1] +
            distanceM(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]));
    }
    let visibleLength = 0;
    for (let i = 0; i < visibility.length - 1; i++) {
        if (visibility[i] && visibility[i + 1]) visibleLength += distances[i + 1] - distances[i];
    }
    const total = distances[distances.length - 1];
    return {
        visibility,
        ground_elevation_m: ground,
        eye_elevation_m: eyeElev,
        visible_length_m: visibleLength,
        visible_pct: total > 0 ? visibleLength / total * 100 : 0,
    };
}
