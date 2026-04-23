from __future__ import annotations

from pathlib import Path
import csv
import shutil

import cv2
import numpy as np

from processing.negative_pipeline import (
    apply_luminance_clahe,
    apply_selective_desaturation,
    apply_unsharp_mask,
    apply_white_balance,
    build_cast_suppression_mask,
    invert_negative,
    load_metadata_overlap,
    parse_manifest,
    stitch_tiles,
    stretch_channel_percentiles,
)


class MockScanner:
    def __init__(self):
        repo_root = Path(__file__).resolve().parents[1]
        self.dataset_dir = (
            repo_root
            / "Image_integration&Post_Processing"
            / "Negative_example"
            / "norway_split_40_overlap"
        )
        manifest = parse_manifest(self.dataset_dir / "manifest.csv")
        self.records = sorted(manifest.records, key=lambda r: (r.row, r.col))
        self.overlap_x = manifest.overlap_x
        self.overlap_y = manifest.overlap_y
        self.counter = 0
        self.metadata_copied = False

    def simulate_next_tile(self, output_tiles_dir: Path) -> bool:
        if self.counter >= len(self.records):
            return False
        record = self.records[self.counter]
        output_tiles_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_copied:
            shutil.copy(
                self.dataset_dir / "metadata.json", output_tiles_dir / "metadata.json"
            )
            self.metadata_copied = True
        shutil.copy(
            self.dataset_dir / record.filename, output_tiles_dir / record.filename
        )
        manifest_path = output_tiles_dir / "manifest.csv"
        if not manifest_path.exists():
            with manifest_path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "row",
                        "col",
                        "filename",
                        "left",
                        "top",
                        "right",
                        "bottom",
                        "overlap_x_px",
                        "overlap_y_px",
                    ]
                )
        with manifest_path.open("a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    record.row,
                    record.col,
                    record.filename,
                    record.left,
                    record.top,
                    record.right,
                    record.bottom,
                    self.overlap_x or "",
                    self.overlap_y or "",
                ]
            )
        self.counter += 1
        return True


