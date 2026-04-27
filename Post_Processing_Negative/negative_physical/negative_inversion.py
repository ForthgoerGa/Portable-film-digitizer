"""Base correction, density calculation, density-domain unmixing, and inversion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_EPS = 1e-6
_DEFAULT_COLOR_UNMIX_MATRIX = np.array(
    [
        [1.55, -0.38, -0.17],
        [-0.22, 1.44, -0.22],
        [-0.08, -0.45, 1.53],
    ],
    dtype=np.float32,
)
_DEFAULT_INVERSION_CHANNEL_GAINS = np.array([1.35, 1.20, 1.10], dtype=np.float32)


@dataclass(frozen=True)
class BaseReference:
    """Film base / orange-mask reference sampled from the base frame."""

    base_rgb: list[float]
    roi_name: str
    bbox: list[int]
    sample_count: int
    source_frame: str
    stats: dict[str, Any]


@dataclass(frozen=True)
class NegativeStageResult:
    """Outputs for stages [6]-[9]."""

    base_corrected_rgb: np.ndarray
    density_rgb: np.ndarray
    density_norm_rgb: np.ndarray
    density_unmixed_rgb: np.ndarray
    density_unmixed_norm_rgb: np.ndarray
    inverted_rgb: np.ndarray
    diagnostics: dict[str, Any]


def estimate_base_reference(
    base_frame_rgb: np.ndarray,
    reference_rois: list[dict[str, Any]],
    source_frame: str,
    roi_name: str = "base",
) -> BaseReference:
    """Estimate base RGB from the marked base ROI in the base frame."""

    roi = _find_roi(reference_rois, roi_name)
    bbox = roi.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"Reference ROI '{roi_name}' must provide a rectangular bbox.")

    h, w = base_frame_rgb.shape[:2]
    x0, y0, x1, y1 = _clamp_bbox(bbox, width=w, height=h)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"Reference ROI '{roi_name}' is empty after clamping: {bbox}")

    crop = np.clip(base_frame_rgb[y0:y1, x0:x1].astype(np.float32), 0.0, 1.0)
    pixels = crop.reshape(-1, 3)
    luma = _luma(pixels)

    # Keep the stable central luma band.  This rejects hard ROI borders, dust,
    # and small content intrusions without assuming the future ROI format.
    lo = float(np.percentile(luma, 10.0))
    hi = float(np.percentile(luma, 90.0))
    keep = (luma >= lo) & (luma <= hi)
    if int(keep.sum()) >= 256:
        pixels = pixels[keep]

    base_rgb = np.median(pixels, axis=0).astype(np.float32)
    base_rgb = np.clip(base_rgb, _EPS, 1.0)

    stats = {
        "crop_shape": [int(crop.shape[0]), int(crop.shape[1])],
        "rgb_mean": [float(v) for v in crop.reshape(-1, 3).mean(axis=0)],
        "rgb_median": [float(v) for v in base_rgb],
        "rgb_std": [float(v) for v in crop.reshape(-1, 3).std(axis=0)],
        "luma_p10": lo,
        "luma_p90": hi,
        "used_fraction": float(pixels.shape[0] / max(crop.reshape(-1, 3).shape[0], 1)),
    }

    return BaseReference(
        base_rgb=[float(v) for v in base_rgb],
        roi_name=str(roi.get("name", roi_name)),
        bbox=[int(x0), int(y0), int(x1), int(y1)],
        sample_count=int(pixels.shape[0]),
        source_frame=source_frame,
        stats=stats,
    )


def process_negative_stages(
    rgb_linear: np.ndarray,
    base_reference: BaseReference,
    density_percentile: float = 99.0,
    curve_strength: float = 2.2,
    color_unmix_matrix: np.ndarray | list[float] | list[list[float]] | None = None,
    color_unmix_strength: float = 1.0,
    neutral_balance: bool = True,
    inversion_channel_gains: np.ndarray | list[float] | None = None,
    inversion_output_percentile: float = 99.5,
    fixed_stats: dict[str, Any] | None = None,
) -> NegativeStageResult:
    """Run [6] base correction, [7] density, [8] unmixing, and [9] inversion."""

    base = np.asarray(base_reference.base_rgb, dtype=np.float32)
    frame = np.clip(rgb_linear.astype(np.float32), _EPS, None)

    base_corrected = frame / base[np.newaxis, np.newaxis, :]
    transmission = np.clip(base_corrected, _EPS, 1.0)
    density = -np.log(transmission).astype(np.float32)

    fixed_stats = fixed_stats or {}
    density_upper = _fixed_or_percentile(
        density,
        fixed_stats.get("density_upper_rgb"),
        density_percentile,
    )
    density_upper = np.clip(density_upper, 0.05, None)
    density_norm = np.clip(density / density_upper[np.newaxis, np.newaxis, :], 0.0, 1.0)

    density_unmixed, color_unmix_diagnostics = apply_color_unmix(
        density,
        matrix=color_unmix_matrix,
        strength=color_unmix_strength,
        neutral_balance=neutral_balance,
        fixed_neutral_gains=fixed_stats.get("color_unmix_neutral_gains"),
        clip_input=False,
        output_min=0.0,
        output_max=None,
    )
    density_unmixed_upper = _fixed_or_percentile(
        density_unmixed,
        fixed_stats.get("density_unmixed_upper_rgb"),
        density_percentile,
    )
    density_unmixed_upper = np.clip(density_unmixed_upper, 0.05, None)
    density_unmixed_norm = np.clip(
        density_unmixed / density_unmixed_upper[np.newaxis, np.newaxis, :],
        0.0,
        1.0,
    )

    inverted, inversion_diagnostics = invert_density_expm1(
        density_unmixed_norm,
        channel_gains=inversion_channel_gains,
        output_percentile=inversion_output_percentile,
        fixed_positive_upper=fixed_stats.get("inversion_positive_upper_rgb"),
    )

    diagnostics = {
        "base_rgb": [float(v) for v in base],
        "density_percentile": float(density_percentile),
        "density_upper_rgb": [float(v) for v in density_upper],
        "density_unmixed_upper_rgb": [float(v) for v in density_unmixed_upper],
        "legacy_curve_strength_ignored": float(curve_strength),
        "base_corrected_mean_rgb": [float(v) for v in np.clip(base_corrected, 0.0, 4.0).mean(axis=(0, 1))],
        "density_mean_rgb": [float(v) for v in density.mean(axis=(0, 1))],
        "density_p95_rgb": [float(v) for v in np.percentile(density.reshape(-1, 3), 95.0, axis=0)],
        "density_unmixed_mean_rgb": [float(v) for v in density_unmixed.mean(axis=(0, 1))],
        "density_unmixed_p95_rgb": [float(v) for v in np.percentile(density_unmixed.reshape(-1, 3), 95.0, axis=0)],
        "inverted_mean_rgb": [float(v) for v in inverted.mean(axis=(0, 1))],
        "inverted_clipped_low_fraction": float((inverted <= 0.001).mean()),
        "inverted_clipped_high_fraction": float((inverted >= 0.999).mean()),
        "inversion": inversion_diagnostics,
        "color_unmix": color_unmix_diagnostics,
        "stage_order": "base_correction -> density -> color_unmix_density -> expm1_inversion",
    }

    return NegativeStageResult(
        base_corrected_rgb=np.clip(base_corrected, 0.0, 1.0).astype(np.float32),
        density_rgb=density.astype(np.float32),
        density_norm_rgb=density_norm.astype(np.float32),
        density_unmixed_rgb=density_unmixed.astype(np.float32),
        density_unmixed_norm_rgb=density_unmixed_norm.astype(np.float32),
        inverted_rgb=inverted,
        diagnostics=diagnostics,
    )


def invert_density_expm1(
    density_unmixed_norm: np.ndarray,
    channel_gains: np.ndarray | list[float] | None = None,
    output_percentile: float = 99.5,
    fixed_positive_upper: np.ndarray | list[float] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Invert normalized density with an expm1 curve and per-channel normalization."""

    density = np.clip(density_unmixed_norm.astype(np.float32), 0.0, None)
    gains = _normalise_channel_vector(channel_gains, _DEFAULT_INVERSION_CHANNEL_GAINS)
    output_percentile = float(np.clip(output_percentile, 90.0, 100.0))

    scaled_density = np.clip(density * gains[np.newaxis, np.newaxis, :], 0.0, 8.0)
    positive = np.expm1(scaled_density).astype(np.float32)
    upper = _fixed_or_percentile(positive, fixed_positive_upper, output_percentile)
    upper = np.clip(upper, _EPS, None)
    normalized = np.clip(positive / upper[np.newaxis, np.newaxis, :], 0.0, 1.0).astype(np.float32)

    diagnostics = {
        "method": "expm1_channel_gain_percentile",
        "channel_gains": [float(v) for v in gains],
        "output_percentile": output_percentile,
        "positive_upper_rgb": [float(v) for v in upper],
        "positive_upper_source": "global_fixed" if fixed_positive_upper is not None else "per_frame_percentile",
        "positive_p95_rgb": [float(v) for v in np.percentile(positive.reshape(-1, 3), 95.0, axis=0)],
        "normalized_mean_rgb": [float(v) for v in normalized.mean(axis=(0, 1))],
        "normalized_p95_rgb": [float(v) for v in np.percentile(normalized.reshape(-1, 3), 95.0, axis=0)],
        "normalized_clipped_high_fraction": float((normalized >= 0.999).mean()),
    }
    return normalized, diagnostics


