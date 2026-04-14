"""film_characterization.py — Film base and density-endpoint estimation.

Estimates the film's base colour (orange mask) and density references needed
for technically grounded inversion.

Two calibration modes:
  - 'border_plus_scene_estimate' — default; uses clear-base border pixels for
    D-min and scene dark pixels for a conservative D-max proxy.
  - 'border_plus_leader' — uses a dedicated leader frame for stable endpoints.

All arrays are float32 RGB H x W x 3 or shape (3,) for single-pixel estimates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .models import FlatFieldModel

from .models import BorderDetectionResult, FilmCharacterization

log = logging.getLogger(__name__)

# Minimum number of border pixels to trust the estimate.
_MIN_BORDER_PIXELS = 64
# Percentile for selecting stable bright border pixels (D-min proxy).
_BASE_BRIGHT_PERCENTILE = 90.0
# Percentile for selecting dark scene pixels (upper-density proxy).
_UPPER_DENSITY_DARK_PERCENTILE = 99.0
# Epsilon for log-transform to avoid log(0).
_LOG_EPS = 1e-6


def estimate_base_from_border(
    frame_rgb: np.ndarray,
    border: BorderDetectionResult,
) -> tuple[np.ndarray, int, float, float, dict[str, list[float]], list[str]]:
    """Estimate film base / orange-mask RGB from stable side-wise clear-base clusters."""
    notes: list[str] = []

    use_mask = border.clearbase_candidate_mask
    n_pixels = int(use_mask.sum())
    if n_pixels < _MIN_BORDER_PIXELS:
        use_mask = border.border_mask
        n_pixels = int(use_mask.sum())
        notes.append("Fell back from clearbase candidates to broader border mask.")

    if n_pixels < _MIN_BORDER_PIXELS:
        log.warning(
            "estimate_base_from_border: too few border pixels (%d); falling back to global brightest pixels.",
            n_pixels,
        )
        flat = frame_rgb.reshape(-1, 3)
        luma = flat.mean(axis=1)
        threshold = np.percentile(luma, 99.0)
        bright_pixels = flat[luma >= threshold]
        if bright_pixels.shape[0] == 0:
            bright_pixels = flat
        base_rgb = np.percentile(bright_pixels, _BASE_BRIGHT_PERCENTILE, axis=0)
        base_rel_spread = float((base_rgb.max() - base_rgb.min()) / (base_rgb.mean() + _LOG_EPS))
        notes.append("Global bright-pixel fallback used for base estimate.")
        return (
            np.clip(base_rgb, _LOG_EPS, 1.0).astype(np.float32),
            int(bright_pixels.shape[0]),
            base_rel_spread,
            0.0,
            {},
            notes,
        )

    side_masks = _split_border_by_side(use_mask)
    side_estimates: dict[str, np.ndarray] = {}
    side_counts: dict[str, int] = {}

    for side, side_mask in side_masks.items():
        if int(side_mask.sum()) < _MIN_BORDER_PIXELS:
            continue
        side_pixels = _select_stable_base_pixels(frame_rgb[side_mask].astype(np.float32), notes=None)
        if side_pixels.shape[0] < _MIN_BORDER_PIXELS:
            continue
        side_estimates[side] = np.median(side_pixels, axis=0).astype(np.float32)
        side_counts[side] = int(side_pixels.shape[0])

    side_base_rgbs = {k: [float(v) for v in rgb] for k, rgb in side_estimates.items()}

    if len(side_estimates) >= 2:
        side_stack = np.stack(list(side_estimates.values()), axis=0)
        side_median = np.median(side_stack, axis=0)
        rel_err = np.linalg.norm(side_stack - side_median[None, :], axis=1) / (np.linalg.norm(side_median) + _LOG_EPS)
        keep = rel_err <= max(np.percentile(rel_err, 60.0), 0.08)
        kept_side_names = [name for name, flag in zip(side_estimates.keys(), keep) if flag]
        if kept_side_names:
            fused = np.stack([side_estimates[name] for name in kept_side_names], axis=0)
            base_rgb = np.median(fused, axis=0).astype(np.float32)
            side_consistency_score = float(np.clip(1.0 - rel_err[keep].mean() * 4.0, 0.0, 1.0))
            sample_count = int(sum(side_counts[name] for name in kept_side_names))
            notes.append(f"Fused side-wise base estimates from: {', '.join(kept_side_names)}.")
            if len(kept_side_names) < len(side_estimates):
                notes.append("Dropped inconsistent side estimates during base fusion.")
        else:
            base_rgb, sample_count, side_consistency_score = _estimate_base_from_all_candidates(frame_rgb, use_mask, notes)
            side_consistency_score = 0.0
    else:
        if len(side_estimates) == 1:
            only_side = next(iter(side_estimates.keys()))
            base_rgb = side_estimates[only_side]
            sample_count = side_counts[only_side]
            side_consistency_score = 0.35
            notes.append(f"Used single-side base estimate from {only_side}; side consistency is limited.")
        else:
            base_rgb, sample_count, side_consistency_score = _estimate_base_from_all_candidates(frame_rgb, use_mask, notes)

    base_rel_spread = float((base_rgb.max() - base_rgb.min()) / (base_rgb.mean() + _LOG_EPS))
    return np.clip(base_rgb, _LOG_EPS, 1.0).astype(np.float32), sample_count, base_rel_spread, side_consistency_score, side_base_rgbs, notes


def estimate_scene_upper_density(
    frame_rgb: np.ndarray,
    base_rgb: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Conservative upper-density estimate from the darkest scene pixels.

    In a negative, the darkest pixels correspond to the highest exposure and
    highest dye density.  We use a very high percentile of the per-channel
    density distribution to get a conservative (not extreme) D-max proxy.

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 flat-field-corrected frame (before inversion).
    base_rgb:
        shape (3,) float32 base estimate.

    Returns
    -------
    (upper_density_rgb, confidence)
        upper_density_rgb: shape (3,) float32; upper-density reference.
        confidence: float in [0, 1]; how reliably this was estimated.
    """
    base_safe = np.clip(base_rgb, _LOG_EPS, 1.0)
    frame_safe = np.clip(frame_rgb, _LOG_EPS, 1.0).astype(np.float32)

    # Transmission relative to base (values < 1 → below base density)
    transmission = frame_safe / base_safe[np.newaxis, np.newaxis, :]
    transmission = np.clip(transmission, _LOG_EPS, 1.0)

    # Density in excess of base
    density = -np.log(transmission)  # H x W x 3, non-negative

    flat_density = density.reshape(-1, 3)  # (N, 3)

    # Take a conservative high-percentile to avoid outlier dark pixels.
    upper = np.percentile(flat_density, _UPPER_DENSITY_DARK_PERCENTILE, axis=0)
    upper = np.clip(upper, 0.05, None).astype(np.float32)  # Minimum sensible density

    # Confidence heuristic: how much the upper reference exceeds the lower.
    spread = float(upper.mean())
    confidence = float(np.clip(spread / 2.0, 0.0, 1.0))

    log.debug(
        "Scene upper density: R=%.3f G=%.3f B=%.3f  confidence=%.3f",
        *upper, confidence,
    )

    return upper, confidence


