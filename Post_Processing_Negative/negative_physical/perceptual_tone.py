"""Stage 2 perceptual tone base for global brightness and highlight behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

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

STAGE2_DEFAULT_PARAMS: dict[str, Any] = {
    "gray_norm_enabled": True,
    "gray_norm_percentile": 30.0,
    "gray_norm_method": "mean_luma",
    "gray_norm_strength": 0.20,
    "gray_norm_eps": 1e-6,
    "perceptual_space_enabled": True,
    "lab_input_mode": "srgb_encoded",
    "lab_input_percentile": 99.5,
    "lab_input_gamma": 2.2,
    "lab_input_eps": 1e-6,
    "lab_input_fixed_scale": None,
    "zoned_tone_enabled": True,
    "shadow_threshold": 0.28,
    "highlight_threshold": 0.72,
    "shadow_gamma": 0.90,
    "mid_sigmoid_k": 3.5,
    "mid_sigmoid_x0": 0.50,
    "highlight_local_strength": 1.0,
    "highlight_rolloff_enabled": True,
    "highlight_rolloff_alpha": 0.20,
    "highlight_rolloff_power": 1.5,
    "preview_enabled": True,
    "preview_percentile": 99.5,
    "preview_gamma": 2.2,
    "preview_eps": 1e-6,
    "preview_fixed_scale": None,
}


@dataclass(frozen=True)
class Stage2PerceptualToneResult:
    """Stage 2 linear output, display preview, debug arrays, and diagnostics."""

    stage2_linear_output: np.ndarray
    stage2_preview_output: np.ndarray
    debug: dict[str, Any]
    diagnostics: dict[str, Any]

    @property
    def rgb_after_stage2(self) -> np.ndarray:
        """Compatibility alias for older callers; this is the linear pipeline output."""

        return self.stage2_linear_output


def stage2_perceptual_tone_base(
    img: np.ndarray,
    gray_norm_enabled: bool = True,
    gray_norm_percentile: float = 30.0,
    gray_norm_method: Literal["mean_luma", "rgb_mean"] = "mean_luma",
    gray_norm_strength: float = 0.20,
    gray_norm_eps: float = 1e-6,
    perceptual_space_enabled: bool = True,
    lab_input_mode: Literal["srgb_encoded", "linear_direct"] = "srgb_encoded",
    lab_input_percentile: float = 99.5,
    lab_input_gamma: float = 2.2,
    lab_input_eps: float = 1e-6,
    zoned_tone_enabled: bool = True,
    shadow_threshold: float = 0.28,
    highlight_threshold: float = 0.72,
    shadow_gamma: float = 0.90,
    mid_sigmoid_k: float = 3.5,
    mid_sigmoid_x0: float = 0.50,
    highlight_local_strength: float = 1.0,
    highlight_rolloff_enabled: bool = True,
    highlight_rolloff_alpha: float = 0.20,
    highlight_rolloff_power: float = 1.5,
    preview_enabled: bool = True,
    preview_percentile: float = 99.5,
    preview_gamma: float = 2.2,
    preview_eps: float = 1e-6,
    debug: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict[str, Any]]:
    """Build a stable luminance base while keeping preview and linear data separate.

    [2a] Lab input preparation no longer sends raw linear RGB directly into Lab by
    default. The default path first normalizes by a high percentile, then gamma
    encodes into an sRGB-like perceptual image for Lab conversion.
    [2b] Gray anchor normalization is weak by default, so it stabilizes exposure
    without crushing or exploding the working image.
    [2c] Zoned tone mapping edits Lab L only, with conservative shadow, midtone,
    and highlight behavior.
    [2d] Global highlight roll-off is deliberately soft and only touches Lab L.
    [2e] The function returns linear data for the pipeline, while debug previews
    use separate percentile normalization and gamma.

    # Acceptance standards:
    # 1. The Stage 2 preview should not be a nearly black blue image.
    # 2. Subject structure should be clearly visible.
    # 3. Highlights should be softer than stage [10].
    # 4. Midtones should have more depth.
    # 5. Shadows may stay dark, but should not be dead black.
    # 6. Color can still be imperfect, but should not collapse into blue-black.
    """

    result = apply_stage2_perceptual_tone_base(
        img,
        gray_norm_enabled=gray_norm_enabled,
        gray_norm_percentile=gray_norm_percentile,
        gray_norm_method=gray_norm_method,
        gray_norm_strength=gray_norm_strength,
        gray_norm_eps=gray_norm_eps,
        perceptual_space_enabled=perceptual_space_enabled,
        lab_input_mode=lab_input_mode,
        lab_input_percentile=lab_input_percentile,
        lab_input_gamma=lab_input_gamma,
        lab_input_eps=lab_input_eps,
        zoned_tone_enabled=zoned_tone_enabled,
        shadow_threshold=shadow_threshold,
        highlight_threshold=highlight_threshold,
        shadow_gamma=shadow_gamma,
        mid_sigmoid_k=mid_sigmoid_k,
        mid_sigmoid_x0=mid_sigmoid_x0,
        highlight_local_strength=highlight_local_strength,
        highlight_rolloff_enabled=highlight_rolloff_enabled,
        highlight_rolloff_alpha=highlight_rolloff_alpha,
        highlight_rolloff_power=highlight_rolloff_power,
        preview_enabled=preview_enabled,
        preview_percentile=preview_percentile,
        preview_gamma=preview_gamma,
        preview_eps=preview_eps,
    )
    if debug:
        return result.stage2_linear_output, result.debug
    return result.stage2_linear_output


def apply_stage2_perceptual_tone_base(
    img: np.ndarray,
    **params: Any,
) -> Stage2PerceptualToneResult:
    """Run Stage 2 with diagnostics and debug arrays."""

    merged = dict(STAGE2_DEFAULT_PARAMS)
    merged.update(params)
    return _stage2_perceptual_tone_base_impl(img, merged)


def make_stage2_preview_output(
    stage2_linear_output: np.ndarray,
    preview_percentile: float = 99.5,
    preview_gamma: float = 2.2,
    preview_eps: float = 1e-6,
    fixed_scale: float | None = None,
) -> np.ndarray:
    """Create the display/debug preview from Stage 2 linear output."""

    x = np.maximum(np.asarray(stage2_linear_output, dtype=np.float32), 0.0)
    scale = float(fixed_scale) if fixed_scale is not None else _safe_percentile(x, preview_percentile)
    x = x / (scale + max(float(preview_eps), 1e-12))
    x = np.clip(x, 0.0, 1.0)
    gamma = max(float(preview_gamma), 1e-6)
    return np.power(x, 1.0 / gamma).astype(np.float32)


def _stage2_perceptual_tone_base_impl(
    img: np.ndarray,
    params: dict[str, Any],
) -> Stage2PerceptualToneResult:
    img_linear = _validate_rgb(img)

    if params["gray_norm_enabled"]:
        img_before_lab, gray_diag = _gray_anchor_normalization(
            img_linear,
            percentile=float(params["gray_norm_percentile"]),
            method=str(params["gray_norm_method"]),
            strength=float(params["gray_norm_strength"]),
            eps=float(params["gray_norm_eps"]),
        )
    else:
        img_before_lab = img_linear
        gray_diag = {
            "enabled": False,
            "gray_rgb": None,
            "gray_luma": None,
        }

    if not bool(params["perceptual_space_enabled"]):
        stage2_linear_output = np.maximum(img_before_lab, 0.0).astype(np.float32)
        stage2_preview_output = (
            make_stage2_preview_output(
                stage2_linear_output,
                preview_percentile=float(params["preview_percentile"]),
                preview_gamma=float(params["preview_gamma"]),
                preview_eps=float(params["preview_eps"]),
                fixed_scale=params.get("preview_fixed_scale"),
            )
            if bool(params["preview_enabled"])
            else np.clip(stage2_linear_output, 0.0, 1.0).astype(np.float32)
        )
        debug = {
            "gray_rgb": gray_diag.get("gray_rgb"),
            "gray_luma": gray_diag.get("gray_luma"),
            "img_before_lab": img_before_lab,
            "img_lab_in": None,
            "L_before": None,
            "L_after_zoned": None,
            "L_after_rolloff": None,
            "stage2_linear_output": stage2_linear_output,
            "stage2_preview_output": stage2_preview_output,
        }
        diagnostics = _diagnostics_without_lab(params, gray_diag, stage2_linear_output)
        diagnostics["preview"] = _preview_diagnostics(stage2_preview_output, params)
        return Stage2PerceptualToneResult(
            stage2_linear_output=stage2_linear_output,
            stage2_preview_output=stage2_preview_output,
            debug=debug,
            diagnostics=diagnostics,
        )

    lab_input_mode = str(params["lab_input_mode"])
    img_lab_in, lab_input_diag = _prepare_lab_input(
        img_before_lab,
        mode=lab_input_mode,
        percentile=float(params["lab_input_percentile"]),
        gamma=float(params["lab_input_gamma"]),
        eps=float(params["lab_input_eps"]),
        fixed_scale=params.get("lab_input_fixed_scale"),
    )
    lab = _rgb_to_lab_for_mode(
        img_lab_in,
        mode=lab_input_mode,
        gamma=float(params["lab_input_gamma"]),
    )
    L_before = (lab[..., 0] / 100.0).astype(np.float32)
    a = lab[..., 1].astype(np.float32)
    b = lab[..., 2].astype(np.float32)

    if params["zoned_tone_enabled"]:
        L_after_zoned, zoned_diag = _zoned_tone_mapping(
            L_before,
            shadow_threshold=float(params["shadow_threshold"]),
            highlight_threshold=float(params["highlight_threshold"]),
            shadow_gamma=float(params["shadow_gamma"]),
            mid_sigmoid_k=float(params["mid_sigmoid_k"]),
            mid_sigmoid_x0=float(params["mid_sigmoid_x0"]),
            highlight_local_strength=float(params["highlight_local_strength"]),
        )
    else:
        L_after_zoned = L_before
        zoned_diag = {"enabled": False}

    if params["highlight_rolloff_enabled"]:
        L_after_rolloff, rolloff_diag = _global_highlight_rolloff(
            L_after_zoned,
            alpha=float(params["highlight_rolloff_alpha"]),
            power=float(params["highlight_rolloff_power"]),
        )
    else:
        L_after_rolloff = L_after_zoned
        rolloff_diag = {"enabled": False}

    lab_out = np.stack([L_after_rolloff * 100.0, a, b], axis=2).astype(np.float32)
    rgb_from_lab = _lab_to_rgb_for_mode(
        lab_out,
        mode=lab_input_mode,
        gamma=float(params["lab_input_gamma"]),
    )
    if lab_input_mode == "srgb_encoded":
        srgb_like = np.maximum(rgb_from_lab, 0.0).astype(np.float32)
        stage2_linear_output = np.power(
            srgb_like,
            max(float(params["lab_input_gamma"]), 1e-6),
        ).astype(np.float32)
    elif lab_input_mode == "linear_direct":
        stage2_linear_output = np.maximum(rgb_from_lab, 0.0).astype(np.float32)
    else:
        raise ValueError(f"Unsupported lab_input_mode: {lab_input_mode}")

    stage2_preview_output = (
        make_stage2_preview_output(
            stage2_linear_output,
            preview_percentile=float(params["preview_percentile"]),
            preview_gamma=float(params["preview_gamma"]),
            preview_eps=float(params["preview_eps"]),
            fixed_scale=params.get("preview_fixed_scale"),
        )
        if bool(params["preview_enabled"])
        else np.clip(stage2_linear_output, 0.0, 1.0).astype(np.float32)
    )

    debug = {
        "gray_rgb": gray_diag.get("gray_rgb"),
        "gray_luma": gray_diag.get("gray_luma"),
        "img_before_lab": img_before_lab,
        "img_lab_in": img_lab_in,
        "L_before": L_before,
        "L_after_zoned": L_after_zoned,
        "L_after_rolloff": L_after_rolloff,
        "stage2_linear_output": stage2_linear_output,
        "stage2_preview_output": stage2_preview_output,
    }
    diagnostics = {
        "stage": "stage2_perceptual_tone_base",
        "note": (
            "Conservative Lab-luminance tone base. Default Lab input is "
            "percentile-normalized and gamma-encoded; preview is separate from linear output."
        ),
        "params": _jsonable_params(params),
        "steps": {
            "2a_lab_input_preparation": lab_input_diag,
            "2b_gray_anchor_normalization": gray_diag,
            "2c_zoned_tone_mapping": zoned_diag,
            "2d_global_highlight_rolloff": rolloff_diag,
            "2e_preview_output": _preview_diagnostics(stage2_preview_output, params),
        },
        "L_before_mean": float(L_before.mean()),
        "L_before_p95": float(np.percentile(L_before, 95.0)),
        "L_after_rolloff_mean": float(L_after_rolloff.mean()),
        "L_after_rolloff_p95": float(np.percentile(L_after_rolloff, 95.0)),
        "linear_output_mean_rgb": [float(v) for v in stage2_linear_output.mean(axis=(0, 1))],
        "linear_output_p95_rgb": [
            float(v) for v in np.percentile(stage2_linear_output.reshape(-1, 3), 95.0, axis=0)
        ],
        "linear_output_min_rgb": [float(v) for v in stage2_linear_output.reshape(-1, 3).min(axis=0)],
        "linear_output_max_rgb": [float(v) for v in stage2_linear_output.reshape(-1, 3).max(axis=0)],
    }
    return Stage2PerceptualToneResult(
        stage2_linear_output=stage2_linear_output,
        stage2_preview_output=stage2_preview_output,
        debug=debug,
        diagnostics=diagnostics,
    )


def _gray_anchor_normalization(
    img: np.ndarray,
    percentile: float,
    method: str,
    strength: float,
    eps: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    sat_proxy = img.max(axis=2) - img.min(axis=2)
    threshold = float(np.percentile(sat_proxy, float(np.clip(percentile, 0.0, 100.0))))
    mask = sat_proxy <= threshold
    if not bool(mask.any()):
        mask = np.ones(sat_proxy.shape, dtype=bool)

    gray_rgb = img[mask].mean(axis=0).astype(np.float32)
    if method == "rgb_mean":
        gray_luma = float(np.mean(gray_rgb))
    elif method == "mean_luma":
        gray_luma = float(np.dot(gray_rgb, _LUMA))
    else:
        raise ValueError(f"Unsupported gray_norm_method: {method}")

    strength = float(np.clip(strength, 0.0, 1.0))
    gray_eff = (1.0 - strength) * 1.0 + strength * gray_luma
    out = img / (gray_eff + max(float(eps), 1e-12))
    return out.astype(np.float32), {
        "enabled": True,
        "percentile": float(percentile),
        "threshold": threshold,
        "gray_rgb": [float(v) for v in gray_rgb],
        "gray_luma": gray_luma,
        "method": method,
        "strength": strength,
        "gray_eff": float(gray_eff),
        "mask_fraction": float(mask.mean()),
    }


def _prepare_lab_input(
    img: np.ndarray,
    mode: str,
    percentile: float,
    gamma: float,
    eps: float,
    fixed_scale: float | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    x = np.maximum(img.astype(np.float32), 0.0)
    if mode == "srgb_encoded":
        scale = float(fixed_scale) if fixed_scale is not None else _safe_percentile(x, percentile)
        x = x / (scale + max(float(eps), 1e-12))
        x = np.clip(x, 0.0, 1.0)
        gamma = max(float(gamma), 1e-6)
        x = np.power(x, 1.0 / gamma).astype(np.float32)
        return x, {
            "enabled": True,
            "mode": mode,
            "percentile": float(percentile),
            "scale": float(scale),
            "scale_source": "global_fixed" if fixed_scale is not None else "per_frame_percentile",
            "gamma": gamma,
            "input_mean_rgb": [float(v) for v in x.mean(axis=(0, 1))],
            "input_p95_rgb": [float(v) for v in np.percentile(x.reshape(-1, 3), 95.0, axis=0)],
        }
    if mode == "linear_direct":
        return x.astype(np.float32), {
            "enabled": True,
            "mode": mode,
            "warning": "Direct linear RGB to Lab is retained only as a debugging option.",
        }
    raise ValueError(f"Unsupported lab_input_mode: {mode}")


def _zoned_tone_mapping(
    L: np.ndarray,
    shadow_threshold: float,
    highlight_threshold: float,
    shadow_gamma: float,
    mid_sigmoid_k: float,
    mid_sigmoid_x0: float,
    highlight_local_strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    if not 0.0 < shadow_threshold < highlight_threshold <= 1.0:
        raise ValueError("Expected 0 < shadow_threshold < highlight_threshold <= 1.")

    L = np.maximum(L.astype(np.float32), 0.0)
    shadow_gamma = max(float(shadow_gamma), 0.01)
    highlight_local_strength = max(float(highlight_local_strength), 0.0)

    L_shadow = np.power(L, shadow_gamma)
    L_mid = 1.0 / (1.0 + np.exp(-float(mid_sigmoid_k) * (L - float(mid_sigmoid_x0))))
    L_high = highlight_threshold + (L - highlight_threshold) / (
        1.0 + highlight_local_strength * np.maximum(L - highlight_threshold, 0.0)
    )

    blend_width = max(0.02, 0.08 * (highlight_threshold - shadow_threshold))
    w_shadow = 1.0 - _smoothstep(shadow_threshold - blend_width, shadow_threshold + blend_width, L)
    w_high = _smoothstep(highlight_threshold - blend_width, highlight_threshold + blend_width, L)
    w_mid = np.clip(1.0 - w_shadow - w_high, 0.0, 1.0)
    w_sum = np.maximum(w_shadow + w_mid + w_high, _EPS)
    L_zoned = (w_shadow * L_shadow + w_mid * L_mid + w_high * L_high) / w_sum

    return L_zoned.astype(np.float32), {
        "enabled": True,
        "shadow_threshold": float(shadow_threshold),
        "highlight_threshold": float(highlight_threshold),
        "shadow_gamma": float(shadow_gamma),
        "mid_sigmoid_k": float(mid_sigmoid_k),
        "mid_sigmoid_x0": float(mid_sigmoid_x0),
        "highlight_local_strength": float(highlight_local_strength),
        "blend_width": float(blend_width),
        "L_after_zoned_mean": float(L_zoned.mean()),
        "L_after_zoned_p95": float(np.percentile(L_zoned, 95.0)),
    }


def _global_highlight_rolloff(
    L: np.ndarray,
    alpha: float,
    power: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    alpha = max(float(alpha), 0.0)
    power = max(float(power), 0.01)
    L = np.maximum(L.astype(np.float32), 0.0)
    L_out = L / (1.0 + alpha * np.power(L, power))
    return L_out.astype(np.float32), {
        "enabled": True,
        "alpha": alpha,
        "power": power,
        "L_after_rolloff_mean": float(L_out.mean()),
        "L_after_rolloff_p95": float(np.percentile(L_out, 95.0)),
    }


def _rgb_to_lab_for_mode(rgb: np.ndarray, mode: str, gamma: float) -> np.ndarray:
    if mode == "srgb_encoded":
        linear = np.power(np.clip(rgb, 0.0, 1.0), max(float(gamma), 1e-6))
        return _linear_rgb_to_lab(linear)
    if mode == "linear_direct":
        return _linear_rgb_to_lab(rgb)
    raise ValueError(f"Unsupported lab_input_mode: {mode}")


def _lab_to_rgb_for_mode(lab: np.ndarray, mode: str, gamma: float) -> np.ndarray:
    linear = _lab_to_linear_rgb(lab)
    if mode == "srgb_encoded":
        return np.power(np.maximum(linear, 0.0), 1.0 / max(float(gamma), 1e-6)).astype(np.float32)
    if mode == "linear_direct":
        return linear.astype(np.float32)
    raise ValueError(f"Unsupported lab_input_mode: {mode}")


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


def _preview_diagnostics(preview: np.ndarray, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(params["preview_enabled"]),
        "percentile": float(params["preview_percentile"]),
        "gamma": float(params["preview_gamma"]),
        "preview_mean_rgb": [float(v) for v in preview.mean(axis=(0, 1))],
        "preview_p95_rgb": [float(v) for v in np.percentile(preview.reshape(-1, 3), 95.0, axis=0)],
    }


def _diagnostics_without_lab(
    params: dict[str, Any],
    gray_diag: dict[str, Any],
    stage2_linear_output: np.ndarray,
) -> dict[str, Any]:
    return {
        "stage": "stage2_perceptual_tone_base",
        "note": "Perceptual Lab conversion disabled; Stage 2 returned gray-normalized linear RGB.",
        "params": _jsonable_params(params),
        "steps": {
            "2a_lab_input_preparation": {"enabled": False},
            "2b_gray_anchor_normalization": gray_diag,
            "2c_zoned_tone_mapping": {"enabled": False, "skipped_reason": "perceptual_space_disabled"},
            "2d_global_highlight_rolloff": {"enabled": False, "skipped_reason": "perceptual_space_disabled"},
        },
        "linear_output_mean_rgb": [float(v) for v in stage2_linear_output.mean(axis=(0, 1))],
        "linear_output_p95_rgb": [
            float(v) for v in np.percentile(stage2_linear_output.reshape(-1, 3), 95.0, axis=0)
        ],
        "linear_output_min_rgb": [float(v) for v in stage2_linear_output.reshape(-1, 3).min(axis=0)],
        "linear_output_max_rgb": [float(v) for v in stage2_linear_output.reshape(-1, 3).max(axis=0)],
    }


def _jsonable_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (float(value) if isinstance(value, np.floating) else value)
        for key, value in params.items()
    }
