#!/usr/bin/env python
"""
Demo image pipeline for overlapping negative-film tiles.

Stages:
1) Stitch tiles into one raw image using overlap-aware feather blending.
2) Invert colors (negative -> positive).
3) Restore color/contrast with white balance + tonal enhancement.
4) Build a selective cast-suppression mask and desaturate only where needed.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class TileRecord:
    row: int
    col: int
    filename: str
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class ManifestInfo:
    records: list[TileRecord]
    overlap_x: int | None
    overlap_y: int | None


def parse_manifest(manifest_path: Path) -> ManifestInfo:
    records: list[TileRecord] = []
    overlap_x: int | None = None
    overlap_y: int | None = None

    with manifest_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(
                TileRecord(
                    row=int(row["row"]),
                    col=int(row["col"]),
                    filename=row["filename"],
                    left=int(row["left"]),
                    top=int(row["top"]),
                    right=int(row["right"]),
                    bottom=int(row["bottom"]),
                )
            )
            ox = row.get("overlap_x_px")
            oy = row.get("overlap_y_px")
            if ox:
                overlap_x = int(float(ox))
            if oy:
                overlap_y = int(float(oy))

    if not records:
        raise ValueError(f"No tile records found in manifest: {manifest_path}")

    records.sort(key=lambda r: (r.row, r.col))
    return ManifestInfo(records=records, overlap_x=overlap_x, overlap_y=overlap_y)


def load_metadata_overlap(metadata_path: Path | None) -> tuple[int | None, int | None]:
    if metadata_path is None or not metadata_path.exists():
        return None, None

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    overlap = payload.get("overlap", {})
    overlap_x = overlap.get("x_px")
    overlap_y = overlap.get("y_px")
    if overlap_x is None or overlap_y is None:
        return None, None
    return int(overlap_x), int(overlap_y)


def iter_rows(records: Iterable[TileRecord]) -> int:
    return max(r.row for r in records)


def iter_cols(records: Iterable[TileRecord]) -> int:
    return max(r.col for r in records)


def build_weight_map(
    tile_h: int,
    tile_w: int,
    overlap_x: int,
    overlap_y: int,
    left_edge: bool,
    right_edge: bool,
    top_edge: bool,
    bottom_edge: bool,
) -> np.ndarray:
    eps = 1e-3
    wx = np.ones((tile_w,), dtype=np.float32)
    wy = np.ones((tile_h,), dtype=np.float32)

    if overlap_x > 0:
        if not left_edge:
            wx[:overlap_x] *= np.linspace(eps, 1.0, overlap_x, dtype=np.float32)
        if not right_edge:
            wx[-overlap_x:] *= np.linspace(1.0, eps, overlap_x, dtype=np.float32)

    if overlap_y > 0:
        if not top_edge:
            wy[:overlap_y] *= np.linspace(eps, 1.0, overlap_y, dtype=np.float32)
        if not bottom_edge:
            wy[-overlap_y:] *= np.linspace(1.0, eps, overlap_y, dtype=np.float32)

    return wy[:, None] * wx[None, :]


def stitch_tiles(
    tiles_dir: Path,
    records: list[TileRecord],
    overlap_x: int,
    overlap_y: int,
) -> np.ndarray:
    canvas_w = max(r.right for r in records)
    canvas_h = max(r.bottom for r in records)
    max_row = iter_rows(records)
    max_col = iter_cols(records)

    pixel_acc = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
    weight_acc = np.zeros((canvas_h, canvas_w), dtype=np.float32)

    for rec in records:
        tile_path = tiles_dir / rec.filename
        tile = cv2.imread(str(tile_path), cv2.IMREAD_COLOR)
        if tile is None:
            raise FileNotFoundError(f"Unable to read tile: {tile_path}")

        tile_h, tile_w = tile.shape[:2]
        expected_h = rec.bottom - rec.top
        expected_w = rec.right - rec.left
        if tile_h != expected_h or tile_w != expected_w:
            raise ValueError(
                "Tile size mismatch for "
                f"{tile_path}: got {tile_w}x{tile_h}, expected {expected_w}x{expected_h}"
            )

        weight = build_weight_map(
            tile_h=tile_h,
            tile_w=tile_w,
            overlap_x=overlap_x,
            overlap_y=overlap_y,
            left_edge=rec.col == 1,
            right_edge=rec.col == max_col,
            top_edge=rec.row == 1,
            bottom_edge=rec.row == max_row,
        )

        y0, y1 = rec.top, rec.bottom
        x0, x1 = rec.left, rec.right
        pixel_acc[y0:y1, x0:x1] += tile.astype(np.float32) * weight[..., None]
        weight_acc[y0:y1, x0:x1] += weight

    stitched = np.clip(
        pixel_acc / np.maximum(weight_acc[..., None], 1e-6),
        0,
        255,
    ).astype(np.uint8)
    return stitched


def invert_negative(image_bgr: np.ndarray) -> np.ndarray:
    return 255 - image_bgr


def apply_white_balance(
    image_bgr: np.ndarray,
    clip_percent: float = 0.5,
) -> np.ndarray:
    # Percentile-clipped white balance is more stable than global gray-world
    # on film scans with strong color casts.
    if hasattr(cv2, "xphoto") and hasattr(cv2.xphoto, "createSimpleWB"):
        wb = cv2.xphoto.createSimpleWB()
        wb.setP(float(clip_percent))
        return wb.balanceWhite(image_bgr)

    # Fallback if xphoto is unavailable.
    return stretch_channel_percentiles(
        image_bgr,
        black_point=clip_percent,
        white_point=100.0 - clip_percent,
    )


def stretch_channel_percentiles(
    image_bgr: np.ndarray,
    black_point: float,
    white_point: float,
) -> np.ndarray:
    src = image_bgr.astype(np.float32)
    out = np.empty_like(src)
    for c in range(3):
        ch = src[..., c]
        low, high = np.percentile(ch, [black_point, white_point])
        if high - low < 1.0:
            out[..., c] = ch
            continue
        out[..., c] = (ch - low) * (255.0 / (high - low))
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_luminance_clahe(
    image_bgr: np.ndarray,
    clip_limit: float = 1.4,
    tile_grid: int = 8,
) -> np.ndarray:
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    l_out = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l_out, a, b)), cv2.COLOR_LAB2BGR)


def build_cast_suppression_mask(
    image_bgr: np.ndarray,
    sigma: float,
    sat_threshold: float = 0.22,
    val_threshold: float = 0.55,
) -> np.ndarray:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[..., 1] / 255.0
    val = hsv[..., 2] / 255.0

    sat_term = np.clip((sat - sat_threshold) / max(1.0 - sat_threshold, 1e-6), 0.0, 1.0)
    val_term = np.clip((val - val_threshold) / max(1.0 - val_threshold, 1e-6), 0.0, 1.0)
    mask = sat_term * val_term
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)

    scale = np.percentile(mask, 99.5)
    if scale > 1e-6:
        mask = np.clip(mask / scale, 0.0, 1.0)
    return mask


def apply_selective_desaturation(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    strength: float,
) -> np.ndarray:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    alpha = np.clip(mask * strength, 0.0, 1.0)
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 - alpha), 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def apply_unsharp_mask(
    image_bgr: np.ndarray,
    amount: float = 0.35,
    sigma: float = 1.2,
) -> np.ndarray:
    src = image_bgr.astype(np.float32)
    blur = cv2.GaussianBlur(src, (0, 0), sigmaX=sigma, sigmaY=sigma)
    sharpened = src + amount * (src - blur)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stitch overlapping negative-film tiles and run demo post-processing."
    )
    parser.add_argument(
        "--tiles-dir",
        type=Path,
        default=Path("Image_integration&Post_Processing/Negative_example/norway_split_40_overlap"),
        help="Directory containing tiles + manifest metadata.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest CSV. Defaults to <tiles-dir>/manifest.csv",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=None,
        help="Path to metadata JSON. Defaults to <tiles-dir>/metadata.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("Image_integration&Post_Processing/output_demo"),
        help="Where outputs are written.",
    )
    parser.add_argument(
        "--overlap-x",
        type=int,
        default=None,
        help="Horizontal overlap in pixels. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--overlap-y",
        type=int,
        default=None,
        help="Vertical overlap in pixels. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--desat-strength",
        type=float,
        default=0.20,
        help="Selective cast-suppression strength in [0, 1].",
    )
    parser.add_argument(
        "--desat-sigma",
        type=float,
        default=7.0,
        help="Gaussian sigma for smoothing the cast-suppression mask.",
    )
    parser.add_argument(
        "--black-point",
        type=float,
        default=0.8,
        help="Lower percentile for per-channel auto-level stretch.",
    )
    parser.add_argument(
        "--white-point",
        type=float,
        default=99.2,
        help="Upper percentile for per-channel auto-level stretch.",
    )
    parser.add_argument(
        "--clahe-clip",
        type=float,
        default=1.4,
        help="CLAHE clip limit on luminance channel.",
    )
    parser.add_argument(
        "--unsharp-amount",
        type=float,
        default=0.35,
        help="Unsharp mask amount for detail recovery.",
    )
    parser.add_argument(
        "--wb-clip-percent",
        type=float,
        default=0.5,
        help="Percentile clipping used by SimpleWB (typical 0.2 to 1.5).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tiles_dir = args.tiles_dir
    manifest_path = args.manifest or (tiles_dir / "manifest.csv")
    metadata_path = args.metadata or (tiles_dir / "metadata.json")

    manifest = parse_manifest(manifest_path)
    meta_ox, meta_oy = load_metadata_overlap(metadata_path)

    overlap_x = args.overlap_x
    overlap_y = args.overlap_y

    if overlap_x is None:
        overlap_x = manifest.overlap_x if manifest.overlap_x is not None else meta_ox
    if overlap_y is None:
        overlap_y = manifest.overlap_y if manifest.overlap_y is not None else meta_oy

    if overlap_x is None or overlap_y is None:
        raise ValueError(
            "Could not determine overlap. Provide --overlap-x and --overlap-y explicitly."
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    stitched_raw = stitch_tiles(
        tiles_dir=tiles_dir,
        records=manifest.records,
        overlap_x=overlap_x,
        overlap_y=overlap_y,
    )
    inverted = invert_negative(stitched_raw)

    wb = apply_white_balance(inverted, clip_percent=args.wb_clip_percent)
    leveled = stretch_channel_percentiles(
        wb,
        black_point=args.black_point,
        white_point=args.white_point,
    )
    contrasted = apply_luminance_clahe(leveled, clip_limit=args.clahe_clip)

    desat_mask = build_cast_suppression_mask(contrasted, sigma=args.desat_sigma)
    cast_corrected = apply_selective_desaturation(
        image_bgr=contrasted,
        mask=desat_mask,
        strength=args.desat_strength,
    )
    restored = apply_unsharp_mask(cast_corrected, amount=args.unsharp_amount)

    stitched_path = output_dir / "stitched_raw.png"
    inverted_path = output_dir / "inverted_positive.png"
    wb_path = output_dir / "white_balanced.png"
    contrast_path = output_dir / "contrast_restored.png"
    mask_path = output_dir / "desaturation_mask.png"
    output_path = output_dir / "inverted_desaturated.png"
    restored_alias_path = output_dir / "restored_color.png"

    cv2.imwrite(str(stitched_path), stitched_raw)
    cv2.imwrite(str(inverted_path), inverted)
    cv2.imwrite(str(wb_path), wb)
    cv2.imwrite(str(contrast_path), contrasted)
    cv2.imwrite(str(mask_path), (desat_mask * 255.0).astype(np.uint8))
    cv2.imwrite(str(output_path), restored)
    cv2.imwrite(str(restored_alias_path), restored)

    print(f"Stitched raw saved: {stitched_path}")
    print(f"Inverted image saved: {inverted_path}")
    print(f"White-balanced image saved: {wb_path}")
    print(f"Contrast-restored image saved: {contrast_path}")
    print(f"Desaturation mask saved: {mask_path}")
    print(f"Final processed image saved: {output_path}")
    print(f"Alias saved: {restored_alias_path}")


if __name__ == "__main__":
    main()
