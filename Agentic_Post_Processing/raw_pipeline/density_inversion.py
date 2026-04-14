"""density_inversion.py — Technically grounded negative-to-positive inversion.

Operates in relative optical-density space.

Pipeline (in order):
  1. compute_transmission   — frame / base (clear base should map near 1)
  2. compute_density        — -log(transmission)  → relative density
  3. normalize_density      — maps relative density range to [0, 1]
  4. invert_negative        — full pipeline, returns InversionResult

All inputs/outputs are float32 RGB H x W x 3 or (3,), values as noted.
"""

from __future__ import annotations

import logging

import numpy as np

from .models import FilmCharacterization, InversionResult

log = logging.getLogger(__name__)

# Epsilon to protect log(0).
_EPS = 1e-6

# Fraction of pixels clipped at low/high extremes that triggers a warning.
_CLIP_WARN_FRAC = 0.05


def compute_transmission(
    frame_rgb: np.ndarray,
    base_rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Compute transmission proxy: frame_rgb / base_rgb.

    Transmission is in (0, 1]: 1 = clear base, 0 = fully opaque.

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 flat-field-corrected linear RGB, [0, 1].
    base_rgb:
        shape (3,) float32 film base estimate.  If None, the raw frame values
        are used as-is (transmission = frame_rgb itself).

    Returns
    -------
    np.ndarray
        float32 H x W x 3, values in (_EPS, 1.0].
    """
    frame_safe = np.clip(frame_rgb, _EPS, 1.0).astype(np.float32)
    if base_rgb is None:
        return frame_safe

    base_safe = np.clip(base_rgb, _EPS, 1.0).astype(np.float32)
    transmission = frame_safe / base_safe[np.newaxis, np.newaxis, :]
    # Clip: transmission > 1 would imply the frame is brighter than the base,
    # which can happen due to noise in the base estimate.
    return np.clip(transmission, _EPS, 1.0).astype(np.float32)


def compute_density(transmission_rgb: np.ndarray) -> np.ndarray:
    """Convert transmission to optical density: D = -log(T).

    Higher density = more silver / dye = darker in the original scene.

    Parameters
    ----------
    transmission_rgb:
        float32 H x W x 3, values in (0, 1].

    Returns
    -------
    np.ndarray
        float32 H x W x 3, values >= 0 (density increases toward infinity
        for fully opaque regions; in practice capped by input clipping).
    """
    t_safe = np.clip(transmission_rgb, _EPS, 1.0).astype(np.float32)
    return -np.log(t_safe).astype(np.float32)


def normalize_density(
    density_rgb: np.ndarray,
    dmin_density_rgb: np.ndarray | None,
    dmax_density_rgb: np.ndarray | None,
    upper_density_rgb: np.ndarray | None,
) -> np.ndarray:
    """Normalise per-channel relative density into [0, 1] using density refs.

    Priority for the upper reference:
    1. dmax_density_rgb (from leader scan)
    2. upper_density_rgb (scene estimate)
    3. 99th-percentile of density_rgb itself (last resort)

    Priority for the lower reference:
    1. dmin_density_rgb (from leader scan)
    2. 0 (clear-base default)
    """
    density = density_rgb.astype(np.float32)

    if dmax_density_rgb is not None:
        upper = np.clip(dmax_density_rgb.astype(np.float32), 1e-4, None)
        log.debug("normalize_density: using leader dmax density.")
    elif upper_density_rgb is not None:
        upper = np.clip(upper_density_rgb.astype(np.float32), 1e-4, None)
        log.debug("normalize_density: using scene upper_density estimate.")
    else:
        flat = density.reshape(-1, 3)
        upper = np.percentile(flat, 99.0, axis=0).astype(np.float32)
        upper = np.clip(upper, 1e-4, None)
        log.warning(
            "normalize_density: no calibration reference; using 99th percentile fallback."
        )

    if dmin_density_rgb is not None:
        lower = np.clip(dmin_density_rgb.astype(np.float32), 0.0, None)
        density = density - lower[np.newaxis, np.newaxis, :]
        density = np.clip(density, 0.0, None)
        upper = upper - lower
        upper = np.clip(upper, 1e-4, None)

    norm = density / upper[np.newaxis, np.newaxis, :]
    return np.clip(norm, 0.0, 1.0).astype(np.float32)


def invert_negative(
    frame_rgb: np.ndarray,
    characterization: FilmCharacterization,
) -> InversionResult:
    """Full density-domain negative inversion pipeline.

    Stages:
      1. transmission (frame / base)
      2. relative density (-log)
      3. density normalisation
      4. density → positive linear (inverted: bright scene = low density)

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 flat-field-corrected frame.
    characterization:
        FilmCharacterization from film_characterization.characterize_film.

    Returns
    -------
    InversionResult
        Contains the technical positive and diagnostic fields.
    """
    # Step 1: transmission
    transmission = compute_transmission(frame_rgb, characterization.base_rgb)

    # Step 2: relative density. Since transmission is already frame/base,
    # clear base should land near 0 density and no extra base subtraction is needed.
    density_relative = compute_density(transmission)

    # Step 3: normalise density
    density_norm = normalize_density(
        density_relative,
        dmin_density_rgb=characterization.dmin_density_rgb,
        dmax_density_rgb=characterization.dmax_density_rgb,
        upper_density_rgb=characterization.upper_density_rgb,
    )

    # Step 4: map normalised density to positive linear RGB.
    # In a negative, high density → dark scene.  We invert: positive = 1 - density_norm.
    positive_linear = (1.0 - density_norm).astype(np.float32)
    positive_linear = np.clip(positive_linear, 0.0, 1.0)

    # --- Diagnostics ---------------------------------------------------------
    means = positive_linear.mean(axis=(0, 1))  # shape (3,)
    channel_spread = float(means.max() - means.min())

    # Clipping ratio: fraction of pixels at 0 or 1 after inversion.
    clipped_low = float((positive_linear <= 0.001).mean())
    clipped_high = float((positive_linear >= 0.999).mean())
    clipping_ratio = clipped_low + clipped_high

    if clipping_ratio > _CLIP_WARN_FRAC:
        log.warning(
            "invert_negative: high clipping ratio (%.3f); check density normalisation.",
            clipping_ratio,
        )

    flat_density = density_relative.reshape(-1, 3)
    density_p50 = np.percentile(flat_density, 50.0, axis=0).astype(np.float32)
    density_p95 = np.percentile(flat_density, 95.0, axis=0).astype(np.float32)
    density_p99 = np.percentile(flat_density, 99.0, axis=0).astype(np.float32)

    diagnostics: dict[str, float] = {
        "mean_r": float(means[0]),
        "mean_g": float(means[1]),
        "mean_b": float(means[2]),
        "channel_spread": channel_spread,
        "clipped_low_frac": clipped_low,
        "clipped_high_frac": clipped_high,
        "density_range_r": float(density_relative[:, :, 0].max()),
        "density_range_g": float(density_relative[:, :, 1].max()),
        "density_range_b": float(density_relative[:, :, 2].max()),
        "density_p50_mean": float(density_p50.mean()),
        "density_p95_mean": float(density_p95.mean()),
        "density_p99_mean": float(density_p99.mean()),
        "density_p99_spread": float(density_p99.max() - density_p99.min()),
    }

    log.info(
        "Inversion complete: means=(%.3f, %.3f, %.3f), spread=%.4f, clipping=%.3f",
        *means, channel_spread, clipping_ratio,
    )

    return InversionResult(
        positive_linear_rgb=positive_linear,
        density_rgb=density_relative,
        density_norm_rgb=density_norm,
        channel_spread=channel_spread,
        clipping_ratio=clipping_ratio,
        diagnostics=diagnostics,
    )
