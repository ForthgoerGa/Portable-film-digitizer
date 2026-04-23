from __future__ import annotations

import cv2
import numpy as np

from models import PipelineParams


def run_negative_pipeline(image_bgr: np.ndarray, params: PipelineParams) -> np.ndarray:
    # 1. Remove orange mask + invert in optical-density space
    demasked = _demask_and_invert(image_bgr)
    # 2. Per-channel percentile white balance
    balanced = _white_balance(demasked, params.wb_clip_percent)
    # 3. Gray-world correction on midtone pixels
    neutralized = _gray_world_midtones(balanced)
    # 4. LAB a/b channel cast removal (strength is now parameterised)
    neutralized = _neutralize_lab_cast(neutralized, params.lab_strength)
    # 5. Auto-level: stretch per-channel black/white points
    leveled = _stretch_percentiles(neutralized, params.black_point, params.white_point)
    # 6. Power-law gamma — lets agent lighten/darken the tonal response
    gammaed = _gamma_correction(leveled, params.gamma)
    # 7. Soft highlight rolloff — compresses near-white to recover blown areas
    rolled = _highlight_compression(gammaed, params.highlight_compression)
    # 8. Additive shadow lift (now shared between negative and positive)
    lifted = _shadow_lift(rolled, params.shadow_lift)
    # 9. Luminance CLAHE (tile grid is now parameterised)
    contrasted = _luminance_clahe(lifted, params.clahe_clip, _grid(params.clahe_grid))
    # 10. Build spatial cast mask then selectively desaturate cast regions
    mask = _cast_suppression_mask(contrasted, params.desat_sigma)
    desaturated = _selective_desaturation(contrasted, mask, params.desat_strength)
    # 11. Selective vibrance boost on muted colours
    vibrant = _vibrance_boost(desaturated, params.vibrance)
    # 12. Unsharp mask for detail recovery (sigma is now parameterised)
    sharpened = _unsharp_mask(vibrant, params.unsharp_amount, params.unsharp_sigma)
    # 13. Final gray-world pass on neutral (unsaturated) pixels
    return _final_neutralize(sharpened)


# ── Core helpers ──────────────────────────────────────────────────────────────

def _grid(size: int) -> int:
    """Clamp clahe_grid to one of the valid values: 4, 8, or 16."""
    if size <= 5:
        return 4
    if size <= 10:
        return 8
    return 16


def _demask_and_invert(image: np.ndarray) -> np.ndarray:
    src = image.astype(np.float32) + 1.0
    film_base = _estimate_film_base(image).astype(np.float32) + 1.0
    transmission = np.clip(src / film_base[None, None, :], 1e-4, 1.0)
    # Optical-density inversion removes the orange base more reliably than 255-pixel
    density = -np.log(transmission)
    scale = np.percentile(density.reshape(-1, 3), 99.0, axis=0)
    positive = density / np.maximum(scale[None, None, :], 1e-4)
    return np.clip(positive * 255.0, 0, 255).astype(np.uint8)