class Stitcher:
    def __init__(self, tiles_dir: Path | None = None, output_dir: Path | None = None):
        self.tiles_dir = tiles_dir
        repo_root = Path(__file__).resolve().parents[1]
        self.output_dir = output_dir or (
            Path(__file__).resolve().parent / "web" / "generated"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_overlaps(self):
        manifest_path = self.tiles_dir / "manifest.csv"
        metadata_path = self.tiles_dir / "metadata.json"
        manifest = parse_manifest(manifest_path)
        meta_ox, meta_oy = load_metadata_overlap(metadata_path)

        overlap_x = manifest.overlap_x if manifest.overlap_x is not None else meta_ox
        overlap_y = manifest.overlap_y if manifest.overlap_y is not None else meta_oy
        if overlap_x is None or overlap_y is None:
            raise ValueError("Could not determine overlap from manifest/metadata.")
        return manifest, overlap_x, overlap_y

    def process_scan(self, tiles_dir: Path) -> dict:
        self.tiles_dir = tiles_dir
        if not self.tiles_dir.exists():
            raise FileNotFoundError(f"Tiles directory not found: {self.tiles_dir}")

        manifest, overlap_x, overlap_y = self._resolve_overlaps()
        stitched_raw = stitch_tiles(
            tiles_dir=self.tiles_dir,
            records=manifest.records,
            overlap_x=overlap_x,
            overlap_y=overlap_y,
        )
        inverted = invert_negative(stitched_raw)
        wb = apply_white_balance(inverted, clip_percent=0.5)
        leveled = stretch_channel_percentiles(
            wb,
            black_point=0.8,
            white_point=99.2,
        )
        contrasted = apply_luminance_clahe(leveled, clip_limit=1.4)
        desat_mask = build_cast_suppression_mask(contrasted, sigma=7.0)
        cast_corrected = apply_selective_desaturation(
            image_bgr=contrasted,
            mask=desat_mask,
            strength=0.20,
        )
        restored = apply_unsharp_mask(cast_corrected, amount=0.35)

        stitched_path = self.output_dir / "stitched_raw.png"
        inverted_path = self.output_dir / "inverted_positive.png"
        white_balanced_path = self.output_dir / "white_balanced.png"
        leveled_path = self.output_dir / "leveled.png"
        contrasted_path = self.output_dir / "contrasted.png"
        mask_path = self.output_dir / "desaturation_mask.png"
        final_path = self.output_dir / "processed_negative.png"

        cv2.imwrite(str(stitched_path), stitched_raw)
        cv2.imwrite(str(inverted_path), inverted)
        cv2.imwrite(str(white_balanced_path), wb)
        cv2.imwrite(str(leveled_path), leveled)
        cv2.imwrite(str(contrasted_path), contrasted)
        cv2.imwrite(str(mask_path), (desat_mask * 255.0).astype(np.uint8))
        cv2.imwrite(str(final_path), restored)

        rows = max(r.row for r in manifest.records)
        cols = max(r.col for r in manifest.records)
        tiles = [
            {
                "row": rec.row,
                "col": rec.col,
                "filename": rec.filename,
                "url": f"/scan-input/{rec.filename}",
            }
            for rec in manifest.records
        ]

        return {
            "rows": rows,
            "cols": cols,
            "tile_count": len(tiles),
            "tiles": tiles,
            "stitched_raw_url": "/scan-output/stitched_raw.png",
            "inverted_url": "/scan-output/inverted_positive.png",
            "white_balanced_url": "/scan-output/white_balanced.png",
            "leveled_url": "/scan-output/leveled.png",
            "contrasted_url": "/scan-output/contrasted.png",
            "mask_url": "/scan-output/desaturation_mask.png",
            "final_url": "/scan-output/processed_negative.png",
            "final_label": "Processed Negative Output",
        }


class Stitcher:
    def __init__(self, tiles_dir: Path | None = None, output_dir: Path | None = None):
        repo_root = Path(__file__).resolve().parents[1]
        self.tiles_dir = tiles_dir or (
            repo_root
            / "Image_integration&Post_Processing"
            / "Negative_example"
            / "norway_split_40_overlap"
        )
        self.output_dir = output_dir or (
            Path(__file__).resolve().parent / "web" / "generated"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_overlaps(self):
        manifest_path = self.tiles_dir / "manifest.csv"
        metadata_path = self.tiles_dir / "metadata.json"
        manifest = parse_manifest(manifest_path)
        meta_ox, meta_oy = load_metadata_overlap(metadata_path)

        overlap_x = manifest.overlap_x if manifest.overlap_x is not None else meta_ox
        overlap_y = manifest.overlap_y if manifest.overlap_y is not None else meta_oy
        if overlap_x is None or overlap_y is None:
            raise ValueError("Could not determine overlap from manifest/metadata.")
        return manifest, overlap_x, overlap_y

    def process_scan(self, tiles_dir: Path) -> dict:
        self.tiles_dir = tiles_dir
        if not self.tiles_dir.exists():
            raise FileNotFoundError(f"Tiles directory not found: {self.tiles_dir}")

        manifest, overlap_x, overlap_y = self._resolve_overlaps()
        stitched_raw = stitch_tiles(
            tiles_dir=self.tiles_dir,
            records=manifest.records,
            overlap_x=overlap_x,
            overlap_y=overlap_y,
        )
        inverted = invert_negative(stitched_raw)
        wb = apply_white_balance(inverted, clip_percent=0.5)
        leveled = stretch_channel_percentiles(
            wb,
            black_point=0.8,
            white_point=99.2,
        )
        contrasted = apply_luminance_clahe(leveled, clip_limit=1.4)
        desat_mask = build_cast_suppression_mask(contrasted, sigma=7.0)
        cast_corrected = apply_selective_desaturation(
            image_bgr=contrasted,
            mask=desat_mask,
            strength=0.20,
        )
        restored = apply_unsharp_mask(cast_corrected, amount=0.35)

        stitched_path = self.output_dir / "stitched_raw.png"
        inverted_path = self.output_dir / "inverted_positive.png"
        white_balanced_path = self.output_dir / "white_balanced.png"
        leveled_path = self.output_dir / "leveled.png"
        contrasted_path = self.output_dir / "contrasted.png"
        mask_path = self.output_dir / "desaturation_mask.png"
        final_path = self.output_dir / "processed_negative.png"

        cv2.imwrite(str(stitched_path), stitched_raw)
        cv2.imwrite(str(inverted_path), inverted)
        cv2.imwrite(str(white_balanced_path), wb)
        cv2.imwrite(str(leveled_path), leveled)
        cv2.imwrite(str(contrasted_path), contrasted)
        cv2.imwrite(str(mask_path), (desat_mask * 255.0).astype(np.uint8))
        cv2.imwrite(str(final_path), restored)

        rows = max(r.row for r in manifest.records)
        cols = max(r.col for r in manifest.records)
        tiles = [
            {
                "row": rec.row,
                "col": rec.col,
                "filename": rec.filename,
                "url": f"/scan-input/{rec.filename}",
            }
            for rec in manifest.records
        ]

        return {
            "rows": rows,
            "cols": cols,
            "tile_count": len(tiles),
            "tiles": tiles,
            "stitched_raw_url": "/scan-output/stitched_raw.png",
            "inverted_url": "/scan-output/inverted_positive.png",
            "white_balanced_url": "/scan-output/white_balanced.png",
            "leveled_url": "/scan-output/leveled.png",
            "contrasted_url": "/scan-output/contrasted.png",
            "mask_url": "/scan-output/desaturation_mask.png",
            "final_url": "/scan-output/processed_negative.png",
            "final_label": "Processed Negative Output",
        }