def apply_color_unmix(
    channel_data: np.ndarray,
    matrix: np.ndarray | list[float] | list[list[float]] | None = None,
    strength: float = 0.65,
    neutral_balance: bool = True,
    fixed_neutral_gains: np.ndarray | list[float] | None = None,
    clip_input: bool = True,
    output_min: float | None = 0.0,
    output_max: float | None = 1.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply a conservative 3x3 dye-channel unmixing pass."""

    data = channel_data.astype(np.float32)
    if clip_input:
        data = np.clip(data, 0.0, 1.0)
    unmix_matrix = _normalise_color_unmix_matrix(matrix)
    strength = max(float(strength), 0.0)

    matrix_result = np.tensordot(data, unmix_matrix.T, axes=1).astype(np.float32)
    blended = data + strength * (matrix_result - data)

    preclip_low_fraction = None if output_min is None else float((blended < output_min).mean())
    preclip_high_fraction = None if output_max is None else float((blended > output_max).mean())
    if output_min is not None:
        blended = np.maximum(blended, float(output_min))
    if output_max is not None:
        blended = np.minimum(blended, float(output_max))
    blended = blended.astype(np.float32)

    balance_diagnostics: dict[str, Any] = {
        "enabled": bool(neutral_balance),
        "gains": [1.0, 1.0, 1.0],
    }
    if neutral_balance:
        if fixed_neutral_gains is not None:
            gains = _normalise_channel_vector(fixed_neutral_gains, np.ones(3, dtype=np.float32))
            balance_diagnostics = {
                "enabled": True,
                "mode": "global_fixed",
                "gains": [float(v) for v in gains],
            }
        else:
            gains, balance_diagnostics = _estimate_mid_neutral_gains(blended)
        blended = blended * gains[np.newaxis, np.newaxis, :]
        if output_min is not None:
            blended = np.maximum(blended, float(output_min))
        if output_max is not None:
            blended = np.minimum(blended, float(output_max))
        blended = blended.astype(np.float32)

    diagnostics = {
        "matrix": [[float(v) for v in row] for row in unmix_matrix.tolist()],
        "strength": strength,
        "clip_input": bool(clip_input),
        "output_min": output_min,
        "output_max": output_max,
        "neutral_balance": balance_diagnostics,
        "preclip_low_fraction": preclip_low_fraction,
        "preclip_high_fraction": preclip_high_fraction,
        "mean_rgb": [float(v) for v in blended.mean(axis=(0, 1))],
        "p5_rgb": [float(v) for v in np.percentile(blended.reshape(-1, 3), 5.0, axis=0)],
        "p95_rgb": [float(v) for v in np.percentile(blended.reshape(-1, 3), 95.0, axis=0)],
        "clipped_low_fraction": None if output_min is None else float((blended <= output_min + 0.001).mean()),
        "clipped_high_fraction": None if output_max is None else float((blended >= output_max - 0.001).mean()),
    }
    return blended.astype(np.float32), diagnostics


def base_reference_to_dict(base_reference: BaseReference) -> dict[str, Any]:
    return asdict(base_reference)


def base_reference_from_dict(payload: dict[str, Any]) -> BaseReference:
    return BaseReference(
        base_rgb=[float(v) for v in payload["base_rgb"]],
        roi_name=str(payload.get("roi_name", "base")),
        bbox=[int(v) for v in payload.get("bbox", [0, 0, 0, 0])],
        sample_count=int(payload.get("sample_count", 0)),
        source_frame=str(payload.get("source_frame", "global_params")),
        stats=dict(payload.get("stats", {})),
    )


def _fixed_or_percentile(
    data: np.ndarray,
    fixed_rgb: np.ndarray | list[float] | None,
    percentile: float,
) -> np.ndarray:
    if fixed_rgb is not None:
        arr = np.asarray(fixed_rgb, dtype=np.float32)
        if arr.shape != (3,):
            raise ValueError(f"Expected fixed RGB stats with shape (3,), got {arr.shape}")
        return np.clip(arr, 0.05, None).astype(np.float32)
    return np.percentile(data.reshape(-1, 3), percentile, axis=0).astype(np.float32)


def save_base_roi_overlay(
    rgb_linear: np.ndarray,
    base_reference: BaseReference,
    path: str | Path,
) -> None:
    """Save a preview of the base frame with the sampled ROI rectangle."""

    preview = _display_u8(rgb_linear)
    x0, y0, x1, y1 = base_reference.bbox
    cv2.rectangle(preview, (x0, y0), (x1, y1), (255, 64, 64), 6)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(preview, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(out_path), bgr):
        raise OSError(f"Failed to write image: {out_path}")


def _find_roi(reference_rois: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for roi in reference_rois:
        if str(roi.get("name", "")).lower() == name.lower():
            return roi
    names = [str(roi.get("name", "")) for roi in reference_rois]
    raise ValueError(f"Reference ROI '{name}' not found. Available ROIs: {names}")


def _clamp_bbox(bbox: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = [int(v) for v in bbox]
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    return x0, y0, x1, y1


def _luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _normalise_color_unmix_matrix(
    matrix: np.ndarray | list[float] | list[list[float]] | None,
) -> np.ndarray:
    if matrix is None:
        return _DEFAULT_COLOR_UNMIX_MATRIX.copy()

    arr = np.asarray(matrix, dtype=np.float32)
    if arr.size == 9:
        arr = arr.reshape(3, 3)
    if arr.shape != (3, 3):
        raise ValueError(f"Color unmix matrix must be 3x3 or 9 values, got {arr.shape}.")
    return arr.astype(np.float32)


def _normalise_channel_vector(
    values: np.ndarray | list[float] | None,
    default: np.ndarray,
) -> np.ndarray:
    if values is None:
        return default.copy()
    arr = np.asarray(values, dtype=np.float32)
    if arr.shape != (3,):
        raise ValueError(f"Expected 3 channel values, got shape {arr.shape}.")
    return arr.astype(np.float32)


def _estimate_mid_neutral_gains(rgb: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    img = rgb.astype(np.float32)
    pixels = img.reshape(-1, 3)
    finite = np.isfinite(pixels).all(axis=1)
    if not bool(finite.any()):
        gains = np.ones(3, dtype=np.float32)
        return gains, {
            "enabled": True,
            "gains": [1.0, 1.0, 1.0],
            "sample_fraction": 0.0,
            "note": "no finite pixels",
        }

    pixels = pixels[finite]
    luma = _luma(pixels)
    saturation = pixels.max(axis=1) - pixels.min(axis=1)
    lo = float(np.percentile(luma, 20.0))
    hi = float(np.percentile(luma, 80.0))
    sat_hi = float(np.percentile(saturation, 70.0))
    keep = (luma >= lo) & (luma <= hi) & (saturation <= sat_hi)
    if int(keep.sum()) < 1024:
        keep = (luma >= lo) & (luma <= hi)
    if int(keep.sum()) < 256:
        keep = np.ones(pixels.shape[0], dtype=bool)

    sample = pixels[keep]
    median_rgb = np.clip(np.median(sample, axis=0).astype(np.float32), _EPS, None)
    target = float(np.mean(median_rgb))
    gains = np.clip(target / median_rgb, 0.78, 1.28).astype(np.float32)

    return gains, {
        "enabled": True,
        "gains": [float(v) for v in gains],
        "sample_fraction": float(sample.shape[0] / max(pixels.shape[0], 1)),
        "sample_median_rgb": [float(v) for v in median_rgb],
        "target_median": target,
        "luma_p20": lo,
        "luma_p80": hi,
        "saturation_p70": sat_hi,
    }


def _display_u8(rgb_linear: np.ndarray) -> np.ndarray:
    img = np.clip(rgb_linear.astype(np.float32), 0.0, 1.0)
    finite = img[np.isfinite(img)]
    if finite.size:
        lo = float(np.percentile(finite, 0.5))
        hi = float(np.percentile(finite, 99.5))
        if hi <= lo:
            hi = lo + 1e-6
        img = np.clip((img - lo) / (hi - lo), 0.0, 1.0)
    img = np.power(img, 1.0 / 2.2)
    return np.round(img * 255.0).astype(np.uint8)
