"""
Estimate stitch overlap from captured preview tiles.

This is a calibration helper, not part of the scan loop. It compares adjacent
JPG tiles and estimates how many pixels overlap horizontally and vertically.
Use a textured target or real film content; a blank backlight frame is not
reliable enough for image-based overlap calibration.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from PIL import Image

try:
    from .config import CAPTURES_DIR
except ImportError:
    from config import CAPTURES_DIR


_TILE_RE = re.compile(r"row_(\d+)_col_(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate scanner tile overlap from preview images.")
    parser.add_argument("--captures-dir", type=Path, default=CAPTURES_DIR)
    parser.add_argument("--max-overlap-frac", type=float, default=0.40)
    parser.add_argument("--step", type=int, default=32)
    parser.add_argument("--min-score", type=float, default=0.18)
    parser.add_argument("--min-support", type=int, default=3)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args()

    result = calibrate_overlap(
        captures_dir=args.captures_dir,
        max_overlap_frac=args.max_overlap_frac,
        step=args.step,
        min_score=args.min_score,
        min_support=args.min_support,
    )
    text = json.dumps(result, indent=2)
    print(text)
    if args.output_json:
        args.output_json.write_text(text + "\n", encoding="utf-8")


def calibrate_overlap(
    captures_dir: Path = CAPTURES_DIR,
    max_overlap_frac: float = 0.40,
    step: int = 32,
    min_score: float = 0.18,
    min_support: int = 3,
) -> dict[str, Any]:
    tiles = _discover_preview_tiles(captures_dir)
    if not tiles:
        raise RuntimeError(f"No preview tiles found in {captures_dir}")

    images = {key: _load_luma(path) for key, path in tiles.items()}
    tile_h, tile_w = next(iter(images.values())).shape

    horizontal = []
    vertical = []
    for (row, col), image in sorted(images.items()):
        right = images.get((row, col + 1))
        if right is not None:
            horizontal.append(_estimate_pair_overlap(image, right, axis="x", max_frac=max_overlap_frac, step=step))
        below = images.get((row + 1, col))
        if below is not None:
            vertical.append(_estimate_pair_overlap(image, below, axis="y", max_frac=max_overlap_frac, step=step))

    summary_x = _summarize_estimates(horizontal, min_score, min_support, step)
    summary_y = _summarize_estimates(vertical, min_score, min_support, step)
    overlap_x = summary_x["overlap_px"]
    overlap_y = summary_y["overlap_px"]
    confidence_x = summary_x["confidence"]
    confidence_y = summary_y["confidence"]
    stride_x = tile_w - overlap_x if overlap_x is not None else None
    stride_y = tile_h - overlap_y if overlap_y is not None else None

    return {
        "captures_dir": str(captures_dir),
        "tile_size": {"width": int(tile_w), "height": int(tile_h)},
        "overlap_x_px": overlap_x,
        "overlap_y_px": overlap_y,
        "stride_x_px": stride_x,
        "stride_y_px": stride_y,
        "confidence": {"x": confidence_x, "y": confidence_y},
        "consensus": {
            "x": summary_x,
            "y": summary_y,
        },
        "samples": {
            "horizontal": horizontal,
            "vertical": vertical,
        },
        "recommended_config": {
            "STITCH_OVERLAP_X_PX": overlap_x,
            "STITCH_OVERLAP_Y_PX": overlap_y,
            "STITCH_TILE_STRIDE_X_PX": stride_x,
            "STITCH_TILE_STRIDE_Y_PX": stride_y,
        },
        "note": (
            "Accept the recommendation only when confidence is not 'low'. "
            "Use a textured calibration target or real film content; blank "
            "backlight captures usually cannot calibrate overlap."
        ),
    }


def _discover_preview_tiles(captures_dir: Path) -> dict[tuple[int, int], Path]:
    tiles: dict[tuple[int, int], Path] = {}
    for path in captures_dir.iterdir() if captures_dir.exists() else []:
        if not path.is_file():
            continue
        match = _TILE_RE.match(path.name)
        if not match:
            continue
        tiles[(int(match.group(1)), int(match.group(2)))] = path
    return tiles


def _load_luma(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    return _highpass(luma)


def _highpass(image: np.ndarray) -> np.ndarray:
    image = image.astype(np.float32)
    small = image[::16, ::16]
    baseline = float(np.median(small))
    centered = image - baseline
    gy, gx = np.gradient(centered)
    mag = np.sqrt(gx * gx + gy * gy)
    scale = float(np.percentile(mag, 95))
    if scale <= 1e-6:
        return mag
    return np.clip(mag / scale, 0.0, 4.0)


def _estimate_pair_overlap(
    first: np.ndarray,
    second: np.ndarray,
    axis: str,
    max_frac: float,
    step: int,
) -> dict[str, Any]:
    h, w = first.shape
    max_overlap = int((w if axis == "x" else h) * max_frac)
    best_overlap = 0
    best_score = -1.0
    scores = []
    for overlap in range(max(step, 8), max_overlap + 1, max(step, 1)):
        if axis == "x":
            a = first[:, w - overlap : w]
            b = second[:, :overlap]
        else:
            a = first[h - overlap : h, :]
            b = second[:overlap, :]
        score = _normalized_corr(a, b)
        scores.append((overlap, score))
        if score > best_score:
            best_score = score
            best_overlap = overlap

    refined_overlap = best_overlap
    if best_overlap:
        lo = max(8, best_overlap - step)
        hi = min(max_overlap, best_overlap + step)
        for overlap in range(lo, hi + 1, max(1, step // 8)):
            if axis == "x":
                a = first[:, w - overlap : w]
                b = second[:, :overlap]
            else:
                a = first[h - overlap : h, :]
                b = second[:overlap, :]
            score = _normalized_corr(a, b)
            if score > best_score:
                best_score = score
                refined_overlap = overlap

    return {
        "overlap_px": int(refined_overlap),
        "score": float(best_score),
        "axis": axis,
    }


def _normalized_corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    a = a - float(np.mean(a))
    b = b - float(np.mean(b))
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom <= 1e-6:
        return 0.0
    return float(np.sum(a * b) / denom)


def _summarize_estimates(
    samples: list[dict[str, Any]],
    min_score: float,
    min_support: int,
    step: int,
) -> dict[str, Any]:
    good = [sample for sample in samples if float(sample["score"]) >= min_score]
    if not good:
        return {
            "overlap_px": None,
            "confidence": "low",
            "support": 0,
            "sample_count": len(samples),
            "median_score": None,
            "spread_px": None,
            "reason": "no adjacent pair passed the correlation score threshold",
        }

    values = [int(sample["overlap_px"]) for sample in good]
    scores = [float(sample["score"]) for sample in good]
    rough_med = int(round(median(values)))
    cluster_radius = max(64, step * 3)
    cluster = [
        sample
        for sample in good
        if abs(int(sample["overlap_px"]) - rough_med) <= cluster_radius
    ]
    support = len(cluster)
    sample_count = len(samples)
    if support < max(1, min_support):
        return {
            "overlap_px": None,
            "confidence": "low",
            "support": support,
            "sample_count": sample_count,
            "median_score": float(median(scores)) if scores else None,
            "spread_px": None,
            "reason": (
                "not enough adjacent pairs agreed on the same overlap; "
                "use a target with stronger non-repeating texture across this axis"
            ),
        }

    cluster_values = [int(sample["overlap_px"]) for sample in cluster]
    cluster_scores = [float(sample["score"]) for sample in cluster]
    med = int(round(median(cluster_values)))
    med -= med % 2
    spread = max(cluster_values) - min(cluster_values)
    median_score = float(median(cluster_scores))
    enough_axis_coverage = support >= max(min_support, sample_count // 3)
    tight_cluster = spread <= max(128, step * 4)

    if enough_axis_coverage and tight_cluster and median_score >= min_score * 1.5:
        confidence = "high"
    elif tight_cluster and median_score >= min_score:
        confidence = "medium"
    else:
        return {
            "overlap_px": None,
            "confidence": "low",
            "support": support,
            "sample_count": sample_count,
            "median_score": median_score,
            "spread_px": int(spread),
            "reason": "candidate overlaps were too dispersed to be trusted",
        }

    return {
        "overlap_px": med,
        "confidence": confidence,
        "support": support,
        "sample_count": sample_count,
        "median_score": median_score,
        "spread_px": int(spread),
        "reason": "accepted by score and multi-pair consensus",
    }


if __name__ == "__main__":
    main()
