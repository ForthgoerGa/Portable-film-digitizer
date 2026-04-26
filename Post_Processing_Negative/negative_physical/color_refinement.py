"""Stage 3 pseudo-LUT color refinement for post-tone negative conversion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_EPS = 1e-6
_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float32,
)
_XYZ_TO_RGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float32,
)

STAGE3_DEFAULT_PARAMS: dict[str, Any] = {
    "lab_input_percentile": 99.5,
    "lab_input_gamma": 2.2,
    "lab_input_eps": 1e-6,
    "luma_chroma_enabled": True,
    "sat_base": 0.92,
    "sat_mid_gain": 0.34,
    "sat_mid_center": 0.48,
    "sat_mid_sigma": 0.18,
    "sat_highlight_decay": 0.18,
    "sat_scale_min": 0.75,
    "sat_scale_max": 1.25,
    "hue_adjust_enabled": True,
    "red_hue_min": -0.45,
    "red_hue_max": 0.55,
    "red_a_scale": 1.05,
    "red_b_scale": 0.94,
    "blue_abs_hue_threshold": 2.1,
    "blue_a_scale": 0.97,
    "blue_b_scale": 1.08,
    "green_hue_min": 0.9,
    "green_hue_max": 1.8,
    "green_a_scale": 0.96,
    "green_b_scale": 1.03,
    "neutral_protect_enabled": True,
    "neutral_percentile": 20.0,
    "neutral_shrink": 0.45,
    "neutral_soft_enabled": False,
    "neutral_sigma": 8.0,
    "neutral_strength": 0.55,
    "preview_enabled": True,
    "preview_percentile": 99.5,
    "preview_gamma": 2.2,
    "preview_eps": 1e-6,
}


@dataclass(frozen=True)
class Stage3PseudoLutColorRefinementResult:
    """Stage 3 linear output, display preview, debug arrays, and diagnostics."""

    stage3_linear_output: np.ndarray
    stage3_preview_output: np.ndarray
    debug: dict[str, Any]
    diagnostics: dict[str, Any]


def stage3_pseudo_lut_color_refinement(
    img: np.ndarray,
    lab_input_percentile: float = 99.5,
    lab_input_gamma: float = 2.2,
    lab_input_eps: float = 1e-6,
    luma_chroma_enabled: bool = True,
    sat_base: float = 0.92,
    sat_mid_gain: float = 0.34,
    sat_mid_center: float = 0.48,
    sat_mid_sigma: float = 0.18,
    sat_highlight_decay: float = 0.18,
    sat_scale_min: float = 0.75,
    sat_scale_max: float = 1.25,
    hue_adjust_enabled: bool = True,
    red_hue_min: float = -0.45,
    red_hue_max: float = 0.55,
    red_a_scale: float = 1.05,
    red_b_scale: float = 0.94,
    blue_abs_hue_threshold: float = 2.1,
    blue_a_scale: float = 0.97,
    blue_b_scale: float = 1.08,
    green_hue_min: float = 0.9,
    green_hue_max: float = 1.8,
    green_a_scale: float = 0.96,
    green_b_scale: float = 1.03,
    neutral_protect_enabled: bool = True,
    neutral_percentile: float = 20.0,
    neutral_shrink: float = 0.45,
    neutral_soft_enabled: bool = False,
    neutral_sigma: float = 8.0,
    neutral_strength: float = 0.55,
    preview_enabled: bool = True,
    preview_percentile: float = 99.5,
    preview_gamma: float = 2.2,
    preview_eps: float = 1e-6,
    debug: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict[str, Any]]:
    """Apply pseudo-LUT color refinement in Lab without changing luminance.

    [3a] Lab input uses preview-style percentile normalization and gamma
    encoding before conversion, so Lab receives a stable perceptual image.
    [3b] Luminance-driven chroma thickens midtones while restraining highlights.
    [3c] Hue-dependent local adjustments emulate a small rule-based pseudo LUT.
    [3d] Neutral protection pulls only low-chroma regions back toward neutral.
    [3e] Lab is written back with the original L and converted to linear output.
    [3f] Display/debug preview is generated separately from linear pipeline data.

    # Acceptance standards:
    # 1. Compared with Stage 2, color regions should be more differentiated.
    # 2. Midtone color should feel thicker and more film-like, not just louder.
    # 3. Highlight color should not become dirty or fluorescent.
    # 4. Neutral regions such as walls, gray objects, snow, and high highlights should be cleaner.
    # 5. The whole image should not collapse into one color cast.
    # 6. This should move the image closer to NLP style, but it is not the final gamma output.
    """

    params = dict(STAGE3_DEFAULT_PARAMS)
    params.update(
        {
            "lab_input_percentile": lab_input_percentile,
            "lab_input_gamma": lab_input_gamma,
            "lab_input_eps": lab_input_eps,
            "luma_chroma_enabled": luma_chroma_enabled,
            "sat_base": sat_base,
            "sat_mid_gain": sat_mid_gain,
            "sat_mid_center": sat_mid_center,
            "sat_mid_sigma": sat_mid_sigma,
            "sat_highlight_decay": sat_highlight_decay,
            "sat_scale_min": sat_scale_min,
            "sat_scale_max": sat_scale_max,
            "hue_adjust_enabled": hue_adjust_enabled,
            "red_hue_min": red_hue_min,
            "red_hue_max": red_hue_max,
            "red_a_scale": red_a_scale,
            "red_b_scale": red_b_scale,
            "blue_abs_hue_threshold": blue_abs_hue_threshold,
            "blue_a_scale": blue_a_scale,
            "blue_b_scale": blue_b_scale,
            "green_hue_min": green_hue_min,
            "green_hue_max": green_hue_max,
            "green_a_scale": green_a_scale,
            "green_b_scale": green_b_scale,
            "neutral_protect_enabled": neutral_protect_enabled,
            "neutral_percentile": neutral_percentile,
            "neutral_shrink": neutral_shrink,
            "neutral_soft_enabled": neutral_soft_enabled,
            "neutral_sigma": neutral_sigma,
            "neutral_strength": neutral_strength,
            "preview_enabled": preview_enabled,
            "preview_percentile": preview_percentile,
            "preview_gamma": preview_gamma,
            "preview_eps": preview_eps,
        }
    )
    result = apply_stage3_pseudo_lut_color_refinement(img, include_debug=debug, **params)
    if debug:
        return result.stage3_linear_output, result.debug
    return result.stage3_linear_output


def apply_stage3_pseudo_lut_color_refinement(
    img: np.ndarray,
    include_debug: bool = True,
    **params: Any,
) -> Stage3PseudoLutColorRefinementResult:
    """Run Stage 3 with diagnostics and optional large debug arrays."""

    merged = dict(STAGE3_DEFAULT_PARAMS)
    merged.update(params)
    return _stage3_pseudo_lut_color_refinement_impl(img, merged, include_debug=include_debug)


def make_stage3_preview_output(
    stage3_linear_output: np.ndarray,
    preview_percentile: float = 99.5,
    preview_gamma: float = 2.2,
    preview_eps: float = 1e-6,
) -> np.ndarray:
    """Create the display/debug preview from Stage 3 linear output."""

    x = np.maximum(np.asarray(stage3_linear_output, dtype=np.float32), 0.0)
    scale = _safe_percentile(x, preview_percentile)
    x = x / (scale + max(float(preview_eps), 1e-12))
    x = np.clip(x, 0.0, 1.0)
    gamma = max(float(preview_gamma), 1e-6)
    return np.power(x, 1.0 / gamma).astype(np.float32)


def _stage3_pseudo_lut_color_refinement_impl(
    img: np.ndarray,
    params: dict[str, Any],
    include_debug: bool,
) -> Stage3PseudoLutColorRefinementResult:
    rgb_linear = _validate_rgb(img)
    img_lab_in, lab_input_diag = _prepare_lab_input(
        rgb_linear,
        percentile=float(params["lab_input_percentile"]),
        gamma=float(params["lab_input_gamma"]),
        eps=float(params["lab_input_eps"]),
    )
    lab = _srgb_like_to_lab(img_lab_in, gamma=float(params["lab_input_gamma"]))
    L = (lab[..., 0] / 100.0).astype(np.float32)
    a_before = lab[..., 1].astype(np.float32)
    b_before = lab[..., 2].astype(np.float32)
    C_before = np.sqrt(a_before * a_before + b_before * b_before).astype(np.float32)

    if bool(params["luma_chroma_enabled"]):
        sat_scale, chroma_diag = _luminance_driven_chroma(L, params)
        a1 = a_before * sat_scale
        b1 = b_before * sat_scale
    else:
        sat_scale = np.ones_like(L, dtype=np.float32)
        a1 = a_before
        b1 = b_before
        chroma_diag = {"enabled": False}

    hue_map = np.arctan2(b1, a1).astype(np.float32)
    if bool(params["hue_adjust_enabled"]):
        a2, b2, red_mask, blue_mask, green_mask, hue_diag = _hue_dependent_adjustment(
            a1,
            b1,
            hue_map,
            params,
        )
    else:
        a2 = a1
        b2 = b1
        red_mask = np.zeros_like(L, dtype=bool)
        blue_mask = np.zeros_like(L, dtype=bool)
        green_mask = np.zeros_like(L, dtype=bool)
        hue_diag = {"enabled": False}

    C_after_hue = np.sqrt(a2 * a2 + b2 * b2).astype(np.float32)
    if bool(params["neutral_protect_enabled"]):
        a3, b3, neutral_mask, neutral_diag = _neutral_protection(a2, b2, C_after_hue, params)
    else:
        a3 = a2
        b3 = b2
        neutral_mask = np.zeros_like(L, dtype=bool)
        neutral_diag = {"enabled": False}

    C_after = np.sqrt(a3 * a3 + b3 * b3).astype(np.float32)
    lab_out = np.stack([L * 100.0, a3, b3], axis=2).astype(np.float32)
    rgb_srgb_like = _lab_to_srgb_like(lab_out, gamma=float(params["lab_input_gamma"]))
    stage3_linear_output = np.power(
        np.clip(rgb_srgb_like, 0.0, 1.0),
        max(float(params["lab_input_gamma"]), 1e-6),
    ).astype(np.float32)
    stage3_preview_output = (
        make_stage3_preview_output(
            stage3_linear_output,
            preview_percentile=float(params["preview_percentile"]),
            preview_gamma=float(params["preview_gamma"]),
            preview_eps=float(params["preview_eps"]),
        )
        if bool(params["preview_enabled"])
        else np.clip(stage3_linear_output, 0.0, 1.0).astype(np.float32)
    )

    debug = (
        {
            "img_lab_in": img_lab_in,
            "L": L,
            "a_before": a_before,
            "b_before": b_before,
            "sat_scale": sat_scale,
            "hue_map": hue_map,
            "red_mask": red_mask,
            "blue_mask": blue_mask,
            "green_mask": green_mask,
            "C_before": C_before,
            "C_after": C_after,
            "neutral_mask": neutral_mask,
            "stage3_linear_output": stage3_linear_output,
            "stage3_preview_output": stage3_preview_output,
        }
        if include_debug
        else {}
    )
    diagnostics = {
        "stage": "stage3_pseudo_lut_color_refinement",
        "note": "Pseudo-LUT Lab chroma refinement; original L is preserved and no DNG ColorMatrix is used.",
        "params": _jsonable_params(params),
        "steps": {
            "3a_perceptual_space_conversion": lab_input_diag,
            "3b_luminance_driven_chroma": chroma_diag,
            "3c_hue_dependent_adjustment": hue_diag,
            "3d_neutral_protection": neutral_diag,
            "3e_lab_to_linear_rgb": {
                "enabled": True,
                "L_preserved": True,
                "space": "Lab -> sRGB-like -> linear",
            },
            "3f_preview_output": _preview_diagnostics(stage3_preview_output, params),
        },
        "C_before_mean": float(C_before.mean()),
        "C_after_mean": float(C_after.mean()),
        "C_before_p95": float(np.percentile(C_before, 95.0)),
        "C_after_p95": float(np.percentile(C_after, 95.0)),
        "linear_output_mean_rgb": [float(v) for v in stage3_linear_output.mean(axis=(0, 1))],
        "linear_output_p95_rgb": [
            float(v) for v in np.percentile(stage3_linear_output.reshape(-1, 3), 95.0, axis=0)
        ],
        "linear_output_min_rgb": [float(v) for v in stage3_linear_output.reshape(-1, 3).min(axis=0)],
        "linear_output_max_rgb": [float(v) for v in stage3_linear_output.reshape(-1, 3).max(axis=0)],
    }
    return Stage3PseudoLutColorRefinementResult(
        stage3_linear_output=stage3_linear_output,
        stage3_preview_output=stage3_preview_output,
        debug=debug,
        diagnostics=diagnostics,
    )


def _prepare_lab_input(
    img: np.ndarray,
    percentile: float,
    gamma: float,
    eps: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    x = np.maximum(img.astype(np.float32), 0.0)
    scale = _safe_percentile(x, percentile)
    x = x / (scale + max(float(eps), 1e-12))
    x = np.clip(x, 0.0, 1.0)
    gamma = max(float(gamma), 1e-6)
    x = np.power(x, 1.0 / gamma).astype(np.float32)
    return x, {
        "enabled": True,
        "mode": "preview_style_encoded",
        "percentile": float(percentile),
        "scale": float(scale),
        "gamma": gamma,
        "input_mean_rgb": [float(v) for v in x.mean(axis=(0, 1))],
        "input_p95_rgb": [float(v) for v in np.percentile(x.reshape(-1, 3), 95.0, axis=0)],
    }


def _luminance_driven_chroma(
    L: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    sigma = max(float(params["sat_mid_sigma"]), 1e-6)
    sat_scale = (
        float(params["sat_base"])
        + float(params["sat_mid_gain"])
        * np.exp(-((L - float(params["sat_mid_center"])) ** 2) / (2.0 * sigma * sigma))
        - float(params["sat_highlight_decay"]) * L
    )
    sat_scale = np.clip(
        sat_scale,
        float(params["sat_scale_min"]),
        float(params["sat_scale_max"]),
    ).astype(np.float32)
    return sat_scale, {
        "enabled": True,
        "sat_base": float(params["sat_base"]),
        "sat_mid_gain": float(params["sat_mid_gain"]),
        "sat_mid_center": float(params["sat_mid_center"]),
        "sat_mid_sigma": sigma,
        "sat_highlight_decay": float(params["sat_highlight_decay"]),
        "sat_scale_min": float(params["sat_scale_min"]),
        "sat_scale_max": float(params["sat_scale_max"]),
        "sat_scale_mean": float(sat_scale.mean()),
        "sat_scale_min_observed": float(sat_scale.min()),
        "sat_scale_max_observed": float(sat_scale.max()),
    }


def _hue_dependent_adjustment(
    a: np.ndarray,
    b: np.ndarray,
    hue_map: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    red_mask = (hue_map >= float(params["red_hue_min"])) & (hue_map <= float(params["red_hue_max"]))
    blue_threshold = abs(float(params["blue_abs_hue_threshold"]))
    blue_mask = (hue_map > blue_threshold) | (hue_map < -blue_threshold)
    green_mask = (hue_map >= float(params["green_hue_min"])) & (
        hue_map <= float(params["green_hue_max"])
    )

    a_out = a.copy()
    b_out = b.copy()
    a_out[red_mask] *= float(params["red_a_scale"])
    b_out[red_mask] *= float(params["red_b_scale"])
    a_out[blue_mask] *= float(params["blue_a_scale"])
    b_out[blue_mask] *= float(params["blue_b_scale"])
    a_out[green_mask] *= float(params["green_a_scale"])
    b_out[green_mask] *= float(params["green_b_scale"])

    return a_out.astype(np.float32), b_out.astype(np.float32), red_mask, blue_mask, green_mask, {
        "enabled": True,
        "red": {
            "hue_min": float(params["red_hue_min"]),
            "hue_max": float(params["red_hue_max"]),
            "a_scale": float(params["red_a_scale"]),
            "b_scale": float(params["red_b_scale"]),
            "mask_fraction": float(red_mask.mean()),
        },
        "blue": {
            "abs_hue_threshold": blue_threshold,
            "a_scale": float(params["blue_a_scale"]),
            "b_scale": float(params["blue_b_scale"]),
            "mask_fraction": float(blue_mask.mean()),
        },
        "green": {
            "hue_min": float(params["green_hue_min"]),
            "hue_max": float(params["green_hue_max"]),
            "a_scale": float(params["green_a_scale"]),
            "b_scale": float(params["green_b_scale"]),
            "mask_fraction": float(green_mask.mean()),
        },
    }


def _neutral_protection(
    a: np.ndarray,
    b: np.ndarray,
    C: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    threshold = float(np.percentile(C, float(np.clip(params["neutral_percentile"], 0.0, 100.0))))
    neutral_mask = C < threshold
    a_out = a.copy()
    b_out = b.copy()
    if bool(params["neutral_soft_enabled"]):
        sigma = max(float(params["neutral_sigma"]), 1e-6)
        strength = float(np.clip(params["neutral_strength"], 0.0, 1.0))
        w = np.exp(-((C * C) / (2.0 * sigma * sigma))).astype(np.float32)
        shrink = np.clip(1.0 - strength * w, 0.0, 1.0)
        a_out[neutral_mask] *= shrink[neutral_mask]
        b_out[neutral_mask] *= shrink[neutral_mask]
        mode = "soft_weighted_mask"
    else:
        shrink_value = float(np.clip(params["neutral_shrink"], 0.0, 1.0))
        a_out[neutral_mask] *= shrink_value
        b_out[neutral_mask] *= shrink_value
        sigma = None
        strength = None
        mode = "hard_mask"

    return a_out.astype(np.float32), b_out.astype(np.float32), neutral_mask, {
        "enabled": True,
        "mode": mode,
        "percentile": float(params["neutral_percentile"]),
        "threshold": threshold,
        "neutral_shrink": float(params["neutral_shrink"]),
        "neutral_soft_enabled": bool(params["neutral_soft_enabled"]),
        "neutral_sigma": sigma,
        "neutral_strength": strength,
        "mask_fraction": float(neutral_mask.mean()),
    }


def _srgb_like_to_lab(rgb: np.ndarray, gamma: float) -> np.ndarray:
    linear = np.power(np.clip(rgb, 0.0, 1.0), max(float(gamma), 1e-6))
    return _linear_rgb_to_lab(linear)


def _lab_to_srgb_like(lab: np.ndarray, gamma: float) -> np.ndarray:
    linear = np.maximum(_lab_to_linear_rgb(lab), 0.0)
    return np.power(linear, 1.0 / max(float(gamma), 1e-6)).astype(np.float32)


def _linear_rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    xyz = np.tensordot(np.maximum(rgb, 0.0), _RGB_TO_XYZ.T, axes=1).astype(np.float32)
    xyz_scaled = xyz / _D65[np.newaxis, np.newaxis, :]
    f = _lab_f(xyz_scaled)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=2).astype(np.float32)


def _lab_to_linear_rgb(lab: np.ndarray) -> np.ndarray:
    L = lab[..., 0]
    a = lab[..., 1]
    b = lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    xyz_scaled = np.stack([_lab_f_inv(fx), _lab_f_inv(fy), _lab_f_inv(fz)], axis=2)
    xyz = xyz_scaled * _D65[np.newaxis, np.newaxis, :]
    return np.tensordot(xyz, _XYZ_TO_RGB.T, axes=1).astype(np.float32)


def _lab_f(t: np.ndarray) -> np.ndarray:
    delta = 6.0 / 29.0
    return np.where(t > delta**3, np.cbrt(t), t / (3.0 * delta**2) + 4.0 / 29.0)


def _lab_f_inv(t: np.ndarray) -> np.ndarray:
    delta = 6.0 / 29.0
    return np.where(t > delta, t**3, 3.0 * delta**2 * (t - 4.0 / 29.0))


def _validate_rgb(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Expected RGB image with shape (H, W, 3), got {arr.shape}.")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return np.maximum(arr, 0.0).astype(np.float32)


def _safe_percentile(arr: np.ndarray, percentile: float) -> float:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 1.0
    value = float(np.percentile(finite, float(np.clip(percentile, 0.0, 100.0))))
    return max(value, 1e-12)


def _preview_diagnostics(preview: np.ndarray, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(params["preview_enabled"]),
        "percentile": float(params["preview_percentile"]),
        "gamma": float(params["preview_gamma"]),
        "preview_mean_rgb": [float(v) for v in preview.mean(axis=(0, 1))],
        "preview_p95_rgb": [float(v) for v in np.percentile(preview.reshape(-1, 3), 95.0, axis=0)],
    }


def _jsonable_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (float(value) if isinstance(value, np.floating) else value)
        for key, value in params.items()
    }
