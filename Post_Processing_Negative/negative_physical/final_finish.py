"""Stage 4 final finish and export mapping for the negative conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

_EPS = 1e-6
_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
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

STAGE4_DEFAULT_PARAMS: dict[str, Any] = {
    "display_mapping_enabled": True,
    "display_mapping_mode": "simple_gamma",
    "display_percentile": 99.5,
    "display_gamma": 2.2,
    "display_eps": 1e-6,
    "display_fixed_scale": None,
    "final_trim_enabled": True,
    "final_exposure_ev": 0.0,
    "final_black_point": 0.0,
    "final_white_point": 1.0,
    "final_contrast": 1.02,
    "final_temp_shift": 0.0,
    "final_tint_shift": 0.0,
    "highlight_desat_enabled": False,
    "highlight_desat_threshold": 0.80,
    "highlight_desat_strength": 0.15,
    "shadow_neutralize_enabled": False,
    "shadow_neutralize_threshold": 0.20,
    "shadow_neutralize_strength": 0.10,
    "grain_enabled": False,
    "grain_strength": 0.02,
    "grain_size": 1.0,
    "bloom_enabled": False,
    "bloom_threshold": 0.85,
    "bloom_strength": 0.05,
    "bloom_sigma": 3.0,
    "final_softness_enabled": False,
    "final_softness_sigma": 0.6,
    "final_sharpen_enabled": False,
    "final_sharpen_amount": 0.10,
    "master_output_mode": "display_mapped_float",
}


@dataclass(frozen=True)
class Stage4FinalFinishResult:
    """Stage 4 final preview, master output, debug arrays, and diagnostics."""

    final_preview_output: np.ndarray
    final_master_output: np.ndarray
    debug: dict[str, Any]
    diagnostics: dict[str, Any]


def stage4_final_finish_and_export(
    img: np.ndarray,
    display_mapping_enabled: bool = True,
    display_mapping_mode: str = "simple_gamma",
    display_percentile: float = 99.5,
    display_gamma: float = 2.2,
    display_eps: float = 1e-6,
    final_trim_enabled: bool = True,
    final_exposure_ev: float = 0.0,
    final_black_point: float = 0.0,
    final_white_point: float = 1.0,
    final_contrast: float = 1.02,
    final_temp_shift: float = 0.0,
    final_tint_shift: float = 0.0,
    highlight_desat_enabled: bool = False,
    highlight_desat_threshold: float = 0.80,
    highlight_desat_strength: float = 0.15,
    shadow_neutralize_enabled: bool = False,
    shadow_neutralize_threshold: float = 0.20,
    shadow_neutralize_strength: float = 0.10,
    grain_enabled: bool = False,
    grain_strength: float = 0.02,
    grain_size: float = 1.0,
    bloom_enabled: bool = False,
    bloom_threshold: float = 0.85,
    bloom_strength: float = 0.05,
    bloom_sigma: float = 3.0,
    final_softness_enabled: bool = False,
    final_softness_sigma: float = 0.6,
    final_sharpen_enabled: bool = False,
    final_sharpen_amount: float = 0.10,
    master_output_mode: str = "display_mapped_float",
    debug: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict[str, Any]]:
    """Create the final viewable output and a high-quality master float output.

    [4a] Final display mapping converts linear working RGB into a display image.
    [4b] Global trim applies small final exposure, point, contrast, temp, and tint changes.
    [4c] Optional local refinement can gently desaturate highlights or neutralize shadows.
    [4d] Optional film finish provides light grain, bloom, softness, and sharpening hooks.
    [4e] Dual output separates final preview from master output for export.

    # Acceptance standards:
    # 1. The image should be directly viewable and exportable.
    # 2. It should no longer look like a linear working image.
    # 3. Brightness, contrast, and color should feel natural and stable.
    # 4. Highlights should not look dirty or clipped harshly.
    # 5. Midtones should keep depth.
    # 6. Neutral regions should stay clean.
    # 7. If film finish is enabled, it should look like scan texture, not a digital filter.
    # 8. Default settings should be robust without strong stylization.
    """

    params = dict(STAGE4_DEFAULT_PARAMS)
    params.update(
        {
            "display_mapping_enabled": display_mapping_enabled,
            "display_mapping_mode": display_mapping_mode,
            "display_percentile": display_percentile,
            "display_gamma": display_gamma,
            "display_eps": display_eps,
            "final_trim_enabled": final_trim_enabled,
            "final_exposure_ev": final_exposure_ev,
            "final_black_point": final_black_point,
            "final_white_point": final_white_point,
            "final_contrast": final_contrast,
            "final_temp_shift": final_temp_shift,
            "final_tint_shift": final_tint_shift,
            "highlight_desat_enabled": highlight_desat_enabled,
            "highlight_desat_threshold": highlight_desat_threshold,
            "highlight_desat_strength": highlight_desat_strength,
            "shadow_neutralize_enabled": shadow_neutralize_enabled,
            "shadow_neutralize_threshold": shadow_neutralize_threshold,
            "shadow_neutralize_strength": shadow_neutralize_strength,
            "grain_enabled": grain_enabled,
            "grain_strength": grain_strength,
            "grain_size": grain_size,
            "bloom_enabled": bloom_enabled,
            "bloom_threshold": bloom_threshold,
            "bloom_strength": bloom_strength,
            "bloom_sigma": bloom_sigma,
            "final_softness_enabled": final_softness_enabled,
            "final_softness_sigma": final_softness_sigma,
            "final_sharpen_enabled": final_sharpen_enabled,
            "final_sharpen_amount": final_sharpen_amount,
            "master_output_mode": master_output_mode,
        }
    )
    result = apply_stage4_final_finish_and_export(img, include_debug=debug, **params)
    if debug:
        return result.final_preview_output, result.debug
    return result.final_preview_output


def apply_stage4_final_finish_and_export(
    img: np.ndarray,
    include_debug: bool = True,
    **params: Any,
) -> Stage4FinalFinishResult:
    """Run Stage 4 with diagnostics and optional debug arrays."""

    merged = dict(STAGE4_DEFAULT_PARAMS)
    merged.update(params)
    return _stage4_final_finish_and_export_impl(img, merged, include_debug=include_debug)


def final_display_mapping(
    img: np.ndarray,
    display_mapping_enabled: bool = True,
    display_mapping_mode: str = "simple_gamma",
    display_percentile: float = 99.5,
    display_gamma: float = 2.2,
    display_eps: float = 1e-6,
    display_fixed_scale: float | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Map linear working RGB to display-referred RGB in [0, 1]."""

    x = _validate_rgb(img)
    if not display_mapping_enabled:
        return np.clip(x, 0.0, 1.0).astype(np.float32), {"enabled": False}

    scale = float(display_fixed_scale) if display_fixed_scale is not None else _safe_percentile(x, display_percentile)
    x = x / (scale + max(float(display_eps), 1e-12))
    x = np.clip(x, 0.0, 1.0)
    if display_mapping_mode == "simple_gamma":
        gamma = max(float(display_gamma), 1e-6)
        out = np.power(x, 1.0 / gamma).astype(np.float32)
    elif display_mapping_mode == "srgb_oetf":
        out = _srgb_oetf(x).astype(np.float32)
    else:
        raise ValueError(f"Unsupported display_mapping_mode: {display_mapping_mode}")

    return out, {
        "enabled": True,
        "mode": display_mapping_mode,
        "display_percentile": float(display_percentile),
        "scale": float(scale),
        "scale_source": "global_fixed" if display_fixed_scale is not None else "per_frame_percentile",
        "display_gamma": float(display_gamma),
        "output_mean_rgb": [float(v) for v in out.mean(axis=(0, 1))],
    }


