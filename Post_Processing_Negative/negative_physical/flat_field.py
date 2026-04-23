"""Bayer-domain flat-field model and correction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .raw_io import RawBayerFrame

_EPS = 1e-6
_DEFAULT_ILLUM_MAX_SIDE = 1024
_DEFAULT_ILLUM_SIGMA_FRAC = 0.02


@dataclass(frozen=True)
class FlatFieldModel:
    """Low-frequency Bayer-domain illumination model."""

    illumination_map: np.ndarray
    cfa_pattern: str
    cfa_pattern_matrix: tuple[tuple[str, ...], ...]
    cfa_index_matrix: tuple[tuple[int, ...], ...]
    width: int
    height: int
    sigma_frac: float
    max_side: int
    plane_stats: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def build_flat_model(
    backlight_frame: RawBayerFrame,
    sigma_frac: float = _DEFAULT_ILLUM_SIGMA_FRAC,
    max_side: int = _DEFAULT_ILLUM_MAX_SIDE,
) -> FlatFieldModel:
    """Build a per-CFA-plane normalized illumination map from a flat frame."""

    sigma_frac = float(sigma_frac)
    max_side = int(max_side)
    if sigma_frac <= 0.0:
        raise ValueError(f"sigma_frac must be > 0, got {sigma_frac}")
    if max_side < 64:
        raise ValueError(f"max_side must be >= 64, got {max_side}")

    flat = backlight_frame.normalized_bayer.astype(np.float32)
    pattern = np.asarray(backlight_frame.cfa_index_matrix, dtype=np.int16)
    colors = backlight_frame.cfa_pattern_matrix
    tile_h, tile_w = pattern.shape
    illumination = np.empty_like(flat, dtype=np.float32)
    plane_stats: list[dict[str, Any]] = []

    for y in range(tile_h):
        for x in range(tile_w):
            plane = flat[y::tile_h, x::tile_w]
            plane_mean = max(float(plane.mean()), _EPS)
            low_freq = _estimate_low_frequency_plane(
                plane,
                sigma_frac=sigma_frac,
                max_side=max_side,
            )
            illum_plane = low_freq / plane_mean
            illumination[y::tile_h, x::tile_w] = np.clip(illum_plane, _EPS, None)

            plane_stats.append(
                {
                    "offset": [int(y), int(x)],
                    "plane_index": int(pattern[y, x]),
                    "color": colors[y][x],
                    "mean": plane_mean,
                    "min": float(plane.min()),
                    "max": float(plane.max()),
                    "std": float(plane.std()),
                    "saturated_fraction": float((plane >= 0.995).mean()),
                    "dark_fraction": float((plane <= 0.001).mean()),
                    "illumination_min": float(illum_plane.min()),
                    "illumination_max": float(illum_plane.max()),
                    "illumination_std": float(illum_plane.std()),
                }
            )

    diagnostics = {
        "flat_source": backlight_frame.path,
        "flat_file": backlight_frame.metadata.get("file_name"),
        "cfa_pattern": backlight_frame.cfa_pattern,
        "width": int(backlight_frame.width),
        "height": int(backlight_frame.height),
        "sigma_frac": sigma_frac,
        "max_side": max_side,
        "illumination_min": float(illumination.min()),
        "illumination_max": float(illumination.max()),
        "illumination_mean": float(illumination.mean()),
        "illumination_std": float(illumination.std()),
        "plane_stats": plane_stats,
        "warnings": _flat_quality_warnings(plane_stats),
    }

    return FlatFieldModel(
        illumination_map=illumination.astype(np.float32),
        cfa_pattern=backlight_frame.cfa_pattern,
        cfa_pattern_matrix=backlight_frame.cfa_pattern_matrix,
        cfa_index_matrix=backlight_frame.cfa_index_matrix,
        width=backlight_frame.width,
        height=backlight_frame.height,
        sigma_frac=sigma_frac,
        max_side=max_side,
        plane_stats=plane_stats,
        diagnostics=diagnostics,
    )


def apply_flat_field(
    frame: RawBayerFrame,
    model: FlatFieldModel,
    strength: float = 1.0,
) -> np.ndarray:
    """Apply the normalized illumination map to a normalized Bayer frame.

    ``strength`` controls how aggressively the illumination map is removed:
    0.0 disables flat-field correction, 1.0 is the physically direct division,
    and values above 1.0 intentionally over-correct residual light spots or
    vignetting for visual tuning.
    """

    _validate_compatible_frame(frame, model)
    strength = float(strength)
    if strength < 0.0:
        raise ValueError(f"flat-field strength must be >= 0, got {strength}")

    illum = np.clip(model.illumination_map.astype(np.float32), _EPS, None)
    if abs(strength - 1.0) > 1e-6:
        illum = np.power(illum, strength).astype(np.float32)

    corrected = frame.normalized_bayer.astype(np.float32) / np.clip(
        illum, _EPS, None
    )
    return corrected.astype(np.float32)


def diagnose_flat_correction(
    frame: RawBayerFrame,
    corrected_bayer: np.ndarray,
    model: FlatFieldModel,
    strength: float = 1.0,
) -> dict[str, Any]:
    """Compute scalar diagnostics for a flat-field corrected frame."""

    before = frame.normalized_bayer.astype(np.float32)
    after = corrected_bayer.astype(np.float32)
    before_cv = low_frequency_variation(before)
    after_cv = low_frequency_variation(after)

    return {
        "source": frame.path,
        "file_name": frame.metadata.get("file_name"),
        "cfa_pattern": frame.cfa_pattern,
        "flat_field_strength": float(strength),
        "metadata": frame.metadata,
        "before_low_frequency_cv": before_cv,
        "after_low_frequency_cv": after_cv,
        "variation_reduction_ratio": (
            after_cv / before_cv if before_cv > _EPS else None
        ),
        "corrected_min": float(after.min()),
        "corrected_max": float(after.max()),
        "corrected_mean": float(after.mean()),
        "corrected_std": float(after.std()),
        "corrected_clipped_low_fraction": float((after <= 0.0).mean()),
        "corrected_clipped_high_fraction": float((after >= 1.0).mean()),
        "warnings": _frame_warnings(frame, corrected_bayer, model),
    }


def low_frequency_variation(bayer: np.ndarray, max_side: int = 512) -> float:
    """Coefficient of variation of a low-frequency luminance proxy."""

    image = bayer.astype(np.float32)
    h, w = image.shape
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        small_w = max(1, int(round(w * scale)))
        small_h = max(1, int(round(h * scale)))
        proxy = cv2.resize(image, (small_w, small_h), interpolation=cv2.INTER_AREA)
    else:
        proxy = image
        small_h, small_w = h, w

    sigma = max(float(np.sqrt(small_h**2 + small_w**2)) * 0.08, 3.0)
    low = cv2.GaussianBlur(proxy, (0, 0), sigmaX=sigma, sigmaY=sigma)
    mean = max(float(low.mean()), _EPS)
    return float(low.std() / mean)


def _estimate_low_frequency_plane(
    plane: np.ndarray,
    sigma_frac: float,
    max_side: int,
) -> np.ndarray:
    h, w = plane.shape
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        small_w = max(1, int(round(w * scale)))
        small_h = max(1, int(round(h * scale)))
        small = cv2.resize(plane, (small_w, small_h), interpolation=cv2.INTER_AREA)
    else:
        small = plane.astype(np.float32)
        small_h, small_w = h, w

    sigma = max(float(np.sqrt(small_h**2 + small_w**2)) * sigma_frac, 3.0)
    blurred = cv2.GaussianBlur(small.astype(np.float32), (0, 0), sigmaX=sigma, sigmaY=sigma)
    if long_side > max_side:
        return cv2.resize(blurred, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    return blurred.astype(np.float32)


def _validate_compatible_frame(frame: RawBayerFrame, model: FlatFieldModel) -> None:
    if (frame.height, frame.width) != (model.height, model.width):
        raise ValueError(
            "Frame size does not match flat-field model: "
            f"{frame.width}x{frame.height} vs {model.width}x{model.height}"
        )
    if frame.cfa_pattern != model.cfa_pattern:
        raise ValueError(
            f"Frame CFA pattern {frame.cfa_pattern} does not match flat CFA {model.cfa_pattern}"
        )


def _flat_quality_warnings(plane_stats: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for stat in plane_stats:
        label = f"{stat['color']}@{stat['offset']}"
        if stat["mean"] < 0.03:
            warnings.append(f"Flat plane {label} is very dark; correction may amplify noise.")
        if stat["saturated_fraction"] > 0.005:
            warnings.append(f"Flat plane {label} has saturated pixels.")
        if stat["illumination_max"] / max(stat["illumination_min"], _EPS) > 4.0:
            warnings.append(f"Flat plane {label} has a very wide illumination range.")
    return warnings


def _frame_warnings(
    frame: RawBayerFrame,
    corrected_bayer: np.ndarray,
    model: FlatFieldModel,
) -> list[str]:
    warnings: list[str] = []
    if frame.cfa_pattern != model.cfa_pattern:
        warnings.append("Frame CFA pattern differs from flat-field CFA pattern.")
    high = float((corrected_bayer >= 1.0).mean())
    low = float((corrected_bayer <= 0.0).mean())
    if high > 0.02:
        warnings.append(f"Corrected frame has {high:.2%} values at or above 1.0.")
    if low > 0.02:
        warnings.append(f"Corrected frame has {low:.2%} values at or below 0.0.")
    return warnings