def _estimate_film_base(image: np.ndarray) -> np.ndarray:
    src = image.astype(np.float32)
    flat = src.reshape(-1, 3)
    luma = flat.mean(axis=1)

    bright = flat[luma >= np.percentile(luma, 99.5)]
    if bright.shape[0] < 1024:
        bright = flat[luma >= np.percentile(luma, 99.0)]

    height, width = image.shape[:2]
    border = max(8, min(height, width) // 20)
    border_pixels = np.concatenate(
        [
            src[:border, :, :].reshape(-1, 3),
            src[-border:, :, :].reshape(-1, 3),
            src[:, :border, :].reshape(-1, 3),
            src[:, -border:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    border_luma = border_pixels.mean(axis=1)
    border_bright = border_pixels[border_luma >= np.percentile(border_luma, 80.0)]

    samples = bright
    if border_bright.size:
        samples = np.concatenate([samples, border_bright], axis=0)

    film_base = np.percentile(samples, 95.0, axis=0)
    return np.clip(film_base, 32.0, 255.0)


def _white_balance(image: np.ndarray, clip_percent: float) -> np.ndarray:
    return _stretch_percentiles(image, clip_percent, 100.0 - clip_percent)


def _stretch_percentiles(image: np.ndarray, black: float, white: float) -> np.ndarray:
    src = image.astype(np.float32)
    out = np.empty_like(src)
    for channel in range(3):
        ch = src[..., channel]
        low, high = np.percentile(ch, [black, white])
        if high - low < 1.0:
            out[..., channel] = ch
            continue
        out[..., channel] = (ch - low) * (255.0 / (high - low))
    return np.clip(out, 0, 255).astype(np.uint8)


def _gamma_correction(image: np.ndarray, gamma: float) -> np.ndarray:
    """Power-law gamma: output = input^gamma.
    gamma < 1 brightens midtones; gamma > 1 darkens; 1.0 is a no-op."""
    if abs(gamma - 1.0) < 0.01:
        return image
    g = max(float(gamma), 0.01)
    table = np.array([((i / 255.0) ** g) * 255.0 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(image, table)


def _highlight_compression(image: np.ndarray, amount: float) -> np.ndarray:
    """Soft quadratic rolloff for near-white pixels.  Reduces clipping artefacts."""
    if amount <= 0.001:
        return image
    src = image.astype(np.float32) / 255.0
    compressed = src * (1.0 - float(amount) * src)
    return np.clip(compressed * 255.0, 0, 255).astype(np.uint8)


def _shadow_lift(image: np.ndarray, lift: float) -> np.ndarray:
    if lift == 0.0:
        return image
    return np.clip(image.astype(np.float32) + float(lift), 0, 255).astype(np.uint8)


def _luminance_clahe(image: np.ndarray, clip_limit: float, tile_grid: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=max(float(clip_limit), 0.01),
        tileGridSize=(tile_grid, tile_grid),
    )
    merged = cv2.merge((clahe.apply(l_chan), a_chan, b_chan))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _gray_world_midtones(image: np.ndarray) -> np.ndarray:
    src = image.astype(np.float32)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    low = np.percentile(gray, 10.0)
    high = np.percentile(gray, 90.0)
    mask = (gray >= low) & (gray <= high)
    pixels = src[mask] if mask.any() else src.reshape(-1, 3)
    means = pixels.mean(axis=0)
    target = float(means.mean())
    # Widened gain range from [0.75,1.35] → [0.5,1.8] to handle heavier casts
    gains = np.clip(target / np.maximum(means, 1e-6), 0.5, 1.8)
    return np.clip(src * gains[None, None, :], 0, 255).astype(np.uint8)


def _neutralize_lab_cast(image: np.ndarray, strength: float = 0.85) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    l_chan = lab[..., 0]
    low = np.percentile(l_chan, 10.0)
    high = np.percentile(l_chan, 90.0)
    mask = (l_chan >= low) & (l_chan <= high)
    if mask.any():
        a_offset = float(lab[..., 1][mask].mean() - 128.0)
        b_offset = float(lab[..., 2][mask].mean() - 128.0)
        lab[..., 1] -= a_offset * float(strength)
        lab[..., 2] -= b_offset * float(strength)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def _cast_suppression_mask(
    image: np.ndarray,
    sigma: float,
    sat_threshold: float = 0.22,
    val_threshold: float = 0.55,
) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[..., 1] / 255.0
    val = hsv[..., 2] / 255.0
    sat_term = np.clip((sat - sat_threshold) / max(1.0 - sat_threshold, 1e-6), 0.0, 1.0)
    val_term = np.clip((val - val_threshold) / max(1.0 - val_threshold, 1e-6), 0.0, 1.0)
    sigma = max(float(sigma), 0.01)
    mask = cv2.GaussianBlur(sat_term * val_term, (0, 0), sigmaX=sigma, sigmaY=sigma)
    scale = float(np.percentile(mask, 99.5))
    if scale > 1e-6:
        mask = np.clip(mask / scale, 0.0, 1.0)
    return mask


def _selective_desaturation(image: np.ndarray, mask: np.ndarray, strength: float) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    alpha = np.clip(mask * float(strength), 0.0, 1.0)
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 - alpha), 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _vibrance_boost(image: np.ndarray, amount: float) -> np.ndarray:
    """Selectively boost saturation on less-saturated colours (vibrance).
    Leaves already-saturated colours mostly unchanged to avoid over-saturation.
    Negative amount desaturates muted colours."""
    if abs(amount) < 0.01:
        return image
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[..., 1] / 255.0
    # Weight is inversely proportional to current saturation
    weight = 1.0 - sat
    delta = float(amount) * weight * 255.0
    hsv[..., 1] = np.clip(hsv[..., 1] + delta, 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _unsharp_mask(image: np.ndarray, amount: float, sigma: float = 1.2) -> np.ndarray:
    src = image.astype(np.float32)
    blur = cv2.GaussianBlur(src, (0, 0), sigmaX=max(sigma, 0.1), sigmaY=max(sigma, 0.1))
    return np.clip(src + float(amount) * (src - blur), 0, 255).astype(np.uint8)


def _final_neutralize(image: np.ndarray) -> np.ndarray:
    src = image.astype(np.float32)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32) / 255.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    low = np.percentile(gray, 15.0)
    high = np.percentile(gray, 85.0)
    mask = (sat < 0.35) & (gray >= low) & (gray <= high)
    pixels = src[mask] if mask.any() else src.reshape(-1, 3)
    means = pixels.mean(axis=0)
    target = float(means.mean())
    # Widened gain range from [0.85,1.18] → [0.75,1.35] for stronger cast removal
    gains = np.clip(target / np.maximum(means, 1e-6), 0.75, 1.35)
    return np.clip(src * gains[None, None, :], 0, 255).astype(np.uint8)
