"""border_detection.py — Automatic film border / clear-base detection.

Combines complementary CV methods to locate the film border, then converts
those generic masks into edge-connected four-side strip estimates that better
match film scanning geometry.

Methods:
  1. projection_based — row/column intensity projections find bright perimeter strips
  2. edge_based        — Canny edges locate the film-gate boundary rectangle
  3. low_variance      — uniform-region mask identifies textureless clear-base pixels

Results are merged into a BorderDetectionResult with a confidence score.

All inputs are float32 RGB H x W x 3 in [0, 1].
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from .models import BorderDetectionResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Fraction of image edge to sample for projection brightness reference.
_PROJ_EDGE_FRAC = 0.05
# Projection brightness threshold relative to per-column maximum.
_PROJ_BRIGHT_FRAC = 0.85

# Canny thresholds for edge-based candidate.
_CANNY_LOW = 30
_CANNY_HIGH = 100
# Minimum fraction of image area the inferred content box must cover.
_EDGE_MIN_CONTENT_FRAC = 0.10

# Local-variance window size and low-variance threshold.
_LV_KERNEL = 15
_LV_THRESHOLD_FRAC = 0.02  # fraction of global variance range

# Minimum number of candidate border pixels (as fraction of total) to trust
# a detection result.
_MIN_BORDER_FRAC = 0.005


def detect_border_regions(frame_rgb: np.ndarray) -> BorderDetectionResult:
    """Run all three border-detection methods and merge into a single result.

    Parameters
    ----------
    frame_rgb:
        float32 H x W x 3 flat-field-corrected frame.

    Returns
    -------
    BorderDetectionResult
    """
    candidates: list[tuple[np.ndarray, float]] = [
        projection_based_border_candidate(frame_rgb),
        edge_based_border_candidate(frame_rgb),
        low_variance_border_candidate(frame_rgb),
    ]
    return merge_border_candidates(candidates)


def projection_based_border_candidate(
    frame_rgb: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Estimate border mask via row / column brightness projections.

    The clear base transmits maximum light and appears brightest in a linear
    negative frame.  This method finds the bright perimeter strips by looking
    for rows/columns whose mean luma exceeds a threshold derived from the
    overall brightness distribution.

    Returns
    -------
    (mask, confidence)
        mask is bool H x W; confidence in [0, 1].
    """
    h, w = frame_rgb.shape[:2]
    # Use luminance channel for projections (perceptual approximation).
    luma = 0.2126 * frame_rgb[:, :, 0] + 0.7152 * frame_rgb[:, :, 1] + 0.0722 * frame_rgb[:, :, 2]

    row_means = luma.mean(axis=1)   # shape (H,)
    col_means = luma.mean(axis=0)   # shape (W,)

    # Threshold: rows/cols brighter than FRAC * global max are candidate border.
    row_thresh = row_means.max() * _PROJ_BRIGHT_FRAC
    col_thresh = col_means.max() * _PROJ_BRIGHT_FRAC

    bright_rows = row_means >= row_thresh  # shape (H,)
    bright_cols = col_means >= col_thresh  # shape (W,)

    # Build a mask: a pixel is a border candidate if its row OR column is bright.
    mask_rows = np.broadcast_to(bright_rows[:, np.newaxis], (h, w))
    mask_cols = np.broadcast_to(bright_cols[np.newaxis, :], (h, w))
    mask = mask_rows | mask_cols

    # Bias toward the image perimeter: keep only pixels in the outer ring.
    perimeter = np.zeros((h, w), dtype=bool)
    edge_r = max(1, int(h * _PROJ_EDGE_FRAC * 2))
    edge_c = max(1, int(w * _PROJ_EDGE_FRAC * 2))
    perimeter[:edge_r, :] = True
    perimeter[-edge_r:, :] = True
    perimeter[:, :edge_c] = True
    perimeter[:, -edge_c:] = True

    # Intersection with perimeter to reduce false positives inside the frame.
    mask = mask & perimeter

    border_frac = mask.mean()
    if border_frac < _MIN_BORDER_FRAC:
        log.debug("projection_based: very small border fraction (%.4f)", border_frac)
        confidence = 0.1
    else:
        # Confidence increases with the number of detected border rows/cols.
        bright_row_frac = bright_rows.mean()
        bright_col_frac = bright_cols.mean()
        confidence = float(np.clip((bright_row_frac + bright_col_frac) / 2.0 * 4.0, 0.0, 1.0))

    return mask.astype(bool), confidence


