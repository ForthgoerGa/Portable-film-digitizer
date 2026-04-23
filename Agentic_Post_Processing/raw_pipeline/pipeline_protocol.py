"""pipeline_protocol.py — Correct multi-frame inversion pipeline.

Implements the capture protocol from pipeline_update.md (openclaw):

    Frame 1  →  flat-field reference   (backlight only, no film)
    Frame 2  →  base reference          (contains backlight, border/base, and partial film)
    Frames 3+→  image frames to invert

Design principles:
  - deterministic, physically motivated functions only
  - Frame 2 is used *as a whole* for base estimation — no border detection
  - no assumptions about Frame 2 geometry for later frames
  - aesthetic rendering is separated from technical inversion

Public API
----------
    result = run_protocol(
        flat_path  = "frame1_flat.dng",
        base_path  = "frame2_base.dng",
        frame_paths= ["frame3.dng", "frame4.dng"],
        output_dir = "/tmp/out",
    )

Returns a dict with per-frame results, stage metrics, and output paths.
"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Tuning constants
# ──────────────────────────────────────────────────────────────────────────────

# ── Base detection constants (Frame 2 has 3 zones: backlight / border+base / image) ──
# After flat-field correction, pure backlight ≈ 1.0; base is intermediate; image is variable.

# Pixels in the lower N-th percentile of local variance are treated as spatially uniform.
_BASE_LOCAL_VAR_PERCENTILE = 35.0

# Exclude the brightest M% of luma from base candidates (that's pure backlight, near 1.0).
_BASE_LUMA_TOP_CLIP = 90.0

# Exclude the darkest M% of luma from base candidates (those are dense film areas).
_BASE_LUMA_BOTTOM_CLIP = 5.0

# Max side (pixels) for the proxy used in variance computation — keeps the filter fast.
_BASE_PROXY_MAX_SIDE = 512

# Percentile of per-channel values within the detected base cluster used as base estimate.
_BASE_CHANNEL_PERCENTILE = 50.0   # median of the detected base cluster

# Percentile of per-channel density used as the upper-density reference
# (conservative; avoids extreme outlier pixels setting too tight a range).
_UPPER_DENSITY_PERCENTILE = 99.0

# Minimum meaningful density spread before the inversion is considered reliable.
_MIN_DENSITY_SPAN = 0.05

# Epsilon to protect log(0) and safe division.
_EPS = 1e-6


# ──────────────────────────────────────────────────────────────────────────────
# Stage 1  — Flat-field
# ──────────────────────────────────────────────────────────────────────────────

def compute_flatfield_model(flat_rgb: np.ndarray) -> dict[str, Any]:
    """Build a flat-field correction model from a blank-light frame.

    Parameters
    ----------
    flat_rgb:
        float32 H×W×3, linear, sensor-normalised to [0, 1].

    Returns
    -------
    dict with keys:
      ``channel_means``   — (R, G, B) global means before normalisation
      ``illumination_map``— float32 H×W×3 low-frequency illumination field
    """
    flat = flat_rgb.astype(np.float32)
    channel_means = (
        float(flat[:, :, 0].mean()),
        float(flat[:, :, 1].mean()),
        float(flat[:, :, 2].mean()),
    )
    global_mean = max(float(flat.mean()), _EPS)
    normalised = flat / global_mean

    # Estimate low-frequency field via downsample → blur → upsample.
    # Keeps the Gaussian kernel small regardless of capture resolution.
    h, w = flat.shape[:2]
    max_side = 512
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        sh, sw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        small = cv2.resize(normalised, (sw, sh), interpolation=cv2.INTER_AREA)
    else:
        small = normalised
        sh, sw = h, w

    sigma = max(np.sqrt(sh ** 2 + sw ** 2) * 0.10, 5.0)
    blurred = np.empty_like(small)
    for c in range(3):
        blurred[:, :, c] = cv2.GaussianBlur(small[:, :, c], (0, 0), sigmaX=sigma, sigmaY=sigma)

    if long_side > max_side:
        illum_map = cv2.resize(blurred, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        illum_map = blurred

    log.info(
        "Flat-field model: size=%dx%d  channel_means=(%.4f, %.4f, %.4f)  sigma=%.1f",
        w, h, *channel_means, sigma,
    )
    return {"channel_means": channel_means, "illumination_map": illum_map.astype(np.float32)}


def apply_flatfield_correction(frame_rgb: np.ndarray, flat_model: dict[str, Any]) -> np.ndarray:
    """Divide the frame by the illumination map to remove shading and colour bias.

    Parameters
    ----------
    frame_rgb:
        float32 H×W×3 sensor-normalised frame.
    flat_model:
        Output of ``compute_flatfield_model``.

    Returns
    -------
    float32 H×W×3 corrected frame, clipped to [0, 1].
    """
    frame = frame_rgb.astype(np.float32)
    illum = flat_model["illumination_map"].astype(np.float32)

    fh, fw = frame.shape[:2]
    ih, iw = illum.shape[:2]
    if (ih, iw) != (fh, fw):
        log.warning("Resizing illumination map (%dx%d → %dx%d).", iw, ih, fw, fh)
        illum = cv2.resize(illum, (fw, fh), interpolation=cv2.INTER_LINEAR)

    corrected = frame / np.clip(illum, _EPS, None)
    return np.clip(corrected, 0.0, 1.0).astype(np.float32)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2  — Base modelling from Frame 2
# ──────────────────────────────────────────────────────────────────────────────

def extract_base_region_candidates(base_frame_rgb: np.ndarray) -> list[dict[str, Any]]:
    """Detect film-border/base pixels within Frame 2 and return colour candidates.

    Frame 2 contains three distinct zones after flat-field correction:

    1. **Pure backlight** — very bright (luma ≈ 1.0), low spatial variance.
       (Illumination visible through the film gate where no film base is present.)
    2. **Film border/base** — intermediate brightness (~0.1–0.5), low spatial
       variance, characteristic orange/amber hue.  ← **we want these pixels**
    3. **Film image content** — variable brightness, high spatial variance.

    Detection strategy
    ------------------
    - Downsample to a fast proxy.
    - Compute local luminance variance with a box filter.
    - Keep pixels whose variance is below the ``_BASE_LOCAL_VAR_PERCENTILE``
      threshold (uniform = not image content).
    - Exclude the top ``_BASE_LUMA_TOP_CLIP`` percentile of luma (pure backlight)
      and the bottom ``_BASE_LUMA_BOTTOM_CLIP`` percentile (dense film).
    - The surviving pixels are the film-border/base zone.
    - A 3×3 spatial grid further tests spatial consistency.

    Parameters
    ----------
    base_frame_rgb:
        float32 H×W×3 flat-field-corrected Frame 2.

    Returns
    -------
    List of dicts, each with:
      ``region``   — name string
      ``rgb``      — shape (3,) float32 colour estimate
      ``std``      — shape (3,) float32 per-channel std
      ``count``    — int number of proxy pixels sampled
      ``luma_mean``— float mean luminance of sampled pixels
    """
    h, w = base_frame_rgb.shape[:2]

    # ── Build a fast proxy ────────────────────────────────────────────────────
    long_side = max(h, w)
    if long_side > _BASE_PROXY_MAX_SIDE:
        scale = _BASE_PROXY_MAX_SIDE / long_side
        sh = max(1, int(round(h * scale)))
        sw = max(1, int(round(w * scale)))
        proxy = cv2.resize(
            base_frame_rgb.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA
        )
    else:
        proxy = base_frame_rgb.astype(np.float32)
        sh, sw = h, w

    luma = (
        0.2126 * proxy[:, :, 0]
        + 0.7152 * proxy[:, :, 1]
        + 0.0722 * proxy[:, :, 2]
    )  # shape (sh, sw)

    # ── Local variance via box filter (var = E[x²] - E[x]²) ─────────────────
    ksize = max(5, (int(round(min(sh, sw) * 0.04)) | 1))  # ~4 % of short dim, odd
    luma_f = luma.astype(np.float32)
    mean_l  = cv2.boxFilter(luma_f,         -1, (ksize, ksize))
    mean_sq = cv2.boxFilter(luma_f * luma_f, -1, (ksize, ksize))
    local_var = np.maximum(mean_sq - mean_l ** 2, 0.0)

    # ── Masks ─────────────────────────────────────────────────────────────────
    var_thresh  = float(np.percentile(local_var, _BASE_LOCAL_VAR_PERCENTILE))
    luma_high   = float(np.percentile(luma, _BASE_LUMA_TOP_CLIP))
    luma_low    = float(np.percentile(luma, _BASE_LUMA_BOTTOM_CLIP))

    low_var_mask     = local_var <= max(var_thresh, 1e-8)
    brightness_mask  = (luma >= luma_low) & (luma <= luma_high)
    base_mask        = low_var_mask & brightness_mask  # (sh, sw) bool

    n_base = int(base_mask.sum())
    if n_base < 64:
        log.warning(
            "extract_base_region_candidates: only %d base pixels after filtering; "
            "falling back to low-variance only.",
            n_base,
        )
        base_mask = low_var_mask
        n_base = int(base_mask.sum())

    flat_proxy = proxy.reshape(-1, 3)
    flat_mask  = base_mask.reshape(-1)
    flat_luma  = luma.reshape(-1)

    candidates: list[dict[str, Any]] = []

    # Global candidate — all detected base pixels.
    if n_base > 0:
        base_pix = flat_proxy[flat_mask]
        rgb_global = np.percentile(base_pix, _BASE_CHANNEL_PERCENTILE, axis=0).astype(
            np.float32
        )
        candidates.append({
            "region": "low_variance_base",
            "rgb": rgb_global,
            "std": base_pix.std(axis=0).astype(np.float32),
            "count": n_base,
            "luma_mean": float(flat_luma[flat_mask].mean()),
        })

    # Spatial 3×3 grid for per-region consistency check.
    for gi in range(3):
        for gj in range(3):
            r0, r1 = gi * sh // 3, (gi + 1) * sh // 3
            c0, c1 = gj * sw // 3, (gj + 1) * sw // 3
            cell_mask  = base_mask[r0:r1, c0:c1].reshape(-1)
            cell_pix   = proxy[r0:r1, c0:c1].reshape(-1, 3).astype(np.float32)
            cell_luma  = luma[r0:r1, c0:c1].reshape(-1)
            n_cell = int(cell_mask.sum())
            if n_cell < 32:
                continue
            cell_base = cell_pix[cell_mask]
            rgb = np.percentile(cell_base, _BASE_CHANNEL_PERCENTILE, axis=0).astype(
                np.float32
            )
            candidates.append({
                "region": f"grid_{gi}_{gj}",
                "rgb": rgb,
                "std": cell_base.std(axis=0).astype(np.float32),
                "count": n_cell,
                "luma_mean": float(cell_luma[cell_mask].mean()),
            })

    log.info(
        "extract_base_region_candidates: %d base pixels (low-var + mid-luma); "
        "%d spatial grid candidates.",
        n_base,
        len(candidates) - 1,
    )
    return candidates


def score_base_region_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score each candidate by how consistent it is with the others.

    Adds a ``score`` field (0–1) and ``consistent`` bool to each candidate.
    Candidates far from the median RGB are flagged as inconsistent (e.g. dark
    corners outside the film gate).

    Parameters
    ----------
    candidates:
        Output of ``extract_base_region_candidates``.

    Returns
    -------
    The same list, mutated in-place with ``score`` and ``consistent`` fields.
    """
    if not candidates:
        return candidates

    rgb_stack = np.stack([c["rgb"] for c in candidates], axis=0)  # (N, 3)
    median_rgb = np.median(rgb_stack, axis=0)                       # (3,)

    # Relative deviation from median.
    diffs = np.linalg.norm(rgb_stack - median_rgb[None, :], axis=1)  # (N,)
    median_norm = np.linalg.norm(median_rgb) + _EPS
    rel_diffs = diffs / median_norm

    # Threshold: candidates within 10% of median are "consistent".
    threshold = max(float(np.percentile(rel_diffs, 70.0)), 0.05)

    for i, c in enumerate(candidates):
        c["score"] = float(np.clip(1.0 - rel_diffs[i] / (threshold * 2.0), 0.0, 1.0))
        c["consistent"] = bool(rel_diffs[i] <= threshold)

    n_consistent = sum(1 for c in candidates if c["consistent"])
    log.debug(
        "score_base_region_candidates: %d / %d candidates consistent.",
        n_consistent, len(candidates),
    )
    return candidates


