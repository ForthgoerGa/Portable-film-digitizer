from __future__ import annotations

import cv2
import numpy as np

from models import PipelineParams


def run_positive_pipeline(image_bgr: np.ndarray, params: PipelineParams) -> np.ndarray:
    balanced = _white_balance(image_bgr, params.wb_clip_percent)
    lifted = _shadow_lift(balanced, params.shadow_lift)
    gammaed = _gamma_correction(lifted, params.gamma)
    rolled = _highlight_compression(gammaed, params.highlight_compression)
    contrasted = _luminance_clahe(rolled, params.clahe_clip, _grid(params.clahe_grid))
    vibrant = _vibrance_boost(contrasted, params.vibrance)
    return _unsharp_mask(vibrant, params.unsharp_amount, params.unsharp_sigma)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _grid(size: int) -> int:
    if size <= 5:
        return 4
    if size <= 10:
        return 8
    return 16


def _white_balance(image: np.ndarray, clip_percent: float) -> np.ndarray:
    src = image.astype(np.float32)
    out = np.empty_like(src)
    for channel in range(3):
        ch = src[..., channel]
        low, high = np.percentile(ch, [clip_percent, 100.0 - clip_percent])
        if high - low < 1.0:
            out[..., channel] = ch
            continue
        out[..., channel] = (ch - low) * (255.0 / (high - low))
    return np.clip(out, 0, 255).astype(np.uint8)


def _shadow_lift(image: np.ndarray, lift: float) -> np.ndarray:
    if lift == 0.0:
        return image
    return np.clip(image.astype(np.float32) + float(lift), 0, 255).astype(np.uint8)


def _gamma_correction(image: np.ndarray, gamma: float) -> np.ndarray:
    """output = input^gamma.  gamma < 1 brightens; gamma > 1 darkens; 1.0 is a no-op."""
    if abs(gamma - 1.0) < 0.01:
        return image
    g = max(float(gamma), 0.01)
    table = np.array([((i / 255.0) ** g) * 255.0 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(image, table)


def _highlight_compression(image: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0.001:
        return image
    src = image.astype(np.float32) / 255.0
    compressed = src * (1.0 - float(amount) * src)
    return np.clip(compressed * 255.0, 0, 255).astype(np.uint8)


def _luminance_clahe(image: np.ndarray, clip_limit: float, tile_grid: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=max(float(clip_limit), 0.01),
        tileGridSize=(tile_grid, tile_grid),
    )
    merged = cv2.merge((clahe.apply(l_chan), a_chan, b_chan))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _vibrance_boost(image: np.ndarray, amount: float) -> np.ndarray:
    if abs(amount) < 0.01:
        return image
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[..., 1] / 255.0
    weight = 1.0 - sat
    delta = float(amount) * weight * 255.0
    hsv[..., 1] = np.clip(hsv[..., 1] + delta, 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _unsharp_mask(image: np.ndarray, amount: float, sigma: float = 1.2) -> np.ndarray:
    src = image.astype(np.float32)
    blur = cv2.GaussianBlur(src, (0, 0), sigmaX=max(sigma, 0.1), sigmaY=max(sigma, 0.1))
    return np.clip(src + float(amount) * (src - blur), 0, 255).astype(np.uint8)