def edge_based_border_candidate(
    frame_rgb: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Estimate border mask by finding the main content bounding rectangle.

    Converts to grayscale, applies Canny, then dilates edges and finds the
    tightest bounding box that encloses the densest edge cluster.  The region
    outside this box is the candidate border.

    Returns
    -------
    (mask, confidence)
        mask is bool H x W; confidence in [0, 1].
    """
    h, w = frame_rgb.shape[:2]

    # Convert float32 RGB → uint8 grayscale for Canny.
    gray_f = 0.2126 * frame_rgb[:, :, 0] + 0.7152 * frame_rgb[:, :, 1] + 0.0722 * frame_rgb[:, :, 2]
    gray_u8 = np.clip(gray_f * 255.0, 0, 255).astype(np.uint8)

    # Mild blur before edge detection to suppress grain noise.
    blurred = cv2.GaussianBlur(gray_u8, (5, 5), sigmaX=1.5)
    edges = cv2.Canny(blurred, _CANNY_LOW, _CANNY_HIGH)

    # Dilate edges slightly so nearby edges merge into contiguous blobs.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilated = cv2.dilate(edges, kernel, iterations=2)

    # Find the bounding box of the largest contiguous edge blob → content area.
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mask = np.zeros((h, w), dtype=bool)
    confidence = 0.2

    if not contours:
        # No contours found — fall back to a thin perimeter strip.
        edge_r = max(1, h // 20)
        edge_c = max(1, w // 20)
        mask[:edge_r, :] = True
        mask[-edge_r:, :] = True
        mask[:, :edge_c] = True
        mask[:, -edge_c:] = True
        return mask, 0.1

    # Use the largest contour's bounding rect as the content region.
    largest = max(contours, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(largest)

    content_frac = (bw * bh) / (h * w)
    if content_frac < _EDGE_MIN_CONTENT_FRAC:
        log.debug("edge_based: content box too small (%.4f of image)", content_frac)
        confidence = 0.15
    else:
        # Content box quality: ideally not touching the frame edges.
        margin_top = by / h
        margin_left = bx / w
        margin_bottom = (h - by - bh) / h
        margin_right = (w - bx - bw) / w
        symmetry = 1.0 - abs(margin_left - margin_right) - abs(margin_top - margin_bottom)
        confidence = float(np.clip(symmetry * content_frac * 2.0, 0.1, 0.9))

    # Border mask = everything outside the content bounding box.
    full = np.ones((h, w), dtype=bool)
    full[by: by + bh, bx: bx + bw] = False
    mask = full

    return mask, confidence


def low_variance_border_candidate(
    frame_rgb: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Identify clear-base pixels by their low local variance.

    Film clear base is photochemically uniform and will show very low texture /
    variance in a well-captured scan.  Scene content has higher local variance.

    Returns
    -------
    (mask, confidence)
        mask is bool H x W; confidence in [0, 1].
    """
    h, w = frame_rgb.shape[:2]

    gray_f = 0.2126 * frame_rgb[:, :, 0] + 0.7152 * frame_rgb[:, :, 1] + 0.0722 * frame_rgb[:, :, 2]
    gray_f32 = gray_f.astype(np.float32)

    ksize = _LV_KERNEL | 1  # must be odd

    # Local variance = E[X²] - E[X]²
    mean_sq = cv2.boxFilter(gray_f32 ** 2, -1, (ksize, ksize), normalize=True)
    sq_mean = cv2.boxFilter(gray_f32, -1, (ksize, ksize), normalize=True) ** 2
    local_var = np.maximum(mean_sq - sq_mean, 0.0)

    # Threshold at a fraction of the global variance range.
    var_max = float(local_var.max())
    threshold = var_max * _LV_THRESHOLD_FRAC
    threshold = max(threshold, 1e-8)

    mask = local_var <= threshold

    border_frac = mask.mean()
    if border_frac < _MIN_BORDER_FRAC:
        log.debug("low_variance: very small candidate fraction (%.4f)", border_frac)
        confidence = 0.1
    else:
        # Confidence: low-variance pixels should be near perimeter, not interior.
        # Create a distance-from-edge map and check where low-var pixels live.
        dist_edge = _dist_from_edge(h, w)
        perimeter_bias = float(
            dist_edge[mask].mean() / (min(h, w) / 2.0 + 1e-6)
        )
        # Low distance from edge = more likely true border → invert.
        confidence = float(np.clip(1.0 - perimeter_bias * 2.0, 0.1, 0.9))

    return mask.astype(bool), confidence


def merge_border_candidates(
    candidates: list[tuple[np.ndarray, float]],
) -> BorderDetectionResult:
    """Merge multiple border-candidate (mask, confidence) pairs.

    Weighted voting is followed by a film-specific refinement step that keeps
    only edge-connected border strips along the four sides of the scan.
    """
    if not candidates:
        raise ValueError("No candidates provided to merge_border_candidates.")

    shape = candidates[0][0].shape
    vote_map = np.zeros(shape, dtype=np.float32)
    total_weight = 0.0

    method_names = ["projection", "edge", "low_variance"]
    method_votes: dict[str, float] = {}

    for i, (mask, conf) in enumerate(candidates):
        name = method_names[i] if i < len(method_names) else f"method_{i}"
        method_votes[name] = float(conf)
        vote_map += mask.astype(np.float32) * conf
        total_weight += conf

    if total_weight < 1e-8:
        total_weight = 1.0

    vote_frac = vote_map / total_weight

    coarse_border_mask = vote_frac >= 0.33
    coarse_clearbase_mask = vote_frac >= 0.66

    border_mask = _refine_to_edge_connected_strips(coarse_border_mask)
    clearbase_candidate_mask = _refine_to_edge_connected_strips(coarse_clearbase_mask)

    if clearbase_candidate_mask.mean() < _MIN_BORDER_FRAC:
        clearbase_candidate_mask = border_mask.copy()

    content_mask = ~border_mask
    frame_bbox = _bbox_from_content(content_mask)

    if border_mask.any():
        agreement = float(vote_frac[border_mask].mean())
    else:
        agreement = 0.0

    h, w = shape
    dist_edge = _dist_from_edge(h, w)
    if border_mask.any():
        mean_dist = float(dist_edge[border_mask].mean())
        coherence = float(np.clip(1.0 - mean_dist / (min(h, w) / 4.0), 0.0, 1.0))
    else:
        coherence = 0.0

    side_coverage = _side_coverage_score(border_mask)
    confidence = float(np.clip(agreement * 0.4 + coherence * 0.4 + side_coverage * 0.2, 0.0, 1.0))

    log.info(
        "Border detection: confidence=%.3f, border_frac=%.3f, side_coverage=%.3f, votes=%s",
        confidence, border_mask.mean(), side_coverage, method_votes,
    )

    return BorderDetectionResult(
        frame_bbox=frame_bbox,
        border_mask=border_mask,
        content_mask=content_mask,
        clearbase_candidate_mask=clearbase_candidate_mask,
        method_votes=method_votes,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dist_from_edge(h: int, w: int) -> np.ndarray:
    """Return float32 H x W array of minimum pixel distance from image border."""
    row_idx = np.arange(h, dtype=np.float32)
    col_idx = np.arange(w, dtype=np.float32)
    dist_row = np.minimum(row_idx, h - 1 - row_idx)
    dist_col = np.minimum(col_idx, w - 1 - col_idx)
    return np.minimum(dist_row[:, np.newaxis], dist_col[np.newaxis, :])


def _bbox_from_content(content_mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return (x0, y0, x1, y1) bounding box of content_mask, or None."""
    rows = np.any(content_mask, axis=1)
    cols = np.any(content_mask, axis=0)
    if not rows.any() or not cols.any():
        return None
    y0, y1 = int(np.argmax(rows)), int(len(rows) - 1 - np.argmax(rows[::-1]))
    x0, x1 = int(np.argmax(cols)), int(len(cols) - 1 - np.argmax(cols[::-1]))
    return (x0, y0, x1, y1)


def _refine_to_edge_connected_strips(mask: np.ndarray) -> np.ndarray:
    """Keep only edge-connected border candidates and convert them to side strips."""
    if mask.dtype != np.uint8:
        mask_u8 = mask.astype(np.uint8)
    else:
        mask_u8 = mask.copy()

    h, w = mask_u8.shape
    if h == 0 or w == 0:
        return mask.astype(bool)

    num_labels, labels = cv2.connectedComponents(mask_u8)
    edge_component_mask = np.zeros_like(mask_u8, dtype=bool)

    for label in range(1, num_labels):
        comp = labels == label
        if _touches_edge(comp):
            edge_component_mask |= comp

    if edge_component_mask.mean() < _MIN_BORDER_FRAC:
        edge_component_mask = mask.astype(bool)

    return _mask_to_side_strips(edge_component_mask)


def _touches_edge(mask: np.ndarray) -> bool:
    return bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


def _mask_to_side_strips(mask: np.ndarray) -> np.ndarray:
    """Convert scattered edge-connected border candidates into four side strips."""
    h, w = mask.shape
    refined = np.zeros_like(mask, dtype=bool)

    top_depth = _side_depth(mask, side="top")
    bottom_depth = _side_depth(mask, side="bottom")
    left_depth = _side_depth(mask, side="left")
    right_depth = _side_depth(mask, side="right")

    if top_depth > 0:
        refined[:top_depth, :] = True
    if bottom_depth > 0:
        refined[h - bottom_depth:, :] = True
    if left_depth > 0:
        refined[:, :left_depth] = True
    if right_depth > 0:
        refined[:, w - right_depth:] = True

    if refined.mean() < _MIN_BORDER_FRAC:
        return mask.astype(bool)
    return refined


def _side_depth(mask: np.ndarray, side: str) -> int:
    """Estimate strip depth for a given side using connected coverage."""
    h, w = mask.shape
    if side in {"top", "bottom"}:
        max_depth = max(1, int(h * 0.20))
        best_depth = 0
        for depth in range(1, max_depth + 1):
            strip = mask[:depth, :] if side == "top" else mask[h - depth:, :]
            coverage = float(strip.mean())
            if coverage >= 0.60:
                best_depth = depth
            else:
                break
        return best_depth

    max_depth = max(1, int(w * 0.20))
    best_depth = 0
    for depth in range(1, max_depth + 1):
        strip = mask[:, :depth] if side == "left" else mask[:, w - depth:]
        coverage = float(strip.mean())
        if coverage >= 0.60:
            best_depth = depth
        else:
            break
    return best_depth


def _side_coverage_score(mask: np.ndarray) -> float:
    """Score how well the refined border occupies coherent side strips."""
    h, w = mask.shape
    depths = [
        _side_depth(mask, "top") / max(h * 0.05, 1.0),
        _side_depth(mask, "bottom") / max(h * 0.05, 1.0),
        _side_depth(mask, "left") / max(w * 0.05, 1.0),
        _side_depth(mask, "right") / max(w * 0.05, 1.0),
    ]
    return float(np.clip(np.mean(depths), 0.0, 1.0))