def apply_final_grain(
    img: np.ndarray,
    grain_strength: float = 0.02,
    grain_size: float = 1.0,
) -> np.ndarray:
    """Add deterministic luminance-weighted monochrome grain."""

    strength = max(float(grain_strength), 0.0)
    if strength <= 0.0:
        return img.astype(np.float32)
    rng = np.random.default_rng(445)
    noise = rng.normal(0.0, 1.0, img.shape[:2]).astype(np.float32)
    if grain_size > 1.0:
        noise = cv2.GaussianBlur(noise, (0, 0), sigmaX=float(grain_size), sigmaY=float(grain_size))
    noise = noise / (float(np.std(noise)) + 1e-6)
    luma = np.clip(np.tensordot(img, _LUMA, axes=1), 0.0, 1.0)
    weight = (0.35 + 0.65 * (1.0 - luma))[..., np.newaxis]
    out = img + strength * weight * noise[..., np.newaxis]
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def apply_final_bloom(
    img: np.ndarray,
    bloom_threshold: float = 0.85,
    bloom_strength: float = 0.05,
    bloom_sigma: float = 3.0,
) -> np.ndarray:
    """Add a simple high-light softness bloom layer."""

    strength = max(float(bloom_strength), 0.0)
    if strength <= 0.0:
        return img.astype(np.float32)
    threshold = float(np.clip(bloom_threshold, 0.0, 1.0))
    highlight = np.clip((img - threshold) / max(1.0 - threshold, 1e-6), 0.0, 1.0)
    blur = cv2.GaussianBlur(
        highlight.astype(np.float32),
        (0, 0),
        sigmaX=max(float(bloom_sigma), 0.01),
        sigmaY=max(float(bloom_sigma), 0.01),
    )
    return np.clip(img + strength * blur, 0.0, 1.0).astype(np.float32)


