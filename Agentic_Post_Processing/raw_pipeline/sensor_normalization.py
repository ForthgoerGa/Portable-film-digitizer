"""sensor_normalization.py — Sensor-level normalisation helpers.

Converts a freshly-loaded RawFrame into a calibrated linear float32 RGB array
in [0, 1].  These corrections should be applied before any flat-field or
film-analysis step.

Stages:
1. black_level subtraction — removes fixed pattern / pedestal offset
2. white_level normalisation — maps sensor saturation level to 1.0
3. bad-pixel correction — simple median-based outlier filter (optional)

All functions operate on float32 RGB (H x W x 3) in RGB channel order.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from .models import RawFrame

log = logging.getLogger(__name__)

# Default fallback black/white levels when metadata is unavailable.
_FALLBACK_BLACK = 0.0
_FALLBACK_WHITE = 1.0

# Fraction of image area that may be outlier pixels before warning.
_BAD_PIXEL_WARN_FRAC = 0.01


def subtract_black_level(raw: RawFrame) -> np.ndarray:
    """Return raw.linear_rgb with per-channel black level subtracted.

    If black_level metadata is unavailable the image is returned unchanged.

    Parameters
    ----------
    raw:
        RawFrame as returned by raw_ingest.load_raw_frame.

    Returns
    -------
    np.ndarray
        float32 H x W x 3, values shifted down so sensor black = 0.
        May contain negative values if the input had noise below black.
    """
    img = raw.linear_rgb.astype(np.float32)

    if raw.black_level is None:
        log.debug("No black_level metadata; skipping black subtraction.")
        return img

    bl = np.asarray(raw.black_level, dtype=np.float32)

    # Normalise the black level from its native ADU scale to [0, 1] using the
    # white level so we subtract the right fraction.
    wl = raw.white_level if raw.white_level is not None else 65535.0
    bl_norm = bl / float(wl)

    if bl_norm.ndim == 0:
        img = img - float(bl_norm)
    else:
        # Per-channel shape (3,) — broadcast over H x W
        img = img - bl_norm[np.newaxis, np.newaxis, :]

    return img


def normalize_white_level(image: np.ndarray, raw: RawFrame) -> np.ndarray:
    """Scale image so that the sensor white/saturation level maps to 1.0.

    After black subtraction the input range is roughly [0, (white-black)/white].
    This function stretches it back to [0, 1].

    Parameters
    ----------
    image:
        float32 H x W x 3 after black-level subtraction.
    raw:
        Original RawFrame (used for metadata only).

    Returns
    -------
    np.ndarray
        float32 H x W x 3 in [0, 1] (clipped).
    """
    if raw.black_level is None or raw.white_level is None:
        # Metadata unavailable — assume the loader already normalised to [0, 1].
        return np.clip(image, 0.0, 1.0)

    wl = float(raw.white_level)
    bl = np.asarray(raw.black_level, dtype=np.float32)
    bl_norm = bl / wl

    # Effective white after subtraction
    if bl_norm.ndim == 0:
        effective_white = 1.0 - float(bl_norm)
    else:
        effective_white = 1.0 - bl_norm  # shape (3,)

    effective_white = np.clip(effective_white, 1e-4, None)
    result = image / effective_white

    return np.clip(result, 0.0, 1.0).astype(np.float32)


def apply_bad_pixel_correction(image: np.ndarray) -> np.ndarray:
    """Replace obvious outlier pixels (dead / hot) with median of neighbours.

    Uses a simple approach: pixels that deviate more than 4 standard deviations
    from the local 3×3 median are replaced with that median.

    Parameters
    ----------
    image:
        float32 H x W x 3.

    Returns
    -------
    np.ndarray
        float32 H x W x 3 with outliers smoothed out.
    """
    result = image.copy()
    for c in range(3):
        ch = image[:, :, c]
        # cv2.medianBlur needs uint8 or float32 input of single channel
        ch_f32 = ch.astype(np.float32)
        median = cv2.medianBlur(ch_f32, 3)
        diff = np.abs(ch_f32 - median)
        std = diff.std()
        if std < 1e-8:
            continue
        threshold = 4.0 * std
        outlier_mask = diff > threshold
        frac = outlier_mask.mean()
        if frac > _BAD_PIXEL_WARN_FRAC:
            log.warning(
                "Bad-pixel correction: channel %d has %.1f%% outlier pixels "
                "(threshold=%.5f); check flat-field capture quality.",
                c, frac * 100, threshold,
            )
        result[:, :, c] = np.where(outlier_mask, median, ch_f32)

    return result.astype(np.float32)


def prepare_linear_rgb(raw: RawFrame) -> np.ndarray:
    """Full sensor-normalisation pipeline: black → white → optional bad-pixel.

    This is the recommended single-call entry point.  The returned array is
    suitable for flat_field and all downstream stages.

    Parameters
    ----------
    raw:
        RawFrame from load_raw_frame.

    Returns
    -------
    np.ndarray
        float32 H x W x 3, RGB, values in [0, 1].
    """
    img = subtract_black_level(raw)
    img = normalize_white_level(img, raw)
    # Skip bad-pixel correction when metadata is missing (non-RAW fallback)
    # to avoid distorting already-processed JPEG/PNG data.
    if raw.black_level is not None:
        img = apply_bad_pixel_correction(img)
    return img
