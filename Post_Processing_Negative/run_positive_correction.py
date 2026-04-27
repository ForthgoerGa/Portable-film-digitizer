"""Flat-field correction and output integration for positive film tiles.

Pipeline:
  DNG frame + backlight DNG
  [1] load_raw_bayer(backlight)     Bayer float32 with sensor normalization
  [2] build_flat_model              Low-frequency illumination map (Gaussian)
  [3] load_raw_bayer(frame)         Bayer float32
  [4] crop to film_content_bbox     CFA-safe crop (if provided)
  [5] apply_flat_field              Vignetting/illumination-corrected Bayer
  [6] demosaic_to_rgb               Linear RGB float32
  [7] per-channel percentile WB     [stretch_low_pct, stretch_high_pct] per channel
  [8] gamma 2.2 encoding            Display RGB
  Output: {stem}_positive_final.png  +  positive_correction_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from negative_physical.flat_field import (
    apply_flat_field,
    build_flat_model,
    diagnose_flat_correction,
)
from negative_physical.raw_io import crop_raw_bayer_frame, load_raw_bayer
from negative_physical.render_preview import demosaic_to_rgb

SCRIPT_DIR = Path(__file__).resolve().parent
POSITIVE_FINAL_SUFFIX = "_positive_final.png"


def _parse_bbox(value: str | None) -> list[int] | None:
    if not value:
        return None
    parts = [int(v.strip()) for v in value.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--film-content-bbox expects x0,y0,x1,y1 (4 ints), got: {value!r}")
    return parts


def _resolve_backlight_path(input_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        p = explicit.resolve()
        if not p.exists():
            raise FileNotFoundError(f"Backlight frame not found: {p}")
        return p
    hints = ("backlight", "blacklight", "flat", "flatfield")
    for candidate in sorted(input_dir.glob("*.dng")):
        stem = candidate.stem.lower()
        if any(h in stem for h in hints):
            return candidate.resolve()
    raise FileNotFoundError(
        f"No backlight frame found in {input_dir}. Pass --backlight-frame explicitly."
    )


def _resolve_frame_paths(input_dir: Path, requested: list[Path] | None) -> list[Path]:
    if requested:
        return [p.resolve() for p in requested]
    paths = sorted(input_dir.glob("capture_*.dng"))
    if not paths:
        raise FileNotFoundError(f"No DNG frames found in {input_dir}")
    return [p.resolve() for p in paths]


def _per_channel_wb(rgb_linear: np.ndarray, low_pct: float, high_pct: float) -> np.ndarray:
    """Per-channel percentile stretch to [0, 1] for basic auto white-balance."""
    out = np.empty_like(rgb_linear)
    for c in range(3):
        ch = rgb_linear[:, :, c]
        lo = float(np.percentile(ch, low_pct))
        hi = float(np.percentile(ch, high_pct))
        denom = hi - lo
        if denom < 1e-6:
            out[:, :, c] = np.clip(ch - lo, 0.0, 1.0)
        else:
            out[:, :, c] = (ch - lo) / denom
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _gamma_encode(rgb: np.ndarray, gamma: float) -> np.ndarray:
    return np.clip(np.power(np.clip(rgb, 0.0, 1.0), 1.0 / gamma), 0.0, 1.0).astype(np.float32)


def _save_display_png(rgb_display: np.ndarray, path: Path) -> None:
    u8 = (np.clip(rgb_display, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(path), bgr):
        raise RuntimeError(f"Failed to write output: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flat-field correction and integration for positive film (slides/transparencies)."
    )
    parser.add_argument("--input-dir", type=Path, default=SCRIPT_DIR / "rpi_captures_positive")
    parser.add_argument(
        "--backlight-frame",
        "--flat",
        dest="backlight_frame",
        type=Path,
        default=None,
        help="Explicit backlight/flat-field DNG path.",
    )
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "output_positive")
    parser.add_argument("--frame", type=Path, action="append", default=None)
    parser.add_argument(
        "--film-content-bbox",
        type=str,
        default=None,
        help=(
            "RAW-coordinate crop bbox x0,y0,x1,y1. When set, both the backlight and "
            "film frame are cropped to this region before processing."
        ),
    )
    parser.add_argument(
        "--flat-strength",
        type=float,
        default=1.0,
        help="Flat-field correction strength. 1.0 = physical division; 0 = disabled.",
    )
    parser.add_argument("--flat-sigma-frac", type=float, default=0.02)
    parser.add_argument("--flat-max-side", type=int, default=1024)
    parser.add_argument(
        "--display-gamma",
        type=float,
        default=2.2,
        help="Gamma exponent for display encoding (applied as 1/gamma power curve).",
    )
    parser.add_argument("--stretch-low-pct", type=float, default=0.5)
    parser.add_argument("--stretch-high-pct", type=float, default=99.5)
    parser.add_argument("--skip-flat-corrected-preview", action="store_true")
    args = parser.parse_args()

    if args.flat_strength < 0.0:
        parser.error("--flat-strength must be >= 0")
    if args.flat_sigma_frac <= 0.0:
        parser.error("--flat-sigma-frac must be > 0")
    if args.flat_max_side < 64:
        parser.error("--flat-max-side must be >= 64")
    if not (0.0 <= args.stretch_low_pct < args.stretch_high_pct <= 100.0):
        parser.error("--stretch-low-pct / --stretch-high-pct out of range")
    if args.display_gamma <= 0.0:
        parser.error("--display-gamma must be > 0")

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    backlight_path = _resolve_backlight_path(input_dir, args.backlight_frame)
    frame_paths = _resolve_frame_paths(input_dir, args.frame)
    film_content_bbox = _parse_bbox(args.film_content_bbox)

    report: dict[str, Any] = {
        "status": "ok",
        "film_type": "positive",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "backlight_frame_path": str(backlight_path),
        "flat_field_strength": float(args.flat_strength),
        "flat_sigma_frac": float(args.flat_sigma_frac),
        "flat_max_side": int(args.flat_max_side),
        "display_gamma": float(args.display_gamma),
        "stretch_low_pct": float(args.stretch_low_pct),
        "stretch_high_pct": float(args.stretch_high_pct),
        "film_content_bbox": film_content_bbox,
        "frames": [],
    }

    # Build flat model (crop backlight to film content bbox if provided).
    print(f"Loading backlight: {backlight_path}", flush=True)
    flat_frame_full = load_raw_bayer(backlight_path)
    flat_frame = (
        crop_raw_bayer_frame(flat_frame_full, film_content_bbox, label="film_content_flat")
        if film_content_bbox is not None
        else flat_frame_full
    )
    flat_model = build_flat_model(
        flat_frame,
        sigma_frac=args.flat_sigma_frac,
        max_side=args.flat_max_side,
    )
    report["flat_model"] = flat_model.diagnostics

    for frame_path in frame_paths:
        print(f"Processing frame: {frame_path.name}", flush=True)
        frame_full = load_raw_bayer(frame_path)
        frame = (
            crop_raw_bayer_frame(frame_full, film_content_bbox, label="film_content")
            if film_content_bbox is not None
            else frame_full
        )

        corrected_bayer = apply_flat_field(frame, flat_model, strength=args.flat_strength)
        flat_diag = diagnose_flat_correction(frame, corrected_bayer, flat_model, args.flat_strength)

        rgb_linear = demosaic_to_rgb(corrected_bayer, flat_model.cfa_pattern)
        rgb_balanced = _per_channel_wb(rgb_linear, args.stretch_low_pct, args.stretch_high_pct)
        rgb_display = _gamma_encode(rgb_balanced, args.display_gamma)

        out_path = output_dir / f"{frame_path.stem}{POSITIVE_FINAL_SUFFIX}"
        _save_display_png(rgb_display, out_path)
        print(f"  → {out_path.name}", flush=True)

        if not args.skip_flat_corrected_preview:
            flat_prev = output_dir / f"{frame_path.stem}_flat_corrected_preview.png"
            _save_display_png(
                _gamma_encode(_per_channel_wb(demosaic_to_rgb(
                    corrected_bayer, flat_model.cfa_pattern
                ), 0.1, 99.9), 2.2),
                flat_prev,
            )

        report["frames"].append({
            "frame": str(frame_path),
            "output": str(out_path),
            "flat_correction_diagnostics": flat_diag,
        })

    report_path = output_dir / "positive_correction_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report: {report_path}", flush=True)


if __name__ == "__main__":
    main()