def apply_final_softness_or_sharpen(
    img: np.ndarray,
    final_softness_enabled: bool = False,
    final_softness_sigma: float = 0.6,
    final_sharpen_enabled: bool = False,
    final_sharpen_amount: float = 0.10,
) -> np.ndarray:
    """Apply optional final softness and/or mild unsharp masking."""

    out = img.astype(np.float32)
    if final_softness_enabled:
        out = cv2.GaussianBlur(
            out,
            (0, 0),
            sigmaX=max(float(final_softness_sigma), 0.01),
            sigmaY=max(float(final_softness_sigma), 0.01),
        )
    if final_sharpen_enabled:
        blur = cv2.GaussianBlur(out, (0, 0), sigmaX=0.8, sigmaY=0.8)
        out = out + max(float(final_sharpen_amount), 0.0) * (out - blur)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _stage4_final_finish_and_export_impl(
    img: np.ndarray,
    params: dict[str, Any],
    include_debug: bool,
) -> Stage4FinalFinishResult:
    img_input_linear = _validate_rgb(img)
    img_display_base, display_diag = final_display_mapping(
        img_input_linear,
        display_mapping_enabled=bool(params["display_mapping_enabled"]),
        display_mapping_mode=str(params["display_mapping_mode"]),
        display_percentile=float(params["display_percentile"]),
        display_gamma=float(params["display_gamma"]),
        display_eps=float(params["display_eps"]),
        display_fixed_scale=params.get("display_fixed_scale"),
    )

    if bool(params["final_trim_enabled"]):
        img_after_trim, trim_diag = _apply_final_global_trim(img_display_base, params)
    else:
        img_after_trim = img_display_base
        trim_diag = {"enabled": False}

    img_after_local_refinement, local_diag = _apply_optional_local_refinement(img_after_trim, params)
    img_after_film_finish, film_diag = _apply_optional_film_finish(img_after_local_refinement, params)
    final_preview_output = np.clip(img_after_film_finish, 0.0, 1.0).astype(np.float32)
    final_master_output, master_diag = _make_master_output(
        img_input_linear,
        final_preview_output,
        master_output_mode=str(params["master_output_mode"]),
    )

    debug = (
        {
            "img_input_linear": img_input_linear,
            "img_display_base": img_display_base,
            "img_after_trim": img_after_trim,
            "img_after_local_refinement": img_after_local_refinement,
            "img_after_film_finish": img_after_film_finish,
            "final_preview_output": final_preview_output,
            "final_master_output": final_master_output,
        }
        if include_debug
        else {}
    )
    diagnostics = {
        "stage": "stage4_final_finish_and_export",
        "note": "Final display mapping and light finishing layer; no physical recovery or large tone rebuild.",
        "params": _jsonable_params(params),
        "steps": {
            "4a_final_display_mapping": display_diag,
            "4b_final_global_trim": trim_diag,
            "4c_optional_local_refinement": local_diag,
            "4d_optional_film_finish": film_diag,
            "4e_dual_output": master_diag,
        },
        "final_preview_mean_rgb": [float(v) for v in final_preview_output.mean(axis=(0, 1))],
        "final_preview_p95_rgb": [
            float(v) for v in np.percentile(final_preview_output.reshape(-1, 3), 95.0, axis=0)
        ],
        "final_preview_min_rgb": [float(v) for v in final_preview_output.reshape(-1, 3).min(axis=0)],
        "final_preview_max_rgb": [float(v) for v in final_preview_output.reshape(-1, 3).max(axis=0)],
    }
    return Stage4FinalFinishResult(
        final_preview_output=final_preview_output,
        final_master_output=final_master_output,
        debug=debug,
        diagnostics=diagnostics,
    )


