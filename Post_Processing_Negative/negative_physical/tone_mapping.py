"""Filmic tone mapping for preview/render output from the negative pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_EPS = 1e-6
_LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass(frozen=True)
class ToneMapResult:
    """Intermediate tone-map stages and final display output."""

    exposure_aligned_rgb: np.ndarray
    compressed_rgb: np.ndarray
    contrast_rgb: np.ndarray
    highlight_rolloff_rgb: np.ndarray
    output_srgb: np.ndarray
    diagnostics: dict[str, Any]


def apply_filmic_tone_map(
    linear_positive: np.ndarray,
    middle_gray: float = 0.30,
    dynamic_compression: float = 1.0,
    sigmoid_contrast: float = 5.5,
    sigmoid_pivot: float = 0.43,
    sigmoid_strength: float = 0.55,
    highlight_rolloff_start: float = 0.68,
    highlight_rolloff_strength: float = 1.35,
    highlight_desaturation: float = 0.22,
) -> ToneMapResult:
    """Apply exposure alignment, filmic compression, S contrast, roll-off, and sRGB gamma."""

    img = np.clip(linear_positive.astype(np.float32), 0.0, None)

    exposure_aligned, exposure_diag = _align_middle_gray(img, middle_gray=middle_gray)
    compressed, compression_diag = _compress_dynamic_range(
        exposure_aligned,
        strength=dynamic_compression,
    )
    contrast, contrast_diag = _apply_sigmoid_contrast(
        compressed,
        contrast=sigmoid_contrast,
        pivot=sigmoid_pivot,
        strength=sigmoid_strength,
    )
    highlight_rolloff, rolloff_diag = _apply_highlight_rolloff(
        contrast,
        start=highlight_rolloff_start,
        strength=highlight_rolloff_strength,
        desaturation=highlight_desaturation,
    )
    output_srgb = _linear_to_srgb(np.clip(highlight_rolloff, 0.0, 1.0))

    diagnostics = {
        "middle_gray": float(middle_gray),
        "dynamic_compression": float(dynamic_compression),
        "sigmoid_contrast": float(sigmoid_contrast),
        "sigmoid_pivot": float(sigmoid_pivot),
        "sigmoid_strength": float(sigmoid_strength),
        "highlight_rolloff_start": float(highlight_rolloff_start),
        "highlight_rolloff_strength": float(highlight_rolloff_strength),
        "highlight_desaturation": float(highlight_desaturation),
        "exposure": exposure_diag,
        "compression": compression_diag,
        "contrast": contrast_diag,
        "highlight_rolloff": rolloff_diag,
        "output_srgb_mean_rgb": [float(v) for v in output_srgb.mean(axis=(0, 1))],
        "output_srgb_p95_rgb": [float(v) for v in np.percentile(output_srgb.reshape(-1, 3), 95.0, axis=0)],
        "output_srgb_clipped_low_fraction": float((output_srgb <= 0.001).mean()),
        "output_srgb_clipped_high_fraction": float((output_srgb >= 0.999).mean()),
    }

    return ToneMapResult(
        exposure_aligned_rgb=exposure_aligned.astype(np.float32),
        compressed_rgb=compressed.astype(np.float32),
        contrast_rgb=contrast.astype(np.float32),
        highlight_rolloff_rgb=highlight_rolloff.astype(np.float32),
        output_srgb=output_srgb.astype(np.float32),
        diagnostics=diagnostics,
    )


def _align_middle_gray(rgb: np.ndarray, middle_gray: float) -> tuple[np.ndarray, dict[str, Any]]:
    luma = _luma(rgb)
    finite = luma[np.isfinite(luma)]
    if finite.size == 0:
        gain = 1.0
        source_mid = 0.0
    else:
        lo = float(np.percentile(finite, 20.0))
        hi = float(np.percentile(finite, 80.0))
        mid_band = finite[(finite >= lo) & (finite <= hi)]
        if mid_band.size < 1024:
            mid_band = finite
        source_mid = float(np.median(mid_band))
        gain = float(middle_gray) / max(source_mid, _EPS)
        gain = float(np.clip(gain, 0.25, 4.0))

    aligned = rgb * gain
    return aligned.astype(np.float32), {
        "source_middle_luma": source_mid,
        "target_middle_gray": float(middle_gray),
        "exposure_gain": gain,
        "exposure_ev": float(np.log2(max(gain, _EPS))),
    }


def _compress_dynamic_range(rgb: np.ndarray, strength: float) -> tuple[np.ndarray, dict[str, Any]]:
    strength = float(np.clip(strength, 0.0, 2.0))
    y = _luma(rgb)
    finite = y[np.isfinite(y)]
    white_point = float(np.percentile(finite, 99.5)) if finite.size else 1.0
    white_point = max(white_point, 1.0)

    reinhard = (y * (1.0 + y / max(white_point * white_point, _EPS))) / (1.0 + y)
    reinhard = np.clip(reinhard, 0.0, 1.0)
    mapped_y = y + strength * (reinhard - y)
    mapped_y = np.clip(mapped_y, 0.0, 1.0)

    mapped = _apply_luma_ratio(rgb, y, mapped_y)
    return mapped, {
        "white_point_luma": white_point,
        "strength": strength,
        "input_luma_p95": float(np.percentile(finite, 95.0)) if finite.size else 0.0,
        "output_luma_p95": float(np.percentile(_luma(mapped), 95.0)),
    }


def _apply_sigmoid_contrast(
    rgb: np.ndarray,
    contrast: float,
    pivot: float,
    strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    contrast = max(float(contrast), 0.1)
    pivot = float(np.clip(pivot, 0.05, 0.95))
    strength = float(np.clip(strength, 0.0, 1.0))

    y = np.clip(_luma(rgb), 0.0, 1.0)
    curve = 1.0 / (1.0 + np.exp(-contrast * (y - pivot)))
    black = 1.0 / (1.0 + np.exp(-contrast * (0.0 - pivot)))
    white = 1.0 / (1.0 + np.exp(-contrast * (1.0 - pivot)))
    curved_y = np.clip((curve - black) / max(white - black, _EPS), 0.0, 1.0)
    mapped_y = y + strength * (curved_y - y)

    mapped = _apply_luma_ratio(rgb, y, mapped_y)
    return mapped, {
        "contrast": contrast,
        "pivot": pivot,
        "strength": strength,
        "input_luma_p10": float(np.percentile(y, 10.0)),
        "output_luma_p10": float(np.percentile(_luma(mapped), 10.0)),
        "input_luma_p90": float(np.percentile(y, 90.0)),
        "output_luma_p90": float(np.percentile(_luma(mapped), 90.0)),
    }


def _apply_highlight_rolloff(
    rgb: np.ndarray,
    start: float,
    strength: float,
    desaturation: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    start = float(np.clip(start, 0.3, 0.95))
    strength = max(float(strength), 0.0)
    desaturation = float(np.clip(desaturation, 0.0, 1.0))

    y = np.clip(_luma(rgb), 0.0, 1.0)
    t = np.clip((y - start) / max(1.0 - start, _EPS), 0.0, 1.0)
    shoulder_t = t / (1.0 + strength * (1.0 - t))
    rolled_y = np.where(t > 0.0, start + (1.0 - start) * shoulder_t, y)
    rolled_y = np.clip(rolled_y, 0.0, 1.0)

    mapped = _apply_luma_ratio(rgb, y, rolled_y)
    highlight_mask = _smoothstep(start, 1.0, rolled_y)[..., np.newaxis]
    gray = rolled_y[..., np.newaxis]
    sat_scale = 1.0 - desaturation * highlight_mask
    mapped = gray + (mapped - gray) * sat_scale
    mapped = np.clip(mapped, 0.0, 1.0).astype(np.float32)

    highlight_fraction = float((y >= start).mean())
    return mapped, {
        "start": start,
        "strength": strength,
        "desaturation": desaturation,
        "highlight_fraction": highlight_fraction,
        "input_luma_p99": float(np.percentile(y, 99.0)),
        "output_luma_p99": float(np.percentile(_luma(mapped), 99.0)),
    }


def _apply_luma_ratio(rgb: np.ndarray, old_luma: np.ndarray, new_luma: np.ndarray) -> np.ndarray:
    ratio = new_luma / np.maximum(old_luma, _EPS)
    mapped = rgb * ratio[..., np.newaxis]
    return np.clip(mapped, 0.0, 1.0).astype(np.float32)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return np.tensordot(rgb.astype(np.float32), _LUMA_WEIGHTS, axes=1).astype(np.float32)


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / max(edge1 - edge0, _EPS), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)
    return np.where(
        rgb <= 0.0031308,
        12.92 * rgb,
        1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
    ).astype(np.float32)
