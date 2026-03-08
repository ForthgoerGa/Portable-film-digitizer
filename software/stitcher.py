from __future__ import annotations

from pathlib import Path

import cv2

from processing.negative_pipeline import (
    apply_white_balance,
    invert_negative,
    load_metadata_overlap,
    parse_manifest,
    stitch_tiles,
)


class Stitcher:
    def __init__(self, tiles_dir: Path | None = None, output_dir: Path | None = None):
        repo_root = Path(__file__).resolve().parents[1]
        self.tiles_dir = tiles_dir or (
            repo_root
            / "Image_integration&Post_Processing"
            / "Negative_example"
            / "norway_split_40_overlap"
        )
        self.output_dir = output_dir or (Path(__file__).resolve().parent / "web" / "generated")
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

    def process_demo_scan(self, wb_clip_percent: float = 0.5) -> dict:
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
        white_balanced = apply_white_balance(inverted, clip_percent=wb_clip_percent)

        stitched_path = self.output_dir / "stitched_raw.png"
        inverted_path = self.output_dir / "inverted_positive.png"
        white_balanced_path = self.output_dir / "white_balanced.png"

        cv2.imwrite(str(stitched_path), stitched_raw)
        cv2.imwrite(str(inverted_path), inverted)
        cv2.imwrite(str(white_balanced_path), white_balanced)

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
            "final_url": "/scan-output/white_balanced.png",
            "final_label": "White-Balanced Output",
        }
