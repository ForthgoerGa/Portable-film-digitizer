"""rendering.py — Aesthetic rendering stage for already-inverted positives.

This stage operates ONLY on the technical positive output from density_inversion.
It must NOT compensate for inversion failures — those should be flagged by
diagnostics and addressed by retrying earlier stages.

Rendering stages:
  1. residual white balance (per-channel gain)
  2. tone curve (gamma + contrast)
  3. colour rendering (saturation / vibrance)
  4. detail rendering (unsharp mask)

All arrays are float32 RGB H x W x 3 in [0, 1].
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np

from .models import RenderResult

log = logging.getLogger(__name__)

# Default rendering parameters.  Callers can override via the params dict.
_DEFAULT_PARAMS: dict[str, Any] = {
    # Residual WB gains (R, G, B); 1.0 = no adjustment.
    "wb_gains": (1.0, 1.0, 1.0),
    # Tone: gamma < 1 brightens midtones, > 1 darkens.
    "gamma": 1.0,
    # Tone: linear contrast multiplier centred at 0.5; 0 = no effect.
    "contrast": 0.0,
    # Colour: saturation multiplier; 1 = unchanged, > 1 = more saturated.
    "saturation": 1.0,
    # Vibrance boost (selective saturation on low-sat pixels); 0 = off.
    "vibrance": 0.0,
    # Unsharp mask amount; 0 = off.
    "unsharp_amount": 0.35,
    # Unsharp mask Gaussian sigma.
    "unsharp_sigma": 1.2,
    # Shadow lift: additive lift in [0, 1] space; 0 = off.
    "shadow_lift": 0.0,
}


def apply_residual_white_balance(
    image_rgb: np.ndarray,
    gains: tuple[float, float, float],
) -> np.ndarray:
    """Apply per-channel multiplicative gains for residual white-balance trim.

    Parameters
    ----------
    image_rgb:
        float32 H x W x 3 in [0, 1].
    gains:
        (r_gain, g_gain, b_gain); values near 1.0 for minor corrections.

    Returns
    -------
    np.ndarray
        float32 H x W x 3, clipped to [0, 1].
    """
    g = np.array(gains, dtype=np.float32)[np.newaxis, np.newaxis, :]
    return np.clip(image_rgb * g, 0.0, 1.0).astype(np.float32)


def apply_tone_curve(
    image_rgb: np.ndarray,
    params: dict[str, float],
) -> np.ndarray:
    """Apply gamma and linear contrast adjustment.

    Gamma is applied first (power-law), then a linear S-curve contrast
    centred at mid-grey.

    Parameters
    ----------
    image_rgb:
        float32 H x W x 3 in [0, 1].
    params:
        Dict with optional keys 'gamma' (float) and 'contrast' (float).

    Returns
    -------
    np.ndarray
        float32 H x W x 3, clipped to [0, 1].
    """
    img = image_rgb.astype(np.float32)

    gamma = float(params.get("gamma", 1.0))
    if abs(gamma - 1.0) > 0.01:
        gamma = max(gamma, 0.01)
        img = np.power(np.clip(img, 0.0, 1.0), gamma)

    contrast = float(params.get("contrast", 0.0))
    if abs(contrast) > 0.001:
        # Linear contrast: output = 0.5 + (input - 0.5) * (1 + contrast)
        img = 0.5 + (img - 0.5) * (1.0 + contrast)

    shadow_lift = float(params.get("shadow_lift", 0.0))
    if shadow_lift > 0.0:
        img = img + shadow_lift

    return np.clip(img, 0.0, 1.0).astype(np.float32)


def apply_color_rendering(
    image_rgb: np.ndarray,
    params: dict[str, float],
) -> np.ndarray:
    """Apply saturation and vibrance adjustments.

    Operates in HSV space via OpenCV (requires temporary BGR uint8 conversion).

    Parameters
    ----------
    image_rgb:
        float32 H x W x 3 in [0, 1].
    params:
        Dict with optional keys 'saturation' (float) and 'vibrance' (float).

    Returns
    -------
    np.ndarray
        float32 H x W x 3 in [0, 1].
    """
    saturation = float(params.get("saturation", 1.0))
    vibrance = float(params.get("vibrance", 0.0))

    if abs(saturation - 1.0) < 0.01 and abs(vibrance) < 0.01:
        return image_rgb.copy()

    # Convert to uint8 BGR for OpenCV HSV operations.
    bgr_u8 = cv2.cvtColor(
        np.clip(image_rgb[:, :, ::-1] * 255.0, 0, 255).astype(np.uint8),
        cv2.COLOR_BGR2BGR,  # identity; kept for clarity
    )
    hsv = cv2.cvtColor(bgr_u8, cv2.COLOR_BGR2HSV).astype(np.float32)

    # Saturation scaling.
    if abs(saturation - 1.0) >= 0.01:
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0.0, 255.0)

    # Vibrance: weight saturation boost by (1 - current_sat), so already-
    # saturated colours are boosted less (avoids over-saturation).
    if abs(vibrance) >= 0.01:
        sat_norm = hsv[:, :, 1] / 255.0
        weight = 1.0 - sat_norm
        delta = vibrance * weight * 255.0
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] + delta, 0.0, 255.0)

    bgr_out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    rgb_f32 = cv2.cvtColor(bgr_out, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.clip(rgb_f32, 0.0, 1.0)


def apply_detail_rendering(
    image_rgb: np.ndarray,
    params: dict[str, float],
) -> np.ndarray:
    """Apply unsharp-mask sharpening.

    Parameters
    ----------
    image_rgb:
        float32 H x W x 3 in [0, 1].
    params:
        Dict with optional keys 'unsharp_amount' (float) and 'unsharp_sigma' (float).

    Returns
    -------
    np.ndarray
        float32 H x W x 3 in [0, 1].
    """
    amount = float(params.get("unsharp_amount", 0.35))
    sigma = float(params.get("unsharp_sigma", 1.2))

    if amount <= 0.0:
        return image_rgb.copy()

    sigma = max(sigma, 0.1)
    blurred = cv2.GaussianBlur(image_rgb, (0, 0), sigmaX=sigma, sigmaY=sigma)
    sharpened = image_rgb + amount * (image_rgb - blurred)
    return np.clip(sharpened, 0.0, 1.0).astype(np.float32)


def render_positive(
    image_rgb: np.ndarray,
    params: dict[str, Any] | None = None,
) -> RenderResult:
    """Full rendering pipeline: WB → tone → colour → detail.

    Parameters
    ----------
    image_rgb:
        float32 H x W x 3 technical positive from density_inversion.
    params:
        Optional dict overriding any subset of _DEFAULT_PARAMS.

    Returns
    -------
    RenderResult
    """
    # Merge caller params over defaults.
    p: dict[str, Any] = {**_DEFAULT_PARAMS, **(params or {})}

    technical_positive = image_rgb.astype(np.float32).copy()
    img = technical_positive

    # Stage 1: residual WB.
    wb_gains = p.get("wb_gains", (1.0, 1.0, 1.0))
    img = apply_residual_white_balance(img, tuple(wb_gains))

    # Stage 2: tone curve (gamma + contrast + shadow lift).
    img = apply_tone_curve(img, p)

    # Stage 3: colour rendering (saturation / vibrance).
    img = apply_color_rendering(img, p)

    # Stage 4: detail (sharpness).
    img = apply_detail_rendering(img, p)

    # --- Compute render metrics on the output --------------------------------
    means = img.mean(axis=(0, 1))
    gray = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    laplacian = cv2.Laplacian(
        np.clip(gray * 255.0, 0, 255).astype(np.uint8), cv2.CV_64F
    )
    sharpness = float(laplacian.var())

    render_metrics: dict[str, float] = {
        "mean_r": float(means[0]),
        "mean_g": float(means[1]),
        "mean_b": float(means[2]),
        "gray_mean": float(gray.mean()),
        "gray_std": float(gray.std()),
        "clipped_black_ratio": float((img <= 0.002).mean()),
        "clipped_white_ratio": float((img >= 0.998).mean()),
        "channel_spread": float(means.max() - means.min()),
        "laplacian_variance": sharpness,
        "blue_red_delta": float(means[2] - means[0]),
        "green_magenta_delta": float(means[1] - (means[0] + means[2]) / 2.0),
    }

    log.info(
        "Render complete: gray_mean=%.3f, sharpness=%.1f, channel_spread=%.4f",
        render_metrics["gray_mean"],
        sharpness,
        render_metrics["channel_spread"],
    )

    return RenderResult(
        rendered_rgb=img,
        technical_positive_rgb=technical_positive,
        render_metrics=render_metrics,
    )
