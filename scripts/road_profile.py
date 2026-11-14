"""
Longitudinal road profile smoothing and gradient measurement.

Race-track gradient figures are reported over tens to hundreds of metres, not
between adjacent DEM posts. Copernicus DSM is ~30 m; sampling the centreline
at 2–5 m picks up bilinear staircase steps unless the elevation profile is
low-pass filtered first (graded-road proxy), then slope is measured with a
rolling arc-length window.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d

# Low-pass σ (~15 m) removes ~30 m DEM quantization without washing out real grade.
ROAD_PROFILE_SMOOTH_M = 15.0
# FIA-style reporting window along the centreline.
GRADIENT_WINDOW_M = 100.0


def smooth_road_profile(
    elevations: np.ndarray,
    sample_spacing_m: float,
    smooth_sigma_m: float = ROAD_PROFILE_SMOOTH_M,
) -> np.ndarray:
    """Gaussian low-pass on chainage-sampled centreline elevations."""
    elev = np.asarray(elevations, dtype=np.float64)
    sigma_samples = max(smooth_sigma_m / max(sample_spacing_m, 1e-6), 0.5)
    return gaussian_filter1d(elev, sigma_samples)


def mean_sample_spacing(
    distances: np.ndarray,
    closed_total_length: float | None = None,
) -> float:
    """Typical spacing between profile samples (m)."""
    if len(distances) < 2:
        return 5.0
    if closed_total_length is not None and closed_total_length > 0:
        return closed_total_length / len(distances)
    seg = np.diff(distances)
    seg = seg[seg > 0]
    return float(np.mean(seg)) if len(seg) else 5.0


def compute_longitudinal_gradients(
    distances: np.ndarray,
    elevations: np.ndarray,
    window_m: float = GRADIENT_WINDOW_M,
    closed_total_length: float | None = None,
) -> np.ndarray:
    """
    Compute gradient (%) at each chainage point using a symmetric rolling window.

    Positive = uphill, negative = downhill.
    For closed circuits pass closed_total_length so the window wraps the lap.
    """
    distances = np.asarray(distances, dtype=np.float64)
    elevations = np.asarray(elevations, dtype=np.float64)
    n = len(distances)
    gradients = np.zeros(n)
    half = window_m / 2.0

    if closed_total_length is not None and closed_total_length > 0:
        dist_ext = np.concatenate([
            distances - closed_total_length,
            distances,
            distances + closed_total_length,
        ])
        elev_ext = np.concatenate([elevations, elevations, elevations])
        for i in range(n):
            d0 = distances[i] - half
            d1 = distances[i] + half
            i0 = int(np.searchsorted(dist_ext, d0))
            i1 = int(np.searchsorted(dist_ext, d1))
            i1 = min(i1, len(dist_ext) - 1)
            if i1 > i0:
                delta_elev = elev_ext[i1] - elev_ext[i0]
                delta_dist = dist_ext[i1] - dist_ext[i0]
                if delta_dist > 0:
                    gradients[i] = (delta_elev / delta_dist) * 100.0
        return gradients

    total = float(distances[-1]) if n else 0.0
    for i in range(n):
        d0 = max(0.0, distances[i] - half)
        d1 = min(total, distances[i] + half)
        i0 = int(np.searchsorted(distances, d0))
        i1 = int(np.searchsorted(distances, d1))
        i1 = min(i1, n - 1)
        if i1 > i0:
            delta_elev = elevations[i1] - elevations[i0]
            delta_dist = distances[i1] - distances[i0]
            if delta_dist > 0:
                gradients[i] = (delta_elev / delta_dist) * 100.0
    return gradients


def grade_profile(
    distances: np.ndarray,
    elevations_raw: np.ndarray,
    sample_spacing_m: float,
    *,
    smooth_sigma_m: float = ROAD_PROFILE_SMOOTH_M,
    window_m: float = GRADIENT_WINDOW_M,
    closed_total_length: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (graded_elevations, gradients_pct) for a centreline profile."""
    graded = smooth_road_profile(elevations_raw, sample_spacing_m, smooth_sigma_m)
    gradients = compute_longitudinal_gradients(
        distances, graded, window_m=window_m, closed_total_length=closed_total_length
    )
    return graded, gradients


def max_abs_gradient_pct(gradients: np.ndarray) -> float:
    return float(np.max(np.abs(gradients)))
