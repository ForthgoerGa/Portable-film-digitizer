"""flat_field.py — Flat-field illumination model and correction.

Build a per-pixel illumination model from a blank-light RAW capture (no film
in the optical path) and apply it to a frame RAW to remove:
- spatial illumination falloff (vignetting)
- colour bias from the light source
- dust/optical artifacts in the light path

All arrays are float32 RGB H x W x 3 in [0, 1].
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from .models import FlatFieldModel

log = logging.getLogger(__name__)

# Gaussian sigma as a fraction of image diagonal for estimating the
# low-frequency illumination field.
_ILLUM_SIGMA_FRAC = 0.10

# Maximum working resolution (longer side) for illumination-map estimation.
# The flat is downsampled to this size, blurred, then upsampled back.
# Keeps the effective blur sigma below ~50 px regardless of RAW resolution.
_ILLUM_MAX_SIDE = 512

# Minimum mean value to avoid division issues when the flat is very dark.
_MIN_MEAN = 1e-5

# Epsilon for safe division when applying the correction.
_APPLY_EPS = 1e-6


def build_flat_field_model(flat_rgb: np.ndarray) -> FlatFieldModel:
    """Estimate an illumination correction model from a flat-field frame.

    The flat frame should be a sensor-normalised linear float32 RGB image
    captured without film (only the light source through the optical stack).

    Parameters
    ----------
    flat_rgb:
        float32 H x W x 3, sensor-normalised linear RGB.

    Returns
    -------
    FlatFieldModel
        Contains the normalised flat, low-freq illumination map, and channel
        means required for apply_flat_field.
    """
    flat_rgb = flat_rgb.astype(np.float32)

    # Per-channel means used to characterise overall colour of the light source.
    channel_means: tuple[float, float, float] = (
        float(flat_rgb[:, :, 0].mean()),
        float(flat_rgb[:, :, 1].mean()),
        float(flat_rgb[:, :, 2].mean()),
    )
    global_mean = float(np.mean(flat_rgb))
    if global_mean < _MIN_MEAN:
        log.warning(
            "Flat-field global mean is very low (%.6f). "
            "Check exposure or file path.",
            global_mean,
        )
        global_mean = max(global_mean, _MIN_MEAN)

    # Normalise so the global mean is ~1.  Values > 1 mean brighter-than-average.
    normalized_flat = flat_rgb / global_mean

    # Estimate low-frequency illumination map via downsample → blur → upsample.
    # Blurring at full resolution with sigma ~10% of the diagonal (≈500 px for
    # a 4K IMX477 frame) requires a ~3000-tap kernel that is prohibitively slow.
    # Instead we downsample to _ILLUM_MAX_SIDE, apply a proportionally smaller
    # Gaussian, then resize back to the original resolution — equivalent result,
    # a fraction of the cost.
    h, w = flat_rgb.shape[:2]
    long_side = max(h, w)
    if long_side > _ILLUM_MAX_SIDE:
        scale = _ILLUM_MAX_SIDE / long_side
        small_h = max(1, int(round(h * scale)))
        small_w = max(1, int(round(w * scale)))
        small_flat = cv2.resize(normalized_flat, (small_w, small_h),
                                interpolation=cv2.INTER_AREA)
    else:
        small_flat = normalized_flat
        small_h, small_w = h, w

    diag_small = np.sqrt(small_h ** 2 + small_w ** 2)
    sigma = max(diag_small * _ILLUM_SIGMA_FRAC, 5.0)

    illum_small = np.empty_like(small_flat)
    for c in range(3):
        illum_small[:, :, c] = cv2.GaussianBlur(
            small_flat[:, :, c],
            ksize=(0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
        )

    # Upsample back to original resolution.
    if long_side > _ILLUM_MAX_SIDE:
        illum_map = cv2.resize(illum_small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        illum_map = illum_small

    # Identify valid pixels: those that are not severely clipped or zero.
    valid_mask = (flat_rgb.mean(axis=2) > _MIN_MEAN) & (flat_rgb.max(axis=2) < 0.98)

    log.info(
        "Flat-field model built: size=%dx%d, channel_means=(%.4f, %.4f, %.4f), sigma=%.1f",
        w, h, *channel_means, sigma,
    )

    return FlatFieldModel(
        flat_linear_rgb=flat_rgb,
        normalized_flat=normalized_flat,
        illumination_map=illum_map,
        channel_means=channel_means,
        valid_mask=valid_mask,
    )


def apply_flat_field(frame_rgb: np.ndarray, flat_model: FlatFieldModel) -> np.ndarray:
    """Divide the frame by the illumination map to correct for shading / bias.

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 sensor-normalised frame to correct.
    flat_model:
        FlatFieldModel from build_flat_field_model.

    Returns
    -------
    np.ndarray
        float32 H x W x 3 flat-field-corrected frame, clipped to [0, 1].

    Notes
    -----
    The illumination_map is clipped to a minimum of _APPLY_EPS before division
    to prevent division-by-zero in shadow regions.
    """
    frame_rgb = frame_rgb.astype(np.float32)
    illum = flat_model.illumination_map.astype(np.float32)

    # Resize illumination map if the frame size differs (e.g. if flat and frame
    # were captured at different resolutions or crops).
    fh, fw = frame_rgb.shape[:2]
    ih, iw = illum.shape[:2]
    if (ih, iw) != (fh, fw):
        log.warning(
            "Flat-field map size (%dx%d) differs from frame size (%dx%d); resizing.",
            iw, ih, fw, fh,
        )
        illum = cv2.resize(illum, (fw, fh), interpolation=cv2.INTER_LINEAR)

    # Clip illumination map from below to prevent divide-by-zero / extreme gains.
    illum_safe = np.clip(illum, _APPLY_EPS, None)

    # Multiply frame by the inverse of the illumination map.
    # After correction, flat areas should have a uniform response.
    corrected = frame_rgb / illum_safe

    return np.clip(corrected, 0.0, 1.0).astype(np.float32)
