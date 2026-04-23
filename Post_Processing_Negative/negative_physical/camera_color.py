"""Stage [10] soft reference color stabilization for post-inversion RGB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

STAGE10_MATRIX_CONSERVATIVE = np.array(
    [
        [1.04, -0.02, -0.02],
        [-0.03, 1.01, 0.02],
        [-0.01, 0.02, 0.99],
    ],
    dtype=np.float32,
)
STAGE10_MATRIX_MEDIUM = np.array(
    [
        [1.08, -0.04, -0.04],
        [-0.06, 1.03, 0.03],
        [-0.02, 0.04, 0.98],
    ],
    dtype=np.float32,
)
STAGE10_MATRIX_AGGRESSIVE = np.array(
    [
        [1.12, -0.06, -0.06],
        [-0.08, 1.04, 0.04],
        [-0.03, 0.05, 0.98],
    ],
    dtype=np.float32,
)
STAGE10_MATRIX_PRESETS = {
    "conservative": STAGE10_MATRIX_CONSERVATIVE,
    "medium": STAGE10_MATRIX_MEDIUM,
    "aggressive": STAGE10_MATRIX_AGGRESSIVE,
}
STAGE10_DEFAULT_PARAMS: dict[str, Any] = {
    "gray_anchor_enabled": True,
    "gray_anchor_percentile": 30.0,
    "gray_anchor_strength": 0.35,
    "gray_anchor_eps": 1e-6,
    "soft_matrix_enabled": True,
    "soft_matrix_strength": 0.15,
    "neutral_damp_enabled": True,
    "neutral_damp_strength": 0.25,
    "neutral_damp_sigma": 0.10,
    "red_guard_enabled": True,
    "red_guard_threshold": 0.05,
    "red_guard_strength": 0.30,
}


@dataclass(frozen=True)
class Stage10SoftReferenceResult:
    """Stage [10] output and diagnostics."""

    mapped_rgb: np.ndarray
    diagnostics: dict[str, Any]


def stage10_soft_reference_mapping(
    img: np.ndarray,
    empirical_matrix: np.ndarray | list[float] | list[list[float]] | None = None,
    gray_anchor_enabled: bool = True,
    gray_anchor_percentile: float = 30.0,
    gray_anchor_strength: float = 0.35,
    gray_anchor_eps: float = 1e-6,
    soft_matrix_enabled: bool = True,
    soft_matrix_strength: float = 0.15,
    neutral_damp_enabled: bool = True,
    neutral_damp_strength: float = 0.25,
    neutral_damp_sigma: float = 0.10,
    red_guard_enabled: bool = True,
    red_guard_threshold: float = 0.05,
    red_guard_strength: float = 0.30,
) -> np.ndarray:
    """Apply weak reference correction to post-inversion linear RGB.

    [10a] weak gray anchor: uses low-saturation regions to softly stabilize
    near-neutral balance without forcing the image to a hard gray card.
    [10b] soft matrix: blends a small empirical matrix with identity, avoiding
    the heavy-handed behavior of a full color transform.
    [10c] neutral damp: pulls low-chroma pixels slightly toward their own
    luminance-equivalent gray to reduce broad red/green casts.
    [10d] red guard: compresses only red excess above a threshold to control
    whole-image red bias while leaving normal red detail mostly intact.
    """

    out, _ = _stage10_soft_reference_mapping_impl(
        img=img,
        empirical_matrix=empirical_matrix,
        gray_anchor_enabled=gray_anchor_enabled,
        gray_anchor_percentile=gray_anchor_percentile,
        gray_anchor_strength=gray_anchor_strength,
        gray_anchor_eps=gray_anchor_eps,
        soft_matrix_enabled=soft_matrix_enabled,
        soft_matrix_strength=soft_matrix_strength,
        neutral_damp_enabled=neutral_damp_enabled,
        neutral_damp_strength=neutral_damp_strength,
        neutral_damp_sigma=neutral_damp_sigma,
        red_guard_enabled=red_guard_enabled,
        red_guard_threshold=red_guard_threshold,
        red_guard_strength=red_guard_strength,
    )
    return out


def apply_stage10_soft_reference_mapping(
    img: np.ndarray,
    empirical_matrix: np.ndarray | list[float] | list[list[float]] | None = None,
    **params: Any,
) -> Stage10SoftReferenceResult:
    """Run stage [10] with diagnostics for reports."""

    merged_params = dict(STAGE10_DEFAULT_PARAMS)
    merged_params.update(params)
    out, diagnostics = _stage10_soft_reference_mapping_impl(
        img=img,
        empirical_matrix=empirical_matrix,
        **merged_params,
    )
    return Stage10SoftReferenceResult(mapped_rgb=out, diagnostics=diagnostics)


def _stage10_soft_reference_mapping_impl(
    img: np.ndarray,
    empirical_matrix: np.ndarray | list[float] | list[list[float]] | None,
    gray_anchor_enabled: bool,
    gray_anchor_percentile: float,
    gray_anchor_strength: float,
    gray_anchor_eps: float,
    soft_matrix_enabled: bool,
    soft_matrix_strength: float,
    neutral_damp_enabled: bool,
    neutral_damp_strength: float,
    neutral_damp_sigma: float,
    red_guard_enabled: bool,
    red_guard_threshold: float,
    red_guard_strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    out = _validate_rgb_image(img)
    empirical = _normalise_matrix(empirical_matrix) if empirical_matrix is not None else STAGE10_MATRIX_MEDIUM.copy()
    diagnostics: dict[str, Any] = {
        "stage": "stage10_soft_reference_mapping",
        "note": "Soft post-inversion reference correction; no DNG ColorMatrix, no gamma, no tone mapping.",
        "empirical_matrix": [[float(v) for v in row] for row in empirical.tolist()],
        "input_mean_rgb": [float(v) for v in out.mean(axis=(0, 1))],
        "steps": {},
    }

    if gray_anchor_enabled:
        out, gray_diag = _weak_gray_anchor(
            out,
            percentile=gray_anchor_percentile,
            strength=gray_anchor_strength,
            eps=gray_anchor_eps,
        )
    else:
        gray_diag = {"enabled": False}
    diagnostics["steps"]["10a_weak_gray_anchor"] = gray_diag

    if soft_matrix_enabled:
        out, matrix_diag = _soft_matrix(
            out,
            empirical_matrix=empirical,
            strength=soft_matrix_strength,
        )
    else:
        matrix_diag = {"enabled": False}
    diagnostics["steps"]["10b_soft_matrix"] = matrix_diag

    if neutral_damp_enabled:
        out, neutral_diag = _neutral_damp(
            out,
            strength=neutral_damp_strength,
            sigma=neutral_damp_sigma,
        )
    else:
        neutral_diag = {"enabled": False}
    diagnostics["steps"]["10c_neutral_damp"] = neutral_diag

    if red_guard_enabled:
        out, red_diag = _red_guard(
            out,
            threshold=red_guard_threshold,
            strength=red_guard_strength,
        )
    else:
        red_diag = {"enabled": False}
    diagnostics["steps"]["10d_red_guard"] = red_diag

    out = np.maximum(out, 0.0).astype(np.float32)
    diagnostics.update(
        {
            "output_mean_rgb": [float(v) for v in out.mean(axis=(0, 1))],
            "output_p95_rgb": [float(v) for v in np.percentile(out.reshape(-1, 3), 95.0, axis=0)],
            "output_min_rgb": [float(v) for v in out.reshape(-1, 3).min(axis=0)],
            "output_max_rgb": [float(v) for v in out.reshape(-1, 3).max(axis=0)],
            "clipped_low_fraction": float((out <= 0.001).mean()),
        }
    )
    return out, diagnostics


def _weak_gray_anchor(
    img: np.ndarray,
    percentile: float,
    strength: float,
    eps: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    sat_proxy = img.max(axis=2) - img.min(axis=2)
    threshold = float(np.percentile(sat_proxy, float(np.clip(percentile, 0.0, 100.0))))
    mask = sat_proxy <= threshold
    if not bool(mask.any()):
        mask = np.ones(sat_proxy.shape, dtype=bool)
    g = img[mask].mean(axis=0).astype(np.float32)
    strength = float(np.clip(strength, 0.0, 1.0))
    eps = max(float(eps), 1e-12)
    g_eff = (1.0 - strength) * np.ones(3, dtype=np.float32) + strength * g
    out = img / (g_eff[np.newaxis, np.newaxis, :] + eps)
    return out.astype(np.float32), {
        "enabled": True,
        "percentile": float(percentile),
        "threshold": threshold,
        "strength": strength,
        "gray_mean_rgb": [float(v) for v in g],
        "g_eff": [float(v) for v in g_eff],
        "mask_fraction": float(mask.mean()),
    }


def _soft_matrix(
    img: np.ndarray,
    empirical_matrix: np.ndarray,
    strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    strength = float(np.clip(strength, 0.0, 1.0))
    identity = np.eye(3, dtype=np.float32)
    matrix_soft = (1.0 - strength) * identity + strength * empirical_matrix
    out = np.tensordot(img, matrix_soft.T, axes=1).astype(np.float32)
    return out, {
        "enabled": True,
        "strength": strength,
        "matrix_soft": [[float(v) for v in row] for row in matrix_soft.tolist()],
    }


def _neutral_damp(
    img: np.ndarray,
    strength: float,
    sigma: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    strength = float(np.clip(strength, 0.0, 1.0))
    sigma = max(float(sigma), 1e-6)
    chroma_proxy = img.max(axis=2) - img.min(axis=2)
    w = np.exp(-((chroma_proxy * chroma_proxy) / (2.0 * sigma * sigma))).astype(np.float32)
    m = img.mean(axis=2, keepdims=True)
    blend = strength * w[..., np.newaxis]
    out = (1.0 - blend) * img + blend * m
    return out.astype(np.float32), {
        "enabled": True,
        "strength": strength,
        "sigma": sigma,
        "mean_weight": float(w.mean()),
        "p95_weight": float(np.percentile(w, 95.0)),
    }


def _red_guard(
    img: np.ndarray,
    threshold: float,
    strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    out = img.copy()
    threshold = float(threshold)
    strength = float(np.clip(strength, 0.0, 1.0))
    r_excess = out[..., 0] - 0.5 * (out[..., 1] + out[..., 2])
    reduction = strength * np.maximum(r_excess - threshold, 0.0)
    out[..., 0] = out[..., 0] - reduction
    return out.astype(np.float32), {
        "enabled": True,
        "threshold": threshold,
        "strength": strength,
        "affected_fraction": float((reduction > 0.0).mean()),
        "mean_reduction": float(reduction.mean()),
        "p95_reduction": float(np.percentile(reduction, 95.0)),
    }


def _validate_rgb_image(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Expected RGB image with shape (H, W, 3), got {arr.shape}.")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return np.maximum(arr, 0.0).astype(np.float32)


def _normalise_matrix(matrix: np.ndarray | list[float] | list[list[float]]) -> np.ndarray:
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.size == 9:
        arr = arr.reshape(3, 3)
    if arr.shape != (3, 3):
        raise ValueError(f"Expected a 3x3 matrix or 9 values, got {arr.shape}.")
    return arr.astype(np.float32)