def build_base_model(candidates: list[dict[str, Any]]) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Fuse scored candidates into a single base-RGB estimate.

    Consistent candidates are median-fused.  If none are consistent, all
    candidates are used.

    Parameters
    ----------
    candidates:
        Output of ``score_base_region_candidates``.

    Returns
    -------
    (base_rgb, confidence, diagnostics)
        base_rgb:   shape (3,) float32 — the estimated base colour.
        confidence: float [0, 1] — how reliable the estimate is.
        diagnostics: dict of scalar metadata.
    """
    if not candidates:
        raise ValueError("build_base_model: no candidates provided.")

    # Prefer consistent candidates; fall back to all if none pass.
    good = [c for c in candidates if c.get("consistent", True)]
    if not good:
        good = candidates

    rgb_stack = np.stack([c["rgb"] for c in good], axis=0)
    base_rgb = np.median(rgb_stack, axis=0).astype(np.float32)
    base_rgb = np.clip(base_rgb, _EPS, 1.0)

    # Spread: how similar are the good candidates?
    stds = rgb_stack.std(axis=0)
    rel_spread = float(stds.mean() / (base_rgb.mean() + _EPS))

    # Confidence is higher when spread is low and many candidates agree.
    frac_consistent = len(good) / max(len(candidates), 1)
    confidence = float(np.clip(frac_consistent * (1.0 - rel_spread * 4.0), 0.0, 1.0))

    log.info(
        "Base model: R=%.4f G=%.4f B=%.4f  rel_spread=%.4f  confidence=%.3f  "
        "candidates=%d/%d consistent",
        *base_rgb, rel_spread, confidence, len(good), len(candidates),
    )

    diagnostics = {
        "base_rgb": [float(v) for v in base_rgb],
        "rel_spread": rel_spread,
        "confidence": confidence,
        "n_candidates": len(candidates),
        "n_consistent": len(good),
    }
    return base_rgb, confidence, diagnostics


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2b — Ratio-based base estimation (preferred over extract_base_region_candidates)
# ──────────────────────────────────────────────────────────────────────────────

def estimate_base_ratio(
    base_raw_rgb: np.ndarray,
    flat_raw_rgb: np.ndarray,
    flat_channel_means: tuple[float, float, float],
    proxy_max_side: int = 512,
    ratio_percentile: float = 90.0,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Estimate film base colour from the pixel-wise base/flat ratio.

    Physical model
    --------------
    At any pixel position p in the film-base zone::

        base_raw[p, c] = flat_raw[p, c] × T_base[c]

    where T_base[c] is the spectrally selective base transmission (orange mask).
    The illumination map cancels in the ratio, so::

        base_rgb[c] = T_base[c] × flat_channel_mean[c]

    This is position-independent — it works even in corners where the
    illumination map is very different from the centre.

    Frame 2 zone detection
    ----------------------
    Frame 2 contains three zones (all in sensor-normalised space):

    * **Pure backlight** — bright, low variance, ratio ≈ 1.0 (no film).
    * **Film base**      — intermediate brightness, low variance, ratio < 1
                           with R > G > B (orange-mask signature).
    * **Image content**  — variable brightness, *high* local variance.

    We identify the base zone by combining a local-variance filter (exclude
    image content) with a luma-range filter (exclude pure backlight and the
    very darkest dense-shadow areas).

    Parameters
    ----------
    base_raw_rgb:
        float32 H×W×3 sensor-normalised Frame 2 (before flat-field correction).
    flat_raw_rgb:
        float32 H×W×3 sensor-normalised Frame 1 (flat-field reference).
    flat_channel_means:
        Per-channel global means of the flat frame, from ``compute_flatfield_model``.
    proxy_max_side:
        Max pixel dimension for the working proxy (keeps computation fast).
    ratio_percentile:
        Percentile of per-channel ratio values within the detected base zone.
        90 is a good default: it gives base values high enough to keep
        clipping below ~25 % while preserving the R > G > B order.

    Returns
    -------
    (base_rgb, confidence, diagnostics)
        base_rgb:    shape (3,) float32 film-base colour estimate.
        confidence:  float [0, 1].
        diagnostics: dict of scalar metadata.
    """
    h, w = base_raw_rgb.shape[:2]
    long_side = max(h, w)

    if long_side > proxy_max_side:
        scale = proxy_max_side / long_side
        sh = max(1, int(round(h * scale)))
        sw = max(1, int(round(w * scale)))
        base_p = cv2.resize(
            base_raw_rgb.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA
        )
        flat_p = cv2.resize(
            flat_raw_rgb.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA
        )
    else:
        base_p = base_raw_rgb.astype(np.float32)
        flat_p = flat_raw_rgb.astype(np.float32)
        sh, sw = h, w

    # Per-pixel ratio: base / flat, channel-wise.
    ratio = base_p / np.clip(flat_p, _EPS, None)  # (sh, sw, 3)

    # Luma of the base proxy (green-dominant without WB in camera-native space).
    base_luma = (
        0.2126 * base_p[:, :, 0]
        + 0.7152 * base_p[:, :, 1]
        + 0.0722 * base_p[:, :, 2]
    )

    # ── Local-variance filter to exclude image-content pixels ────────────────
    ksize = max(5, (int(round(min(sh, sw) * 0.04)) | 1))
    bl_f = base_luma.astype(np.float32)
    mean_l  = cv2.boxFilter(bl_f, -1, (ksize, ksize))
    mean_sq = cv2.boxFilter(bl_f * bl_f, -1, (ksize, ksize))
    local_var = np.maximum(mean_sq - mean_l ** 2, 0.0)

    # Keep the lower 40 % of local variance (spatially uniform regions).
    var_thresh = float(np.percentile(local_var, 40.0))
    low_var_mask = local_var <= max(var_thresh, 1e-9)

    # ── Luma filter: exclude pure backlight (top 8 %) and near-black (bot 5 %) ─
    luma_high = float(np.percentile(base_luma, 92.0))
    luma_low  = float(np.percentile(base_luma,  5.0))
    mid_luma_mask = (base_luma >= luma_low) & (base_luma <= luma_high)

    base_zone_mask = low_var_mask & mid_luma_mask
    n = int(base_zone_mask.sum())

    if n < 200:
        log.warning(
            "estimate_base_ratio: only %d pixels in base zone (low-var + mid-luma); "
            "relaxing to low-var only.",
            n,
        )
        base_zone_mask = low_var_mask
        n = int(base_zone_mask.sum())

    if n < 50:
        log.warning(
            "estimate_base_ratio: extremely few base pixels (%d); "
            "falling back to global median.",
            n,
        )
        base_zone_mask = np.ones((sh, sw), dtype=bool)
        n = sh * sw

    flat_mask = base_zone_mask.reshape(-1)
    ratio_flat = ratio.reshape(-1, 3)
    base_zone_ratios = ratio_flat[flat_mask]  # (n, 3)

    # Per-channel high-percentile ratio in the base zone.
    ratio_pct = np.percentile(base_zone_ratios, ratio_percentile, axis=0).astype(np.float32)

    # Scale by flat global means → absolute base estimate.
    fm = np.array(flat_channel_means, dtype=np.float32)
    base_rgb = ratio_pct * fm
    base_rgb = np.clip(base_rgb, _EPS, 1.0)

    ratio_std = base_zone_ratios.std(axis=0)
    rel_spread = float(ratio_std.mean() / (float(ratio_pct.mean()) + _EPS))
    confidence = float(np.clip(1.0 - rel_spread * 2.0, 0.0, 1.0))

    log.info(
        "estimate_base_ratio: base_rgb=(%.4f, %.4f, %.4f)  "
        "ratio_pct=(%.3f, %.3f, %.3f)  n=%d  conf=%.3f",
        *base_rgb, *ratio_pct, n, confidence,
    )

    return base_rgb, confidence, {
        "base_rgb":          [float(v) for v in base_rgb],
        "ratio_pct":         [float(v) for v in ratio_pct],
        "flat_channel_means": list(flat_channel_means),
        "n_pixels":          n,
        "confidence":        confidence,
        "rel_spread":        rel_spread,
        "method":            "base_flat_ratio",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Stage 3  — Technical inversion
# ──────────────────────────────────────────────────────────────────────────────

def invert_negative_frame(
    frame_rgb: np.ndarray,
    base_rgb: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Invert a flat-field-corrected negative frame using the base model.

    Algorithm
    ---------
    1. transmission = frame / base   (element-wise; clipped to [eps, 1])
    2. density      = -log(transmission)  per-channel relative density
    3. upper_ref    = conservative 99th-percentile of density per channel
    4. density_norm = density / upper_ref   (maps [0, upper_ref] → [0, 1])
    5. positive     = 1 − density_norm     (inverts: dark negative → light positive)

    Parameters
    ----------
    frame_rgb:
        float32 H×W×3 flat-field-corrected negative frame.
    base_rgb:
        shape (3,) float32 base estimate from ``build_base_model``.

    Returns
    -------
    (positive_rgb, diagnostics)
        positive_rgb: float32 H×W×3 technical positive, clipped to [0, 1].
        diagnostics:  scalar metrics dict.
    """
    frame = frame_rgb.astype(np.float32)
    base  = np.clip(base_rgb, _EPS, 1.0).astype(np.float32)

    # Transmission: how much light passed through relative to clear base.
    # Values > 1 (frame brighter than base, noise/overexposure) → clipped to 1.
    transmission = np.clip(frame / base[np.newaxis, np.newaxis, :], _EPS, 1.0)

    # Relative optical density above base (always ≥ 0).
    density = -np.log(transmission)  # H×W×3, float32

    # Per-channel upper density reference from scene content.
    flat_density = density.reshape(-1, 3)
    upper_ref = np.percentile(flat_density, _UPPER_DENSITY_PERCENTILE, axis=0).astype(np.float32)
    upper_ref = np.clip(upper_ref, _MIN_DENSITY_SPAN, None)

    # Normalise density to [0, 1] then invert to get positive.
    density_norm = density / upper_ref[np.newaxis, np.newaxis, :]
    positive = np.clip(1.0 - density_norm, 0.0, 1.0).astype(np.float32)

    # ── Diagnostics ──
    means = positive.mean(axis=(0, 1))
    channel_spread = float(means.max() - means.min())
    clipping_ratio = float(
        ((positive <= 0.0) | (positive >= 1.0)).mean()
    )

    if clipping_ratio > 0.15:
        log.warning(
            "invert_negative_frame: high clipping (%.1f%%). "
            "Check flat-field quality and base estimate.",
            clipping_ratio * 100,
        )

    diag = {
        "mean_r": float(means[0]),
        "mean_g": float(means[1]),
        "mean_b": float(means[2]),
        "channel_spread": channel_spread,
        "clipping_ratio": clipping_ratio,
        "upper_ref_r": float(upper_ref[0]),
        "upper_ref_g": float(upper_ref[1]),
        "upper_ref_b": float(upper_ref[2]),
        "density_p50_mean": float(np.percentile(flat_density, 50, axis=0).mean()),
        "density_p99_mean": float(upper_ref.mean()),
    }
    log.info(
        "Inversion: means=(%.3f, %.3f, %.3f)  spread=%.4f  clipping=%.3f",
        *means, channel_spread, clipping_ratio,
    )
    return positive, diag


# ──────────────────────────────────────────────────────────────────────────────
# Stage 4  — Neutralisation
# ──────────────────────────────────────────────────────────────────────────────

def neutralize_positive_frame(positive_rgb: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Reduce residual colour cast in the inverted positive.

    Applies gray-world neutralisation: scales each channel so all three
    channel means are equal to the overall luminance mean.  This is a soft
    correction — if the result is already neutral it does nothing.

    Parameters
    ----------
    positive_rgb:
        float32 H×W×3 technical positive from ``invert_negative_frame``.

    Returns
    -------
    (neutralised_rgb, diagnostics)
    """
    img = positive_rgb.astype(np.float32)
    means = img.mean(axis=(0, 1))  # (3,)
    gray_mean = float(means.mean())

    if gray_mean < _EPS:
        log.warning("neutralize_positive_frame: near-zero image mean; skipping neutralisation.")
        return img, {"skipped": True, "gray_mean_before": gray_mean}

    gains = gray_mean / np.clip(means, _EPS, None)

    # Limit gains to avoid amplifying noise in nearly-zero channels.
    gains = np.clip(gains, 0.5, 2.0).astype(np.float32)

    neutralised = np.clip(img * gains[np.newaxis, np.newaxis, :], 0.0, 1.0).astype(np.float32)
    means_after = neutralised.mean(axis=(0, 1))

    diag = {
        "gains_r": float(gains[0]),
        "gains_g": float(gains[1]),
        "gains_b": float(gains[2]),
        "gray_mean_before": gray_mean,
        "gray_mean_after": float(neutralised.mean()),
        "channel_spread_before": float(means.max() - means.min()),
        "channel_spread_after":  float(means_after.max() - means_after.min()),
    }
    log.info(
        "Neutralisation: gains=(%.3f, %.3f, %.3f)  spread %.4f → %.4f",
        *gains,
        diag["channel_spread_before"],
        diag["channel_spread_after"],
    )
    return neutralised, diag


# ──────────────────────────────────────────────────────────────────────────────
# Stage 5/6  — Metrics, preview, export
# ──────────────────────────────────────────────────────────────────────────────

def compute_output_metrics(positive_rgb: np.ndarray) -> dict[str, float]:
    """Compute scalar quality metrics on the finished positive image.

    Returns
    -------
    dict with: ``gray_mean``, ``gray_std``, ``clipped_black_ratio``,
    ``clipped_white_ratio``, ``channel_spread``, ``mean_saturation``,
    ``laplacian_variance``, ``blue_red_delta``, ``green_magenta_delta``.
    """
    img = positive_rgb.astype(np.float32)
    gray = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

    means = img.mean(axis=(0, 1))
    channel_spread = float(means.max() - means.min())

    # Saturation in HSV.
    img_u8 = np.clip(img * 255, 0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(img_u8, cv2.COLOR_RGB2HSV)
    mean_saturation = float(hsv[:, :, 1].mean()) / 255.0

    # Sharpness (Laplacian variance).
    gray_u8 = np.clip(gray * 255, 0, 255).astype(np.uint8)
    lap_var = float(cv2.Laplacian(gray_u8, cv2.CV_64F).var())

    return {
        "gray_mean":           float(gray.mean()),
        "gray_std":            float(gray.std()),
        "clipped_black_ratio": float((img <= 0.0).mean()),
        "clipped_white_ratio": float((img >= 1.0).mean()),
        "channel_spread":      channel_spread,
        "mean_saturation":     mean_saturation,
        "laplacian_variance":  lap_var,
        "blue_red_delta":      float(means[2] - means[0]),
        "green_magenta_delta": float(means[1] - (means[0] + means[2]) / 2.0),
    }


def render_preview(
    positive_rgb: np.ndarray,
    params: dict[str, Any] | None = None,
) -> np.ndarray:
    """Apply light aesthetic rendering to produce a viewer-ready preview.

    Kept minimal: residual WB gain, mild contrast, optional unsharp mask.
    Does NOT compensate for inversion failures.

    Parameters
    ----------
    positive_rgb:
        float32 H×W×3 neutralised positive.
    params:
        Optional override dict. Keys: ``wb_gains`` (3-tuple), ``gamma`` (float),
        ``contrast`` (float), ``unsharp_amount`` (float),
        ``unsharp_sigma`` (float).
    """
    p = {
        "wb_gains":     (1.0, 1.0, 1.0),
        "gamma":        1.0,
        "contrast":     0.0,
        "unsharp_amount": 0.3,
        "unsharp_sigma":  1.2,
    }
    if params:
        p.update(params)

    img = positive_rgb.astype(np.float32).copy()

    # Residual WB.
    gains = np.array(p["wb_gains"], dtype=np.float32)
    img *= gains[np.newaxis, np.newaxis, :]
    img = np.clip(img, 0.0, 1.0)

    # Gamma.
    gamma = float(p["gamma"])
    if abs(gamma - 1.0) > 0.001:
        img = np.power(np.clip(img, _EPS, 1.0), gamma)

    # Contrast (linear around 0.5).
    contrast = float(p["contrast"])
    if abs(contrast) > 0.001:
        img = np.clip(0.5 + (img - 0.5) * (1.0 + contrast), 0.0, 1.0)

    # Unsharp mask.
    amount = float(p["unsharp_amount"])
    if amount > 0.001:
        sigma = float(p["unsharp_sigma"])
        blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma)
        img = np.clip(img + amount * (img - blurred), 0.0, 1.0)

    return img.astype(np.float32)


# ──────────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_protocol(
    flat_path: str,
    base_path: str,
    frame_paths: list[str],
    output_dir: str,
    film_type: str = "color_negative",
    render_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full multi-frame inversion protocol.

    Parameters
    ----------
    flat_path:
        Frame 1 — flat-field RAW (backlight only, no film).
    base_path:
        Frame 2 — clear-film-base RAW (no image content).
    frame_paths:
        Frames 3+ — negative image frames to invert.
    output_dir:
        Directory for output images and metadata.
    film_type:
        ``"color_negative"`` (default), ``"bw_negative"``, or ``"positive"``.
    render_params:
        Optional rendering overrides passed to ``render_preview``.

    Returns
    -------
    dict with:
      ``status``          — "ok" | "error"
      ``film_type``       — as supplied
      ``flat_model_diag`` — flat-field channel means
      ``base_model``      — base estimation diagnostics
      ``frames``          — list of per-frame result dicts
      ``output_dir``      — resolved output directory
    """
    from .raw_ingest import load_raw_frame, load_image_as_raw_frame
    from .sensor_normalization import prepare_linear_rgb

    output_dir = str(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    def _load(path: str) -> np.ndarray:
        """Load a file and return sensor-normalised float32 RGB."""
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
            raw = load_image_as_raw_frame(path)
        else:
            try:
                raw = load_raw_frame(path)
            except ImportError:
                raw = load_image_as_raw_frame(path)
        return prepare_linear_rgb(raw)

    try:
        # ── Stage 1: Flat-field ───────────────────────────────────────────────
        log.info("=== Stage 1: Flat-field ===")
        flat_rgb = _load(flat_path)
        flat_model = compute_flatfield_model(flat_rgb)

        # ── Stage 2: Base modelling from Frame 2 ─────────────────────────────
        log.info("=== Stage 2: Base modelling from Frame 2 ===")
        base_raw_rgb = _load(base_path)

        # Ratio-based estimation: cancels illumination map, reveals orange-mask.
        # Uses sensor-normalised frames (before flat-field correction) so the
        # per-pixel ratio base/flat is position-independent.
        base_rgb, base_confidence, base_diag = estimate_base_ratio(
            base_raw_rgb=base_raw_rgb,
            flat_raw_rgb=flat_rgb,
            flat_channel_means=flat_model["channel_means"],
        )

        # Save base frame diagnostics image (flat-corrected, for visual inspection).
        base_corrected = apply_flatfield_correction(base_raw_rgb, flat_model)
        _save_rgb(base_corrected, output_dir, "00_base_frame_corrected.png")

        # ── Stages 3–6: Per-frame inversion ───────────────────────────────────
        frame_results: list[dict[str, Any]] = []

        for frame_path in frame_paths:
            stem = Path(frame_path).stem
            log.info("=== Processing frame: %s ===", stem)
            frame_result = _process_one_frame(
                frame_path=frame_path,
                flat_model=flat_model,
                base_rgb=base_rgb,
                film_type=film_type,
                render_params=render_params,
                output_dir=output_dir,
                stem=stem,
                loader=_load,
            )
            frame_results.append(frame_result)

        return {
            "status": "ok",
            "film_type": film_type,
            "flat_model_diag": {
                "channel_means": list(flat_model["channel_means"]),
            },
            "base_model": base_diag,
            "frames": frame_results,
            "output_dir": output_dir,
        }

    except Exception:
        tb = traceback.format_exc()
        log.error("run_protocol failed:\n%s", tb)
        return {"status": "error", "error": tb, "frames": [], "output_dir": output_dir}


def _process_one_frame(
    frame_path: str,
    flat_model: dict[str, Any],
    base_rgb: np.ndarray,
    film_type: str,
    render_params: dict[str, Any] | None,
    output_dir: str,
    stem: str,
    loader,
) -> dict[str, Any]:
    """Invert, neutralise, render and save one image frame.  Internal helper."""
    try:
        # Stage 3: Technical inversion.
        frame_raw_rgb = loader(frame_path)
        frame_corrected = apply_flatfield_correction(frame_raw_rgb, flat_model)

        if film_type in ("color_negative", "bw_negative"):
            positive, inv_diag = invert_negative_frame(frame_corrected, base_rgb)
        else:
            # Positive / slide: skip inversion, apply WB only.
            positive = frame_corrected.copy()
            inv_diag = {"note": "positive film; inversion skipped"}

        _save_rgb(positive, output_dir, f"{stem}_01_technical_positive.png")

        # Stage 4: Neutralisation.
        neutralised, neut_diag = neutralize_positive_frame(positive)
        _save_rgb(neutralised, output_dir, f"{stem}_02_neutralised.png")

        # Stage 5: Metrics.
        metrics = compute_output_metrics(neutralised)

        # Stage 6: Rendering + export.
        rendered = render_preview(neutralised, render_params)
        render_path = str(Path(output_dir) / f"{stem}_03_rendered.png")
        _save_rgb(rendered, output_dir, f"{stem}_03_rendered.png")

        # Evaluator output (simplified stage-aware scores).
        evaluator = _build_evaluator_output(inv_diag, neut_diag, metrics)

        return {
            "source": Path(frame_path).name,
            "status": "ok",
            "inversion": inv_diag,
            "neutralisation": neut_diag,
            "metrics": metrics,
            "evaluator": evaluator,
            "output_paths": {
                "technical_positive": str(Path(output_dir) / f"{stem}_01_technical_positive.png"),
                "neutralised":        str(Path(output_dir) / f"{stem}_02_neutralised.png"),
                "rendered":           render_path,
            },
        }

    except Exception:
        tb = traceback.format_exc()
        log.error("Frame %s failed:\n%s", stem, tb)
        return {"source": Path(frame_path).name, "status": "error", "error": tb}


# ──────────────────────────────────────────────────────────────────────────────
# Evaluator (heuristic, no agent)
# ──────────────────────────────────────────────────────────────────────────────

def _build_evaluator_output(
    inv_diag: dict[str, Any],
    neut_diag: dict[str, Any],
    metrics: dict[str, float],
) -> dict[str, Any]:
    """Build a heuristic stage-aware evaluator output dict."""
    spread_after = neut_diag.get("channel_spread_after", inv_diag.get("channel_spread", 1.0))
    clipping = float(inv_diag.get("clipping_ratio", 0.0))
    gray_mean = float(metrics.get("gray_mean", 0.5))
    saturation = float(metrics.get("mean_saturation", 0.0))
    sharpness = float(metrics.get("laplacian_variance", 0.0))

    # Stage scores.
    inv_score = float(np.clip(
        (1.0 - min(spread_after * 5.0, 1.0)) * 0.5 +
        (1.0 - min(clipping * 4.0, 1.0)) * 0.5,
        0.0, 1.0,
    ))
    exposure_ok = 0.25 <= gray_mean <= 0.75
    exposure_score = 1.0 if exposure_ok else float(max(0.0, 1.0 - abs(gray_mean - 0.5) * 3.0))
    sharp_score = float(np.clip((sharpness - 50) / 300, 0.0, 1.0))
    render_score = float(np.clip(exposure_score * 0.5 + sharp_score * 0.5, 0.0, 1.0))

    overall = float(inv_score * 0.6 + render_score * 0.4)

    # Cast detection.
    cast_type: str | None = None
    br_delta = float(metrics.get("blue_red_delta", 0.0))
    gm_delta = float(metrics.get("green_magenta_delta", 0.0))
    if abs(br_delta) > 0.05:
        cast_type = "blue" if br_delta > 0 else "red"
    elif abs(gm_delta) > 0.05:
        cast_type = "green" if gm_delta > 0 else "magenta"

    return {
        "overall_score": round(overall, 4),
        "stage_scores": {
            "technical_inversion": round(inv_score, 4),
            "rendering": round(render_score, 4),
        },
        "cast_type": cast_type,
        "confidence": round(float(np.clip(1.0 - clipping * 2.0, 0.0, 1.0)), 4),
        "notes": [
            f"channel_spread={spread_after:.4f}",
            f"clipping={clipping:.3f}",
            f"saturation={saturation:.3f}",
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ──────────────────────────────────────────────────────────────────────────────

def _save_rgb(img_rgb: np.ndarray, output_dir: str, name: str) -> str:
    """Save a float32 RGB [0,1] image as uint8 PNG (BGR for cv2)."""
    path = str(Path(output_dir) / name)
    bgr = cv2.cvtColor(
        np.clip(img_rgb * 255.0, 0, 255).astype(np.uint8),
        cv2.COLOR_RGB2BGR,
    )
    cv2.imwrite(path, bgr)
    log.debug("Saved %s", path)
    return path
