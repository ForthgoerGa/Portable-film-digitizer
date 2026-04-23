"""Demosaic and display-only rendering helpers for physical correction output."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_BAYER_TO_RGB_EA = {
    "BGGR": cv2.COLOR_BayerBG2RGB_EA,
    "GBRG": cv2.COLOR_BayerGB2RGB_EA,
    "RGGB": cv2.COLOR_BayerRG2RGB_EA,
    "GRBG": cv2.COLOR_BayerGR2RGB_EA,
}


def demosaic_to_rgb(bayer: np.ndarray, cfa_pattern: str) -> np.ndarray:
    """Demosaic a Bayer float image into linear RGB float32 in [0, 1]."""

    pattern = cfa_pattern.upper()
    if pattern not in _BAYER_TO_RGB_EA:
        raise ValueError(f"Unsupported 2x2 Bayer pattern for preview: {cfa_pattern}")
    bayer_u16 = np.round(np.clip(bayer, 0.0, 1.0) * 65535.0).astype(np.uint16)
    rgb_u16 = cv2.cvtColor(bayer_u16, _BAYER_TO_RGB_EA[pattern])
    return (rgb_u16.astype(np.float32) / 65535.0).astype(np.float32)


def save_preview_png(
    rgb_linear: np.ndarray,
    path: str | Path,
    gamma: float = 2.2,
    low_percentile: float = 0.5,
    high_percentile: float = 99.5,
) -> None:
    """Save an 8-bit display preview with percentile stretch and optional gamma."""

    display = _percentile_stretch(rgb_linear, low_percentile, high_percentile)
    if gamma > 0.0 and abs(gamma - 1.0) > 1e-3:
        display = np.power(np.clip(display, 0.0, 1.0), 1.0 / gamma)
    _write_rgb_png(path, np.round(np.clip(display, 0.0, 1.0) * 255.0).astype(np.uint8))


def save_linear_rgb16_png(rgb_linear: np.ndarray, path: str | Path) -> None:
    """Save clipped linear RGB as a 16-bit PNG for inspection."""

    u16 = np.round(np.clip(rgb_linear, 0.0, 1.0) * 65535.0).astype(np.uint16)
    _write_rgb_png(path, u16)


def save_display_rgb_png(rgb_display: np.ndarray, path: str | Path) -> None:
    """Save display-referred RGB in [0, 1] without stretching or extra gamma."""

    u8 = np.round(np.clip(rgb_display, 0.0, 1.0) * 255.0).astype(np.uint8)
    _write_rgb_png(path, u8)


def save_illumination_preview(
    illumination_bayer: np.ndarray,
    cfa_pattern: str,
    path: str | Path,
) -> None:
    """Demosaic and save the normalized illumination field for visual checking."""

    rgb = demosaic_to_rgb(_normalise_illumination_for_display(illumination_bayer), cfa_pattern)
    save_preview_png(rgb, path, gamma=1.0, low_percentile=0.1, high_percentile=99.9)


def _percentile_stretch(
    rgb_linear: np.ndarray,
    low_percentile: float,
    high_percentile: float,
) -> np.ndarray:
    finite = rgb_linear[np.isfinite(rgb_linear)]
    if finite.size == 0:
        return np.zeros_like(rgb_linear, dtype=np.float32)
    lo = float(np.percentile(finite, low_percentile))
    hi = float(np.percentile(finite, high_percentile))
    if hi <= lo:
        hi = lo + 1e-6
    return ((rgb_linear.astype(np.float32) - lo) / (hi - lo)).astype(np.float32)


def _normalise_illumination_for_display(illumination_bayer: np.ndarray) -> np.ndarray:
    illum = illumination_bayer.astype(np.float32)
    lo = float(np.percentile(illum, 0.1))
    hi = float(np.percentile(illum, 99.9))
    if hi <= lo:
        hi = lo + 1e-6
    return np.clip((illum - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _write_rgb_png(path: str | Path, rgb: np.ndarray) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok = cv2.imwrite(str(out_path), bgr)
    if not ok:
        raise OSError(f"Failed to write image: {out_path}")