def characterize_film(
    frame_rgb: np.ndarray,
    border: BorderDetectionResult,
    leader_rgb: np.ndarray | None = None,
    flat_model: "FlatFieldModel | None" = None,
) -> FilmCharacterization:
    """Full film characterisation pipeline for a single frame.

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 flat-field-corrected frame.
    border:
        BorderDetectionResult from detect_border_regions.
    leader_rgb:
        Optional float32 H x W x 3 leader / calibration frame.
        If provided, D-min / D-max are derived from it.
    flat_model:
        Optional FlatFieldModel (used only for notes / metadata).

    Returns
    -------
    FilmCharacterization
    """
    notes: list[str] = []

    # --- Base RGB from border ------------------------------------------------
    (
        base_rgb,
        base_sample_count,
        base_rel_spread,
        side_consistency_score,
        side_base_rgbs,
        base_notes,
    ) = estimate_base_from_border(frame_rgb, border)
    notes.extend(base_notes)

    log.info(
        "Film base: R=%.4f G=%.4f B=%.4f  (border_confidence=%.3f, samples=%d, rel_spread=%.4f, side_consistency=%.3f)",
        *base_rgb, border.confidence, base_sample_count, base_rel_spread, side_consistency_score,
    )

    if border.confidence < 0.3:
        notes.append(
            f"Low border confidence ({border.confidence:.2f}); base estimate may be unreliable."
        )
    if base_rel_spread > 0.20:
        notes.append(
            f"Base estimate has high inter-channel spread ({base_rel_spread:.3f}); cast risk is elevated."
        )
    if side_consistency_score < 0.5:
        notes.append(
            f"Side-wise base consistency is low ({side_consistency_score:.2f}); border contamination risk is elevated."
        )

    # --- Leader-based calibration (enhanced mode) ----------------------------
    dmin_density_rgb: np.ndarray | None = None
    dmax_density_rgb: np.ndarray | None = None
    upper_density_rgb: np.ndarray | None = None
    upper_ref_confidence = 0.0
    calibration_mode = "border_plus_scene_estimate"

    if leader_rgb is not None:
        calibration_mode = "border_plus_leader"
        dmin_density_rgb, dmax_density_rgb, leader_conf = _estimate_from_leader(leader_rgb, base_rgb)
        upper_density_rgb = dmax_density_rgb
        upper_ref_confidence = leader_conf
        notes.append("Relative density references derived from leader frame.")
        log.info(
            "Leader calibration densities: dmin=(%.4f %.4f %.4f) dmax=(%.4f %.4f %.4f)",
            *dmin_density_rgb, *dmax_density_rgb,
        )
    else:
        # Fallback: estimate upper density conservatively from scene content.
        upper_density_rgb, upper_ref_confidence = estimate_scene_upper_density(
            frame_rgb, base_rgb
        )
        notes.append(
            "No leader frame; upper-density reference estimated from scene content "
            f"(confidence={upper_ref_confidence:.2f})."
        )
        if upper_ref_confidence < 0.3:
            notes.append(
                "Upper-density estimate is low-confidence; consider capturing a leader frame."
            )

    if flat_model is not None:
        r, g, b = flat_model.channel_means
        notes.append(f"Flat channel means: R={r:.4f} G={g:.4f} B={b:.4f}")

    return FilmCharacterization(
        base_rgb=base_rgb,
        base_sample_count=base_sample_count,
        base_rel_spread=base_rel_spread,
        side_consistency_score=side_consistency_score,
        side_base_rgbs=side_base_rgbs,
        upper_density_rgb=upper_density_rgb,
        dmin_density_rgb=dmin_density_rgb,
        dmax_density_rgb=dmax_density_rgb,
        border_confidence=border.confidence,
        upper_ref_confidence=upper_ref_confidence,
        calibration_mode=calibration_mode,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _select_stable_base_pixels(border_pixels: np.ndarray, notes: list[str] | None = None) -> np.ndarray:
    """Apply brightness and channel-spread filtering to candidate base pixels."""
    if border_pixels.shape[0] == 0:
        return border_pixels

    pixels = border_pixels.astype(np.float32)
    luma = pixels.mean(axis=1)
    chroma_spread = pixels.max(axis=1) - pixels.min(axis=1)

    bright_threshold = np.percentile(luma, 75.0)
    bright_keep = luma >= bright_threshold
    if bright_keep.sum() >= _MIN_BORDER_PIXELS:
        pixels = pixels[bright_keep]
        luma = luma[bright_keep]
        chroma_spread = chroma_spread[bright_keep]
        if notes is not None:
            notes.append("Restricted base estimation to brighter clear-base candidates.")

    spread_threshold = np.percentile(chroma_spread, 50.0)
    stable_keep = chroma_spread <= spread_threshold
    if stable_keep.sum() >= _MIN_BORDER_PIXELS:
        pixels = pixels[stable_keep]
        luma = luma[stable_keep]
        if notes is not None:
            notes.append("Rejected high channel-spread border pixels before base estimation.")

    cluster_q_low = np.percentile(luma, 60.0)
    cluster_q_high = np.percentile(luma, 95.0)
    cluster_keep = (luma >= cluster_q_low) & (luma <= cluster_q_high)
    if cluster_keep.sum() >= _MIN_BORDER_PIXELS:
        pixels = pixels[cluster_keep]
        if notes is not None:
            notes.append("Selected stable bright cluster for final base estimate.")

    return pixels


def _estimate_base_from_all_candidates(
    frame_rgb: np.ndarray,
    use_mask: np.ndarray,
    notes: list[str],
) -> tuple[np.ndarray, int, float]:
    pixels = _select_stable_base_pixels(frame_rgb[use_mask].astype(np.float32), notes=notes)
    if pixels.shape[0] == 0:
        pixels = frame_rgb[use_mask].astype(np.float32)
    base_rgb = np.median(pixels, axis=0).astype(np.float32)
    return base_rgb, int(pixels.shape[0]), 0.25


def _split_border_by_side(mask: np.ndarray) -> dict[str, np.ndarray]:
    """Split a border mask into top/bottom/left/right edge-connected side masks."""
    h, w = mask.shape
    side_depth_h = max(1, int(h * 0.20))
    side_depth_w = max(1, int(w * 0.20))

    top = np.zeros_like(mask, dtype=bool)
    bottom = np.zeros_like(mask, dtype=bool)
    left = np.zeros_like(mask, dtype=bool)
    right = np.zeros_like(mask, dtype=bool)

    top[:side_depth_h, :] = mask[:side_depth_h, :]
    bottom[h - side_depth_h:, :] = mask[h - side_depth_h:, :]
    left[:, :side_depth_w] = mask[:, :side_depth_w]
    right[:, w - side_depth_w:] = mask[:, w - side_depth_w:]

    return {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
    }


def _estimate_from_leader(
    leader_rgb: np.ndarray,
    base_rgb: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Estimate relative density endpoints from a leader / calibration frame.

    The returned references are in the same relative-density space used by
    density_inversion: D_rel = -log(leader / base_rgb).

    Returns
    -------
    (dmin_density_rgb, dmax_density_rgb, confidence)
    """
    base_safe = np.clip(base_rgb, _LOG_EPS, 1.0).astype(np.float32)
    transmission = np.clip(
        leader_rgb.astype(np.float32) / base_safe[np.newaxis, np.newaxis, :],
        _LOG_EPS,
        1.0,
    )
    density = -np.log(transmission)

    flat = density.reshape(-1, 3).astype(np.float32)
    density_luma = flat.mean(axis=1)

    low_thresh = np.percentile(density_luma, 1.0)
    low_pix = flat[density_luma <= low_thresh]
    if low_pix.shape[0] == 0:
        low_pix = flat

    high_thresh = np.percentile(density_luma, 99.0)
    high_pix = flat[density_luma >= high_thresh]
    if high_pix.shape[0] == 0:
        high_pix = flat

    dmin_density_rgb = np.percentile(low_pix, 10.0, axis=0).astype(np.float32)
    dmax_density_rgb = np.percentile(high_pix, 90.0, axis=0).astype(np.float32)

    dmin_density_rgb = np.clip(dmin_density_rgb, 0.0, None)
    dmax_density_rgb = np.clip(dmax_density_rgb, 1e-4, None)

    separation = float((dmax_density_rgb - dmin_density_rgb).mean())
    confidence = float(np.clip(separation / 2.0, 0.0, 1.0))

    return dmin_density_rgb, dmax_density_rgb, confidence