def _apply_final_global_trim(
    img: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    out = img.astype(np.float32)
    out = out * (2.0 ** float(params["final_exposure_ev"]))
    black = float(np.clip(params["final_black_point"], 0.0, 0.99))
    out = (out - black) / max(1.0 - black, 1e-6)
    out = out / max(float(params["final_white_point"]), 1e-6)
    out = (out - 0.5) * float(params["final_contrast"]) + 0.5
    temp_shift = float(params["final_temp_shift"])
    tint_shift = float(params["final_tint_shift"])
    gains = np.array(
        [1.0 + temp_shift, 1.0 + tint_shift, 1.0 - temp_shift],
        dtype=np.float32,
    )
    out = out * gains[np.newaxis, np.newaxis, :]
    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    return out, {
        "enabled": True,
        "final_exposure_ev": float(params["final_exposure_ev"]),
        "final_black_point": black,
        "final_white_point": float(params["final_white_point"]),
        "final_contrast": float(params["final_contrast"]),
        "final_temp_shift": temp_shift,
        "final_tint_shift": tint_shift,
        "channel_gains": [float(v) for v in gains],
    }


def _apply_optional_local_refinement(
    img: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    out = img.astype(np.float32)
    steps: dict[str, Any] = {}
    if bool(params["highlight_desat_enabled"]):
        out, diag = _highlight_desaturate_lab(
            out,
            threshold=float(params["highlight_desat_threshold"]),
            strength=float(params["highlight_desat_strength"]),
        )
        steps["highlight_desat"] = diag
    else:
        steps["highlight_desat"] = {"enabled": False}

    if bool(params["shadow_neutralize_enabled"]):
        out, diag = _shadow_neutralize_lab(
            out,
            threshold=float(params["shadow_neutralize_threshold"]),
            strength=float(params["shadow_neutralize_strength"]),
        )
        steps["shadow_neutralize"] = diag
    else:
        steps["shadow_neutralize"] = {"enabled": False}
    return np.clip(out, 0.0, 1.0).astype(np.float32), {"enabled": any(v["enabled"] for v in steps.values()), "steps": steps}


def _highlight_desaturate_lab(
    img: np.ndarray,
    threshold: float,
    strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    lab = _display_rgb_to_lab(img)
    L = np.clip(lab[..., 0] / 100.0, 0.0, 1.0)
    w = _smoothstep(threshold - 0.05, threshold + 0.05, L)
    factor = 1.0 - float(np.clip(strength, 0.0, 1.0)) * w
    lab[..., 1] *= factor
    lab[..., 2] *= factor
    return _lab_to_display_rgb(lab), {
        "enabled": True,
        "threshold": float(threshold),
        "strength": float(strength),
        "mean_weight": float(w.mean()),
    }


def _shadow_neutralize_lab(
    img: np.ndarray,
    threshold: float,
    strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    lab = _display_rgb_to_lab(img)
    L = np.clip(lab[..., 0] / 100.0, 0.0, 1.0)
    w = 1.0 - _smoothstep(threshold - 0.05, threshold + 0.05, L)
    factor = 1.0 - float(np.clip(strength, 0.0, 1.0)) * w
    lab[..., 1] *= factor
    lab[..., 2] *= factor
    return _lab_to_display_rgb(lab), {
        "enabled": True,
        "threshold": float(threshold),
        "strength": float(strength),
        "mean_weight": float(w.mean()),
    }


def _apply_optional_film_finish(
    img: np.ndarray,
    params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    out = img.astype(np.float32)
    steps: dict[str, Any] = {}
    if bool(params["bloom_enabled"]):
        out = apply_final_bloom(
            out,
            bloom_threshold=float(params["bloom_threshold"]),
            bloom_strength=float(params["bloom_strength"]),
            bloom_sigma=float(params["bloom_sigma"]),
        )
        steps["bloom"] = {
            "enabled": True,
            "threshold": float(params["bloom_threshold"]),
            "strength": float(params["bloom_strength"]),
            "sigma": float(params["bloom_sigma"]),
        }
    else:
        steps["bloom"] = {"enabled": False}

    if bool(params["final_softness_enabled"]) or bool(params["final_sharpen_enabled"]):
        out = apply_final_softness_or_sharpen(
            out,
            final_softness_enabled=bool(params["final_softness_enabled"]),
            final_softness_sigma=float(params["final_softness_sigma"]),
            final_sharpen_enabled=bool(params["final_sharpen_enabled"]),
            final_sharpen_amount=float(params["final_sharpen_amount"]),
        )
        steps["softness_sharpen"] = {
            "enabled": True,
            "softness_enabled": bool(params["final_softness_enabled"]),
            "softness_sigma": float(params["final_softness_sigma"]),
            "sharpen_enabled": bool(params["final_sharpen_enabled"]),
            "sharpen_amount": float(params["final_sharpen_amount"]),
        }
    else:
        steps["softness_sharpen"] = {"enabled": False}

    if bool(params["grain_enabled"]):
        out = apply_final_grain(
            out,
            grain_strength=float(params["grain_strength"]),
            grain_size=float(params["grain_size"]),
        )
        steps["grain"] = {
            "enabled": True,
            "strength": float(params["grain_strength"]),
            "size": float(params["grain_size"]),
        }
    else:
        steps["grain"] = {"enabled": False}
    return np.clip(out, 0.0, 1.0).astype(np.float32), {"enabled": any(v["enabled"] for v in steps.values()), "steps": steps}


def _make_master_output(
    img_input_linear: np.ndarray,
    final_preview_output: np.ndarray,
    master_output_mode: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    if master_output_mode == "display_mapped_float":
        master = final_preview_output.astype(np.float32)
    elif master_output_mode == "linear_float":
        master = np.maximum(img_input_linear, 0.0).astype(np.float32)
    else:
        raise ValueError(f"Unsupported master_output_mode: {master_output_mode}")
    return master, {
        "final_preview_output": True,
        "final_master_output": True,
        "master_output_mode": master_output_mode,
        "master_min_rgb": [float(v) for v in master.reshape(-1, 3).min(axis=0)],
        "master_max_rgb": [float(v) for v in master.reshape(-1, 3).max(axis=0)],
    }


def _display_rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    linear = _srgb_eotf(np.clip(rgb, 0.0, 1.0))
    return _linear_rgb_to_lab(linear)


def _lab_to_display_rgb(lab: np.ndarray) -> np.ndarray:
    linear = np.maximum(_lab_to_linear_rgb(lab), 0.0)
    return np.clip(_srgb_oetf(linear), 0.0, 1.0).astype(np.float32)


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


def _srgb_oetf(linear: np.ndarray) -> np.ndarray:
    x = np.clip(linear, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _srgb_eotf(encoded: np.ndarray) -> np.ndarray:
    x = np.clip(encoded, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / max(edge1 - edge0, _EPS), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


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


def _jsonable_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (float(value) if isinstance(value, np.floating) else value)
        for key, value in params.items()
    }
