"""
Capture integration helpers for the Raspberry Pi scanner.

The preview artifact is display-oriented: it supports per-tile brightness
normalization and overlap blending so the operator can inspect the scan.

The RAW artifact is data-oriented: it stitches the Bayer mosaics into a real
multi-tile DNG. By default it does not apply tile brightness gains, because the
post-processing pipeline should see fixed-exposure sensor data.
"""

from __future__ import annotations

import json
import math
import re
import struct
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - scanner Pi image should include numpy
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - scanner Pi image should include Pillow
    Image = None

try:
    from .config import (
        CAPTURES_DIR,
        CAPTURE_MANIFEST_FILENAME,
        STITCHED_PREVIEW_FILENAME,
        STITCHED_RAW_FILENAME,
        X_SEGMENTS,
        Y_SEGMENTS,
    )
except ImportError:
    from config import (
        CAPTURES_DIR,
        CAPTURE_MANIFEST_FILENAME,
        STITCHED_PREVIEW_FILENAME,
        STITCHED_RAW_FILENAME,
        X_SEGMENTS,
        Y_SEGMENTS,
    )

try:
    from . import config as scan_config
except ImportError:
    import config as scan_config


_TILE_RE = re.compile(r"row_(\d+)_col_(\d+)\.(dng|jpg|jpeg|png)$", re.IGNORECASE)


@dataclass(frozen=True)
class Placement:
    rows: int
    cols: int
    tile_width: int
    tile_height: int
    step_x_dx: int
    step_x_dy: int
    step_y_dx: int
    step_y_dy: int
    origin_x: int
    origin_y: int
    stride_x: int
    stride_y: int
    overlap_x: int
    overlap_y: int
    canvas_width: int
    canvas_height: int
    source: str


@dataclass(frozen=True)
class RawTile:
    pixels: Any
    width: int
    height: int
    metadata: dict[str, Any]


def integrate_captures(captures_dir: Path = CAPTURES_DIR) -> dict[str, Any]:
    """Create stitched preview, stitched RAW DNG, and integration metadata."""
    captures_dir.mkdir(parents=True, exist_ok=True)
    raw_tiles = _discover_tiles(captures_dir, {".dng"})
    preview_tiles = _discover_tiles(captures_dir, {".jpg", ".jpeg", ".png"})

    preview_path = captures_dir / STITCHED_PREVIEW_FILENAME
    raw_path = captures_dir / STITCHED_RAW_FILENAME
    manifest_path = captures_dir / CAPTURE_MANIFEST_FILENAME

    placement = _build_placement(raw_tiles, preview_tiles)
    brightness_gains = _estimate_preview_brightness_gains(preview_tiles)
    preview_offsets, alignment_refinement = _estimate_preview_alignment_offsets(
        preview_tiles,
        placement,
    )
    pair_offsets = _load_manual_pair_offsets(captures_dir)
    manual_positions = _manual_abs_positions(pair_offsets, Y_SEGMENTS, X_SEGMENTS) if pair_offsets else None
    preview_origins, preview_cw, preview_ch = _resolve_tile_origins(
        placement, manual_positions, preview_offsets, round_to_even=False
    )
    raw_origins_dynamic, raw_cw_dynamic, raw_ch_dynamic = _resolve_tile_origins(
        placement, manual_positions, preview_offsets, round_to_even=True
    )
    raw_origins, raw_cw, raw_ch, raw_canvas_meta = _resolve_stable_raw_canvas(
        placement, manual_positions, preview_offsets
    )
    preview_crop, preview_crop_meta = _edge_alignment_crop(
        placement, preview_origins, (preview_cw, preview_ch), round_to_even=False
    )
    raw_crop = None
    raw_crop_meta = {
        "enabled": False,
        "reason": "canonical_raw_uses_fixed_canvas",
        "canvas_size": {"width": raw_cw, "height": raw_ch},
        "dynamic_canvas_size_without_fixed_margin": {
            "width": raw_cw_dynamic,
            "height": raw_ch_dynamic,
        },
    }
    preview_result = _stitch_preview(
        preview_tiles,
        preview_path,
        placement,
        brightness_gains,
        origins=preview_origins,
        canvas_size=(preview_cw, preview_ch),
        canvas_crop=preview_crop,
    )
    raw_result = _stitch_raw_dng(
        raw_tiles, raw_path, placement,
        origins=raw_origins,
        canvas_size=(raw_cw, raw_ch),
        canvas_crop=raw_crop,
    )

    manifest = {
        "created_at": time.time(),
        "grid": {"rows": Y_SEGMENTS, "cols": X_SEGMENTS},
        "placement": {
            "tile_width": placement.tile_width,
            "tile_height": placement.tile_height,
            "step_x_dx": placement.step_x_dx,
            "step_x_dy": placement.step_x_dy,
            "step_y_dx": placement.step_y_dx,
            "step_y_dy": placement.step_y_dy,
            "origin_x": placement.origin_x,
            "origin_y": placement.origin_y,
            "stride_x": placement.stride_x,
            "stride_y": placement.stride_y,
            "overlap_x": placement.overlap_x,
            "overlap_y": placement.overlap_y,
            "canvas_width": placement.canvas_width,
            "canvas_height": placement.canvas_height,
            "source": placement.source,
        },
        "motor_model": {
            "stepper": "E Series Nema 17 Bipolar 1.8deg 17Ncm 1A 42x42x23mm",
            "full_steps_per_rev": 200,
            "configured_microsteps_per_rev": getattr(scan_config, "SPR", None),
            "x_steps_per_segment": getattr(scan_config, "X_STEPS_PER_SEG", None),
            "y_steps_per_segment": getattr(scan_config, "Y_STEPS_PER_SEG", None),
            "note": (
                "Motor angle alone cannot determine pixel overlap. Set "
                "STITCH_TILE_STRIDE_X_PX/Y_PX or STITCH_OVERLAP_X_PX/Y_PX after "
                "mechanical/optical calibration."
            ),
        },
        "raw_tile_count": len(raw_tiles),
        "preview_tile_count": len(preview_tiles),
        "raw_tiles": _tile_manifest(raw_tiles),
        "preview_tiles": _tile_manifest(preview_tiles),
        "brightness_normalization": {
            "target": "preview_only",
            "gains": {
                f"row_{row}_col_{col}": float(gain)
                for (row, col), gain in sorted(brightness_gains.items())
            },
        },
        "alignment_refinement": alignment_refinement,
        "edge_crop": {
            "preview": preview_crop_meta,
            "raw": raw_crop_meta,
        },
        "raw_canvas": raw_canvas_meta,
        "manual_alignment": {
            "pairs_defined": len(pair_offsets),
            "full_coverage": manual_positions is not None,
        },
        "stitched_preview": _relative_or_none(preview_result),
        "stitched_raw": _relative_or_none(raw_result),
        "raw_integration_mode": (
            "multi_tile_bayer_mosaic_dng" if raw_result else "missing_raw_tiles"
        ),
        "true_raw_mosaic_pending": False if raw_result else True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "stitched_preview_path": str(preview_result) if preview_result else None,
        "stitched_raw_path": str(raw_result) if raw_result else None,
    }


def _discover_tiles(captures_dir: Path, suffixes: set[str]) -> dict[tuple[int, int], Path]:
    tiles: dict[tuple[int, int], Path] = {}
    for path in captures_dir.iterdir() if captures_dir.exists() else []:
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        match = _TILE_RE.match(path.name)
        if not match:
            continue
        row = int(match.group(1))
        col = int(match.group(2))
        tiles[(row, col)] = path
    return tiles


def _build_placement(
    raw_tiles: dict[tuple[int, int], Path],
    preview_tiles: dict[tuple[int, int], Path],
) -> Placement:
    tile_width, tile_height = _read_tile_size(raw_tiles, preview_tiles)
    step_x_dx, step_x_dy, step_y_dx, step_y_dy, source = _resolve_step_vectors(
        tile_width,
        tile_height,
    )
    for name, value in {
        "step_x_dx": step_x_dx,
        "step_x_dy": step_x_dy,
        "step_y_dx": step_y_dx,
        "step_y_dy": step_y_dy,
    }.items():
        if value % 2 != 0:
            raise ValueError(
                f"{name} must be even to preserve 2x2 Bayer CFA phase, got {value}."
            )

    stride_x = abs(step_x_dx)
    stride_y = abs(step_y_dy)
    if stride_x <= 0 or stride_y <= 0:
        raise ValueError("Primary stitch stride must be positive")

    overlap_x = max(tile_width - stride_x, 0)
    overlap_y = max(tile_height - stride_y, 0)

    x_values: list[int] = []
    y_values: list[int] = []
    for row in range(Y_SEGMENTS):
        for col in range(X_SEGMENTS):
            x = col * step_x_dx + row * step_y_dx
            y = col * step_x_dy + row * step_y_dy
            x_values.append(int(x))
            y_values.append(int(y))

    min_x = min(x_values)
    min_y = min(y_values)
    max_x = max(x_values) + tile_width
    max_y = max(y_values) + tile_height

    return Placement(
        rows=Y_SEGMENTS,
        cols=X_SEGMENTS,
        tile_width=tile_width,
        tile_height=tile_height,
        step_x_dx=step_x_dx,
        step_x_dy=step_x_dy,
        step_y_dx=step_y_dx,
        step_y_dy=step_y_dy,
        origin_x=-min_x,
        origin_y=-min_y,
        stride_x=stride_x,
        stride_y=stride_y,
        overlap_x=overlap_x,
        overlap_y=overlap_y,
        canvas_width=max_x - min_x,
        canvas_height=max_y - min_y,
        source=source,
    )


def _read_tile_size(
    raw_tiles: dict[tuple[int, int], Path],
    preview_tiles: dict[tuple[int, int], Path],
) -> tuple[int, int]:
    if raw_tiles:
        tile = _read_raw_tile(raw_tiles[sorted(raw_tiles)[0]])
        return tile.width, tile.height
    if Image is None or not preview_tiles:
        raise RuntimeError("No RAW or preview tiles available for integration")
    with Image.open(preview_tiles[sorted(preview_tiles)[0]]) as img:
        return img.size


def _resolve_stride(tile_width: int, tile_height: int) -> tuple[int, int, str]:
    stride_x = getattr(scan_config, "STITCH_TILE_STRIDE_X_PX", None)
    stride_y = getattr(scan_config, "STITCH_TILE_STRIDE_Y_PX", None)
    if stride_x is not None or stride_y is not None:
        return int(stride_x or tile_width), int(stride_y or tile_height), "configured_stride_px"

    overlap_x = int(getattr(scan_config, "STITCH_OVERLAP_X_PX", 0))
    overlap_y = int(getattr(scan_config, "STITCH_OVERLAP_Y_PX", 0))
    if overlap_x or overlap_y:
        return tile_width - overlap_x, tile_height - overlap_y, "configured_overlap_px"

    return tile_width, tile_height, "no_overlap_default"


def _resolve_step_vectors(
    tile_width: int,
    tile_height: int,
) -> tuple[int, int, int, int, str]:
    step_x_dx = getattr(scan_config, "STITCH_STEP_X_DX_PX", None)
    step_x_dy = getattr(scan_config, "STITCH_STEP_X_DY_PX", None)
    step_y_dx = getattr(scan_config, "STITCH_STEP_Y_DX_PX", None)
    step_y_dy = getattr(scan_config, "STITCH_STEP_Y_DY_PX", None)

    configured = [step_x_dx, step_x_dy, step_y_dx, step_y_dy]
    if any(value is not None for value in configured):
        if (
            step_x_dx is None
            or step_x_dy is None
            or step_y_dx is None
            or step_y_dy is None
        ):
            raise ValueError(
                "If using calibrated step vectors, set all of "
                "STITCH_STEP_X_DX_PX, STITCH_STEP_X_DY_PX, STITCH_STEP_Y_DX_PX, STITCH_STEP_Y_DY_PX."
            )
        sx_dx = int(step_x_dx)
        sx_dy = int(step_x_dy)
        sy_dx = int(step_y_dx)
        sy_dy = int(step_y_dy)
        return (
            sx_dx,
            sx_dy,
            sy_dx,
            sy_dy,
            "configured_step_vectors_px",
        )

    stride_x, stride_y, source = _resolve_stride(tile_width, tile_height)
    return int(stride_x), 0, 0, int(stride_y), source


def _tile_origin(placement: Placement, row: int, col: int) -> tuple[int, int]:
    x = col * placement.step_x_dx + row * placement.step_y_dx + placement.origin_x
    y = col * placement.step_x_dy + row * placement.step_y_dy + placement.origin_y
    return int(x), int(y)


def _tile_origin_with_offset(
    placement: Placement,
    row: int,
    col: int,
    offsets: dict[tuple[int, int], tuple[int, int]] | None,
) -> tuple[int, int]:
    x, y = _tile_origin(placement, row, col)
    if not offsets:
        return x, y
    dx, dy = offsets.get((row, col), (0, 0))
    return int(x + dx), int(y + dy)



def _load_manual_pair_offsets(captures_dir: Path) -> dict:
    """Read current-scan alignment measurements; last entry per pair wins."""
    candidates = []
    scan_local = captures_dir / "alignment_measurements.json"
    if scan_local.exists():
        candidates.append((scan_local, None))

    persistent = Path(__file__).parent / "calibration" / "alignment_measurements.json"
    capture_signature = _capture_signature_for_dir(captures_dir)
    if persistent.exists() and capture_signature:
        candidates.append((persistent, capture_signature))

    if not candidates:
        return {}

    result = {}
    for path, expected_signature in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in data.get("measurements", []):
            if expected_signature and m.get("capture_signature") != expected_signature:
                continue
            try:
                src = (int(m["row"]), int(m["col"]))
                dst = (int(m["neighbor_row"]), int(m["neighbor_col"]))
                result[(src, dst)] = (int(m["dx"]), int(m["dy"]))
            except (KeyError, ValueError, TypeError):
                continue
    return result


def _capture_signature_for_dir(captures_dir: Path) -> str | None:
    tiles = _discover_tiles(captures_dir, {".jpg", ".jpeg", ".png"})
    if not tiles:
        return None
    digest = hashlib.sha256()
    for (row, col), path in sorted(tiles.items()):
        stat = path.stat()
        digest.update(
            f"{row},{col},{path.name},{stat.st_size},{stat.st_mtime:.6f}\n".encode()
        )
    return digest.hexdigest()


def _manual_abs_positions(pair_offsets: dict, rows: int, cols: int):
    """BFS from (0,0) through pair offsets -> absolute canvas coords per tile.

    Returns the full dict only when every grid tile is reachable; None otherwise.
    """
    if not pair_offsets:
        return None
    positions = {(0, 0): (0, 0)}
    changed = True
    while changed:
        changed = False
        for (src, dst), (dx, dy) in pair_offsets.items():
            if src in positions and dst not in positions:
                sx, sy = positions[src]
                positions[dst] = (sx + dx, sy + dy)
                changed = True
    if not {(r, c) for r in range(rows) for c in range(cols)}.issubset(positions):
        return None
    return positions


def _resolve_tile_origins(
    placement: Placement,
    manual_positions,
    phase_offsets,
    round_to_even: bool = False,
) -> tuple:
    """Compute final canvas (x, y) for every tile.

    Returns (origins_dict, canvas_width, canvas_height).

    Priority: manual per-pair positions > global step-vector model.
    phase_offsets (from phase correlation) applied on top as a fine correction.
    round_to_even=True preserves 2x2 Bayer CFA phase for RAW stitching.
    """
    raw: dict = {}
    for row in range(placement.rows):
        for col in range(placement.cols):
            if manual_positions is not None and (row, col) in manual_positions:
                x, y = manual_positions[(row, col)]
            else:
                x = col * placement.step_x_dx + row * placement.step_y_dx
                y = col * placement.step_x_dy + row * placement.step_y_dy
            if phase_offsets:
                dx, dy = phase_offsets.get((row, col), (0, 0))
                x += dx
                y += dy
            raw[(row, col)] = (int(x), int(y))

    min_x = min(x for x, _ in raw.values())
    min_y = min(y for _, y in raw.values())

    origins: dict = {}
    for key, (x, y) in raw.items():
        cx, cy = x - min_x, y - min_y
        if round_to_even:
            cx = int(round(cx / 2)) * 2
            cy = int(round(cy / 2)) * 2
        origins[key] = (cx, cy)

    canvas_w = max(cx + placement.tile_width for cx, _ in origins.values())
    canvas_h = max(cy + placement.tile_height for _, cy in origins.values())
    if round_to_even:
        canvas_w = int(math.ceil(canvas_w / 2)) * 2
        canvas_h = int(math.ceil(canvas_h / 2)) * 2

    return origins, canvas_w, canvas_h


def _resolve_stable_raw_canvas(
    placement: Placement,
    manual_positions,
    phase_offsets,
) -> tuple[dict[tuple[int, int], tuple[int, int]], int, int, dict[str, Any]]:
    """Place RAW tiles on a fixed-size canvas for calibration compatibility.

    Preview alignment may crop ragged edges for display. The canonical RAW DNG
    must not change dimensions from scan to scan, otherwise a backlight/base
    calibration captured on one scan cannot be applied to the next scan. Reserve
    a fixed correction margin around the physical placement model and place the
    refined tile coordinates inside it.
    """
    max_correction = max(int(getattr(scan_config, "STITCH_REFINE_MAX_CORRECTION_PX", 0)), 0)
    margin = int(math.ceil(max_correction / 2)) * 2

    if manual_positions is not None:
        origins, canvas_w, canvas_h = _resolve_tile_origins(
            placement,
            manual_positions,
            phase_offsets,
            round_to_even=True,
        )
        return origins, canvas_w, canvas_h, {
            "stable": False,
            "reason": "manual_alignment_canvas",
            "width": int(canvas_w),
            "height": int(canvas_h),
            "margin_px": 0,
        }

    origins: dict[tuple[int, int], tuple[int, int]] = {}
    for row in range(placement.rows):
        for col in range(placement.cols):
            x, y = _tile_origin(placement, row, col)
            if phase_offsets:
                dx, dy = phase_offsets.get((row, col), (0, 0))
                x += dx
                y += dy
            x = int(round((x + margin) / 2)) * 2
            y = int(round((y + margin) / 2)) * 2
            origins[(row, col)] = (x, y)

    canvas_w = int(math.ceil((placement.canvas_width + 2 * margin) / 2)) * 2
    canvas_h = int(math.ceil((placement.canvas_height + 2 * margin) / 2)) * 2
    return origins, canvas_w, canvas_h, {
        "stable": True,
        "reason": "fixed_margin_around_physical_model",
        "width": canvas_w,
        "height": canvas_h,
        "margin_px": margin,
        "base_canvas_width": placement.canvas_width,
        "base_canvas_height": placement.canvas_height,
    }


def _estimate_preview_brightness_gains(
    tiles: dict[tuple[int, int], Path],
) -> dict[tuple[int, int], float]:
    if Image is None or np is None or not tiles:
        return {}

    medians: dict[tuple[int, int], float] = {}
    for key, path in tiles.items():
        with Image.open(path) as img:
            arr = np.asarray(img.convert("RGB"), dtype=np.float32)
        h, w = arr.shape[:2]
        y0, y1 = int(h * 0.25), int(h * 0.75)
        x0, x1 = int(w * 0.25), int(w * 0.75)
        crop = arr[y0:y1, x0:x1]
        luma = 0.2126 * crop[..., 0] + 0.7152 * crop[..., 1] + 0.0722 * crop[..., 2]
        medians[key] = float(np.median(luma))

    valid = [v for v in medians.values() if v > 1.0]
    if not valid:
        return {key: 1.0 for key in tiles}
    target = float(np.median(valid))
    return {key: float(np.clip(target / max(value, 1.0), 0.65, 1.55)) for key, value in medians.items()}


def _estimate_preview_alignment_offsets(
    tiles: dict[tuple[int, int], Path],
    placement: Placement,
) -> tuple[dict[tuple[int, int], tuple[int, int]], dict[str, Any]]:
    """Estimate offsets in two stages: tiles within rows, then row strips."""
    if Image is None or np is None or len(tiles) < 2:
        return {}, {"enabled": False, "reason": "missing_dependencies_or_tiles"}

    downsample = max(int(getattr(scan_config, "STITCH_REFINE_DOWNSAMPLE", 4)), 1)
    max_shift_px = max(int(getattr(scan_config, "STITCH_REFINE_MAX_SHIFT_PX", 64)), 0)
    max_shift_ds = max(1, int(round(max_shift_px / downsample)))
    min_overlap_px = max(int(getattr(scan_config, "STITCH_REFINE_MIN_OVERLAP_PX", 320)), 32)
    min_overlap_ds = max(32, int(round(min_overlap_px / downsample)))
    min_snr = float(getattr(scan_config, "STITCH_REFINE_MIN_SNR", 2.0))

    gray_tiles: dict[tuple[int, int], Any] = {}
    for key, path in tiles.items():
        with Image.open(path) as img:
            arr = np.asarray(img.convert("L"), dtype=np.float32)
        if downsample > 1:
            arr = arr[::downsample, ::downsample]
        gray_tiles[key] = arr

    residual_h: dict[tuple[int, int], tuple[int, int]] = {}
    residual_h_ds: dict[tuple[int, int], tuple[int, int]] = {}
    row_residuals: dict[int, tuple[int, int]] = {}
    accepted_tile_pairs = 0
    accepted_row_pairs = 0

    step_x_dx_ds = int(round(placement.step_x_dx / downsample))
    step_x_dy_ds = int(round(placement.step_x_dy / downsample))
    step_y_dy_ds = int(round(placement.step_y_dy / downsample))

    for row in range(Y_SEGMENTS):
        for col in range(X_SEGMENTS):
            if (row, col) not in gray_tiles:
                continue

            if col > 0 and (row, col - 1) in gray_tiles:
                ref = gray_tiles[(row, col - 1)]
                cur = gray_tiles[(row, col)]
                pair = _extract_overlap_pair(ref, cur, step_x_dx_ds, step_x_dy_ds)
                if pair is not None:
                    ref_patch, cur_patch = pair
                    if (
                        ref_patch.shape[0] >= min_overlap_ds
                        and ref_patch.shape[1] >= min_overlap_ds
                    ):
                        dx_ds, dy_ds, snr = _phase_correlation_shift(
                            ref_patch,
                            cur_patch,
                            max_shift_ds,
                        )
                        if snr >= min_snr:
                            residual = (int(dx_ds * downsample), int(dy_ds * downsample))
                            residual_h[(row, col)] = residual
                            residual_h_ds[(row, col)] = (int(dx_ds), int(dy_ds))
                            accepted_tile_pairs += 1

    max_correction_px = max(int(getattr(scan_config, "STITCH_REFINE_MAX_CORRECTION_PX", 96)), 0)
    row_internal_offsets: dict[tuple[int, int], tuple[int, int]] = {}
    row_internal_offsets_ds: dict[tuple[int, int], tuple[int, int]] = {}
    for row in range(Y_SEGMENTS):
        row_internal_offsets[(row, 0)] = (0, 0)
        row_internal_offsets_ds[(row, 0)] = (0, 0)
        for col in range(1, X_SEGMENTS):
            prev = row_internal_offsets.get((row, col - 1))
            prev_ds = row_internal_offsets_ds.get((row, col - 1))
            if prev is None or prev_ds is None:
                continue
            rx, ry = residual_h.get((row, col), (0, 0))
            rx_ds, ry_ds = residual_h_ds.get((row, col), (0, 0))
            row_internal_offsets[(row, col)] = (
                int(np.clip(prev[0] + rx, -max_correction_px, max_correction_px)),
                int(np.clip(prev[1] + ry, -max_correction_px, max_correction_px)),
            )
            row_internal_offsets_ds[(row, col)] = (
                int(np.clip(prev_ds[0] + rx_ds, -max_shift_ds * X_SEGMENTS, max_shift_ds * X_SEGMENTS)),
                int(np.clip(prev_ds[1] + ry_ds, -max_shift_ds * X_SEGMENTS, max_shift_ds * X_SEGMENTS)),
            )

    row_strips = _compose_alignment_rows(
        gray_tiles,
        row_internal_offsets_ds,
        placement,
        step_x_dx_ds,
        step_x_dy_ds,
    )
    row_strips = _pad_alignment_rows(row_strips)
    for row in range(1, Y_SEGMENTS):
        ref = row_strips.get(row - 1)
        cur = row_strips.get(row)
        if ref is None or cur is None:
            continue
        pair = _extract_overlap_pair(ref, cur, 0, step_y_dy_ds)
        if pair is None:
            continue
        ref_patch, cur_patch = pair
        if ref_patch.shape[0] < min_overlap_ds or ref_patch.shape[1] < min_overlap_ds:
            continue
        dx_ds, dy_ds, snr = _phase_correlation_shift(ref_patch, cur_patch, max_shift_ds)
        if snr >= min_snr:
            row_residuals[row] = (int(dx_ds * downsample), int(dy_ds * downsample))
            accepted_row_pairs += 1

    row_offsets: dict[int, tuple[int, int]] = {0: (0, 0)}
    for row in range(1, Y_SEGMENTS):
        prev = row_offsets.get(row - 1)
        if prev is None:
            continue
        rx, ry = row_residuals.get(row, (0, 0))
        row_offsets[row] = (
            int(np.clip(prev[0] + rx, -max_correction_px, max_correction_px)),
            int(np.clip(prev[1] + ry, -max_correction_px, max_correction_px)),
        )

    offsets: dict[tuple[int, int], tuple[int, int]] = {}
    for row in range(Y_SEGMENTS):
        row_dx, row_dy = row_offsets.get(row, (0, 0))
        for col in range(X_SEGMENTS):
            if (row, col) not in gray_tiles:
                continue
            tile_dx, tile_dy = row_internal_offsets.get((row, col), (0, 0))
            offsets[(row, col)] = (
                int(np.clip(row_dx + tile_dx, -max_correction_px, max_correction_px)),
                int(np.clip(row_dy + tile_dy, -max_correction_px, max_correction_px)),
            )

    used_tiles = len(offsets)

    nonzero = {
        key: value
        for key, value in offsets.items()
        if value != (0, 0)
    }
    serializable_offsets = {
        f"row_{row}_col_{col}": {"dx": int(dx), "dy": int(dy)}
        for (row, col), (dx, dy) in sorted(nonzero.items())
    }
    meta = {
        "enabled": True,
        "mode": "row_then_vertical_overlap_refinement",
        "downsample": downsample,
        "accepted_pairs": accepted_tile_pairs + accepted_row_pairs,
        "accepted_tile_pairs": accepted_tile_pairs,
        "accepted_row_pairs": accepted_row_pairs,
        "used_tiles": used_tiles,
        "max_shift_px": max_shift_px,
        "max_correction_px": max_correction_px,
        "horizontal_pair_residuals": [
            {
                "src": f"row_{row}_col_{col - 1}",
                "dst": f"row_{row}_col_{col}",
                "dx": int(dx),
                "dy": int(dy),
            }
            for (row, col), (dx, dy) in sorted(residual_h.items())
        ],
        "row_pair_residuals": [
            {
                "src": f"row_{row - 1}",
                "dst": f"row_{row}",
                "dx": int(dx),
                "dy": int(dy),
            }
            for row, (dx, dy) in sorted(row_residuals.items())
        ],
        "offsets": serializable_offsets,
    }
    return offsets, meta


def _compose_alignment_rows(
    gray_tiles: dict[tuple[int, int], Any],
    row_offsets_ds: dict[tuple[int, int], tuple[int, int]],
    placement: Placement,
    step_x_dx_ds: int,
    step_x_dy_ds: int,
) -> dict[int, Any]:
    rows: dict[int, Any] = {}
    if np is None:
        return rows
    for row in range(Y_SEGMENTS):
        row_tiles = [(col, gray_tiles[(row, col)]) for col in range(X_SEGMENTS) if (row, col) in gray_tiles]
        if not row_tiles:
            continue
        raw_positions = {}
        for col, tile in row_tiles:
            ox, oy = row_offsets_ds.get((row, col), (0, 0))
            raw_positions[col] = (col * step_x_dx_ds + ox, col * step_x_dy_ds + oy)
        min_x = min(x for x, _ in raw_positions.values())
        min_y = min(y for _, y in raw_positions.values())
        width = max(raw_positions[col][0] - min_x + tile.shape[1] for col, tile in row_tiles)
        height = max(raw_positions[col][1] - min_y + tile.shape[0] for col, tile in row_tiles)
        canvas = np.zeros((int(height), int(width)), dtype=np.float32)
        score_canvas = np.full((int(height), int(width)), -1.0, dtype=np.float32)
        for col, tile in row_tiles:
            x = int(raw_positions[col][0] - min_x)
            y = int(raw_positions[col][1] - min_y)
            score = _alignment_source_score(tile.shape[1], tile.shape[0])
            region = canvas[y : y + tile.shape[0], x : x + tile.shape[1]]
            score_region = score_canvas[y : y + tile.shape[0], x : x + tile.shape[1]]
            use_tile = score > score_region
            region[use_tile] = tile[use_tile]
            score_region[use_tile] = score[use_tile]
        rows[row] = canvas
    return rows


def _alignment_source_score(width: int, height: int) -> Any:
    x = np.linspace(-1.0, 1.0, int(width), dtype=np.float32)
    y = np.linspace(-1.0, 1.0, int(height), dtype=np.float32)
    score_x = 1.0 - np.abs(x)
    score_y = 1.0 - np.abs(y)
    return score_y[:, None] * score_x[None, :]


def _pad_alignment_rows(rows: dict[int, Any]) -> dict[int, Any]:
    if not rows or np is None:
        return rows
    max_height = max(int(row.shape[0]) for row in rows.values())
    max_width = max(int(row.shape[1]) for row in rows.values())
    padded = {}
    for row, image in rows.items():
        out = np.zeros((max_height, max_width), dtype=np.float32)
        out[: image.shape[0], : image.shape[1]] = image
        padded[row] = out
    return padded


def _edge_alignment_crop(
    placement: Placement,
    origins: dict[tuple[int, int], tuple[int, int]],
    canvas_size: tuple[int, int],
    round_to_even: bool,
) -> tuple[tuple[int, int, int, int] | None, dict[str, Any]]:
    """Crop to the common row/column coverage when offsets create ragged edges."""
    canvas_w, canvas_h = int(canvas_size[0]), int(canvas_size[1])
    if not origins:
        return None, {"enabled": False, "reason": "missing_origins"}

    row_extents = []
    for row in range(placement.rows):
        row_tiles = [
            origins[(row, col)]
            for col in range(placement.cols)
            if (row, col) in origins
        ]
        if row_tiles:
            row_extents.append((
                min(x for x, _ in row_tiles),
                max(x + placement.tile_width for x, _ in row_tiles),
            ))

    col_extents = []
    for col in range(placement.cols):
        col_tiles = [
            origins[(row, col)]
            for row in range(placement.rows)
            if (row, col) in origins
        ]
        if col_tiles:
            col_extents.append((
                min(y for _, y in col_tiles),
                max(y + placement.tile_height for _, y in col_tiles),
            ))

    left = max((x0 for x0, _ in row_extents), default=0)
    right = min((x1 for _, x1 in row_extents), default=canvas_w)
    top = max((y0 for y0, _ in col_extents), default=0)
    bottom = min((y1 for _, y1 in col_extents), default=canvas_h)

    left = max(0, min(left, canvas_w))
    right = max(0, min(right, canvas_w))
    top = max(0, min(top, canvas_h))
    bottom = max(0, min(bottom, canvas_h))

    min_width = int(canvas_w * 0.70)
    min_height = int(canvas_h * 0.70)
    if right - left < min_width or bottom - top < min_height:
        return None, {
            "enabled": False,
            "reason": "common_crop_too_small",
            "candidate": {"left": left, "top": top, "right": right, "bottom": bottom},
            "canvas_size": {"width": canvas_w, "height": canvas_h},
        }

    if round_to_even:
        left = int(math.ceil(left / 2)) * 2
        top = int(math.ceil(top / 2)) * 2
        right = int(math.floor(right / 2)) * 2
        bottom = int(math.floor(bottom / 2)) * 2

    crop = (left, top, right, bottom)
    cuts = {
        "left": left,
        "top": top,
        "right": canvas_w - right,
        "bottom": canvas_h - bottom,
    }
    if not any(cuts.values()):
        return None, {
            "enabled": False,
            "reason": "edges_already_rectangular",
            "canvas_size": {"width": canvas_w, "height": canvas_h},
        }
    return crop, {
        "enabled": True,
        "crop": {"left": left, "top": top, "right": right, "bottom": bottom},
        "cuts": cuts,
        "canvas_size_before": {"width": canvas_w, "height": canvas_h},
        "canvas_size_after": {"width": right - left, "height": bottom - top},
    }


def _extract_overlap_pair(
    ref_tile: Any,
    cur_tile: Any,
    dx: int,
    dy: int,
) -> tuple[Any, Any] | None:
    h = int(ref_tile.shape[0])
    w = int(ref_tile.shape[1])
    x0 = max(0, dx)
    y0 = max(0, dy)
    x1 = min(w, dx + w)
    y1 = min(h, dy + h)
    if x1 <= x0 or y1 <= y0:
        return None

    ref_patch = ref_tile[y0:y1, x0:x1]
    cur_patch = cur_tile[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
    if ref_patch.size == 0 or cur_patch.size == 0:
        return None
    return ref_patch, cur_patch


def _phase_correlation_shift(
    ref: Any,
    cur: Any,
    max_shift: int,
) -> tuple[int, int, float]:
    ref_f = ref.astype(np.float32, copy=False)
    cur_f = cur.astype(np.float32, copy=False)
    ref_f = ref_f - float(np.mean(ref_f))
    cur_f = cur_f - float(np.mean(cur_f))
    if not np.any(ref_f) or not np.any(cur_f):
        return 0, 0, 0.0

    fy, fx = ref_f.shape
    wy = np.hanning(fy).astype(np.float32)
    wx = np.hanning(fx).astype(np.float32)
    window = wy[:, None] * wx[None, :]
    ref_w = ref_f * window
    cur_w = cur_f * window

    cross = np.fft.fft2(ref_w) * np.conj(np.fft.fft2(cur_w))
    denom = np.abs(cross)
    denom[denom < 1e-9] = 1e-9
    corr = np.fft.ifft2(cross / denom).real

    peak_index = np.unravel_index(np.argmax(corr), corr.shape)
    dy = int(peak_index[0])
    dx = int(peak_index[1])
    if dy > fy // 2:
        dy -= fy
    if dx > fx // 2:
        dx -= fx

    dx = int(np.clip(dx, -max_shift, max_shift))
    dy = int(np.clip(dy, -max_shift, max_shift))

    peak = float(corr[peak_index])
    median = float(np.median(np.abs(corr)))
    snr = peak / (median + 1e-6)
    return dx, dy, snr


def _stitch_preview(
    tiles: dict[tuple[int, int], Path],
    output_path: Path,
    placement: Placement,
    brightness_gains: dict[tuple[int, int], float],
    offsets: dict[tuple[int, int], tuple[int, int]] | None = None,
    origins: dict | None = None,
    canvas_size: tuple | None = None,
    canvas_crop: tuple[int, int, int, int] | None = None,
) -> Path | None:
    if Image is None or np is None or not tiles:
        return None
    if placement.overlap_x == 0 and placement.overlap_y == 0:
        return _stitch_preview_no_overlap(
            tiles,
            output_path,
            placement,
            brightness_gains,
            offsets,
            origins=origins,
            canvas_size=canvas_size,
            canvas_crop=canvas_crop,
        )

    # For large overlaps, averaging neighboring tiles can produce visible ghosting
    # because small geometric differences are blended together. Use the same
    # center-priority source selection as RAW integration to keep preview edges sharp.
    _pcw = canvas_size[0] if canvas_size else placement.canvas_width
    _pch = canvas_size[1] if canvas_size else placement.canvas_height
    canvas = np.zeros((_pch, _pcw, 3), dtype=np.float32)
    source_score = np.full((_pch, _pcw), -1.0, dtype=np.float32)

    for row in range(Y_SEGMENTS):
        for col in range(X_SEGMENTS):
            tile_path = tiles.get((row, col))
            if tile_path is None:
                continue
            with Image.open(tile_path) as img:
                tile = np.asarray(img.convert("RGB"), dtype=np.float32)
            gain = brightness_gains.get((row, col), 1.0)
            tile = np.clip(tile * gain, 0.0, 255.0)
            score = _raw_source_score(tile.shape[1], tile.shape[0], placement, row, col)
            x, y = origins[(row, col)] if origins and (row, col) in origins else _tile_origin_with_offset(placement, row, col, offsets)
            region = canvas[y : y + tile.shape[0], x : x + tile.shape[1]]
            score_region = source_score[y : y + tile.shape[0], x : x + tile.shape[1]]
            use_tile = score > score_region
            if use_tile.any():
                region[use_tile] = tile[use_tile]
                score_region[use_tile] = score[use_tile]

    if canvas_crop is not None:
        left, top, right, bottom = canvas_crop
        canvas = canvas[top:bottom, left:right]
    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    image.save(tmp_path, format="JPEG", quality=95)
    tmp_path.replace(output_path)
    return output_path


def _stitch_preview_no_overlap(
    tiles: dict[tuple[int, int], Path],
    output_path: Path,
    placement: Placement,
    brightness_gains: dict[tuple[int, int], float],
    offsets: dict[tuple[int, int], tuple[int, int]] | None = None,
    origins: dict | None = None,
    canvas_size: tuple | None = None,
    canvas_crop: tuple[int, int, int, int] | None = None,
) -> Path | None:
    _ncw = canvas_size[0] if canvas_size else placement.canvas_width
    _nch = canvas_size[1] if canvas_size else placement.canvas_height
    canvas = Image.new("RGB", (_ncw, _nch))
    for row in range(Y_SEGMENTS):
        for col in range(X_SEGMENTS):
            tile_path = tiles.get((row, col))
            if tile_path is None:
                continue
            with Image.open(tile_path) as img:
                tile = img.convert("RGB")
                gain = brightness_gains.get((row, col), 1.0)
                if abs(gain - 1.0) > 1e-3:
                    arr = np.asarray(tile, dtype=np.float32)
                    tile = Image.fromarray(np.clip(arr * gain, 0, 255).astype(np.uint8), mode="RGB")
                _pos = origins[(row, col)] if origins and (row, col) in origins else _tile_origin_with_offset(placement, row, col, offsets)
                canvas.paste(tile, _pos)
    if canvas_crop is not None:
        canvas = canvas.crop(canvas_crop)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    canvas.save(tmp_path, format="JPEG", quality=95)
    tmp_path.replace(output_path)
    return output_path


def _stitch_raw_dng(
    tiles: dict[tuple[int, int], Path],
    output_path: Path,
    placement: Placement,
    origins: dict | None = None,
    canvas_size: tuple | None = None,
    canvas_crop: tuple[int, int, int, int] | None = None,
) -> Path | None:
    if np is None or not tiles:
        return None

    first_key = sorted(tiles)[0]
    first = _read_raw_tile(tiles[first_key])
    if first.width != placement.tile_width or first.height != placement.tile_height:
        raise RuntimeError("RAW tile size does not match placement")

    _rcw = canvas_size[0] if canvas_size else placement.canvas_width
    _rch = canvas_size[1] if canvas_size else placement.canvas_height
    canvas = np.zeros((_rch, _rcw), dtype=np.uint16)
    if placement.overlap_x == 0 and placement.overlap_y == 0:
        for row in range(Y_SEGMENTS):
            for col in range(X_SEGMENTS):
                tile_path = tiles.get((row, col))
                if tile_path is None:
                    continue
                tile = _read_raw_tile(tile_path)
                if tile.width != placement.tile_width or tile.height != placement.tile_height:
                    raise RuntimeError(f"RAW tile size mismatch: {tile_path.name}")
                x, y = origins[(row, col)] if origins and (row, col) in origins else _tile_origin(placement, row, col)
                canvas[y : y + tile.height, x : x + tile.width] = tile.pixels
        if canvas_crop is not None:
            left, top, right, bottom = canvas_crop
            canvas = canvas[top:bottom, left:right]
        metadata = dict(first.metadata)
        metadata.update(
            {
                "width": int(canvas.shape[1]),
                "height": int(canvas.shape[0]),
                "software": "ece_445 software/src/integrator.py",
                "raw_overlap_policy": "none",
            }
        )
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        _write_linear_dng16(tmp_path, canvas, metadata)
        tmp_path.replace(output_path)
        return output_path

    source_score = np.full((_rch, _rcw), -1.0, dtype=np.float32)

    for row in range(Y_SEGMENTS):
        for col in range(X_SEGMENTS):
            tile_path = tiles.get((row, col))
            if tile_path is None:
                continue
            tile = _read_raw_tile(tile_path)
            if tile.width != placement.tile_width or tile.height != placement.tile_height:
                raise RuntimeError(f"RAW tile size mismatch: {tile_path.name}")
            x, y = origins[(row, col)] if origins and (row, col) in origins else _tile_origin(placement, row, col)
            region = canvas[y : y + tile.height, x : x + tile.width]
            score_region = source_score[y : y + tile.height, x : x + tile.width]
            score = _raw_source_score(tile.width, tile.height, placement, row, col)
            use_tile = score > score_region
            if use_tile.any():
                region[use_tile] = tile.pixels[use_tile]
                score_region[use_tile] = score[use_tile]

    if canvas_crop is not None:
        left, top, right, bottom = canvas_crop
        canvas = canvas[top:bottom, left:right]
    metadata = dict(first.metadata)
    metadata.update(
        {
            "width": int(canvas.shape[1]),
            "height": int(canvas.shape[0]),
            "software": "ece_445 software/src/integrator.py",
            "raw_overlap_policy": "preserve_sensor_samples_center_priority",
        }
    )
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    _write_linear_dng16(tmp_path, canvas, metadata)
    tmp_path.replace(output_path)
    return output_path


def _blend_mask(width: int, height: int, placement: Placement, row: int, col: int) -> Any:
    mask_x = np.ones(width, dtype=np.float32)
    mask_y = np.ones(height, dtype=np.float32)
    if placement.overlap_x > 0:
        ox = min(placement.overlap_x, width)
        ramp_in = np.linspace(0.0, 1.0, ox, endpoint=False, dtype=np.float32)
        ramp_out = np.linspace(1.0, 0.0, ox, endpoint=False, dtype=np.float32)
        if col > 0:
            mask_x[:ox] = np.minimum(mask_x[:ox], ramp_in)
        if col < placement.cols - 1:
            mask_x[-ox:] = np.minimum(mask_x[-ox:], ramp_out)
    if placement.overlap_y > 0:
        oy = min(placement.overlap_y, height)
        ramp_in = np.linspace(0.0, 1.0, oy, endpoint=False, dtype=np.float32)
        ramp_out = np.linspace(1.0, 0.0, oy, endpoint=False, dtype=np.float32)
        if row > 0:
            mask_y[:oy] = np.minimum(mask_y[:oy], ramp_in)
        if row < placement.rows - 1:
            mask_y[-oy:] = np.minimum(mask_y[-oy:], ramp_out)
    return mask_y[:, None] * mask_x[None, :]


def _raw_source_score(width: int, height: int, placement: Placement, row: int, col: int) -> Any:
    score_x = np.ones(width, dtype=np.float32)
    score_y = np.ones(height, dtype=np.float32)
    if placement.overlap_x > 0:
        ox = min(placement.overlap_x, width)
        ramp_in = np.linspace(0.0, 1.0, ox, endpoint=False, dtype=np.float32)
        ramp_out = np.linspace(1.0, 0.0, ox, endpoint=False, dtype=np.float32)
        if col > 0:
            score_x[:ox] = np.minimum(score_x[:ox], ramp_in)
        if col < placement.cols - 1:
            score_x[-ox:] = np.minimum(score_x[-ox:], ramp_out)
    if placement.overlap_y > 0:
        oy = min(placement.overlap_y, height)
        ramp_in = np.linspace(0.0, 1.0, oy, endpoint=False, dtype=np.float32)
        ramp_out = np.linspace(1.0, 0.0, oy, endpoint=False, dtype=np.float32)
        if row > 0:
            score_y[:oy] = np.minimum(score_y[:oy], ramp_in)
        if row < placement.rows - 1:
            score_y[-oy:] = np.minimum(score_y[-oy:], ramp_out)
    return score_y[:, None] * score_x[None, :]


def _read_raw_tile(path: Path) -> RawTile:
    try:
        import tifffile  # type: ignore

        with tifffile.TiffFile(str(path)) as tif:
            page = tif.pages[0]
            pixels = page.asarray().astype(np.uint16, copy=False)
            metadata = _metadata_from_tifffile_page(page)
        return RawTile(pixels=pixels, width=int(pixels.shape[1]), height=int(pixels.shape[0]), metadata=metadata)
    except Exception:
        return _read_raw_tile_minimal(path)


def _metadata_from_tifffile_page(page: Any) -> dict[str, Any]:
    tags = page.tags

    def value(name: str, default: Any = None) -> Any:
        return tags[name].value if name in tags else default

    return {
        "make": value("Make", "RaspberryPi"),
        "model": value("Model", "imx477"),
        "dng_version": value("DNGVersion", b"\x01\x04\x00\x00"),
        "cfa_repeat_pattern_dim": value("CFARepeatPatternDim", (2, 2)),
        "cfa_pattern": value("CFAPattern", b"\x02\x01\x01\x00"),
        "black_level": value("BlackLevel", (256, 256, 256, 256)),
        "white_level": value("WhiteLevel", 4095),
        "color_matrix_1": value(
            "ColorMatrix1",
            (14153, 10000, -3044, 10000, -375, 10000, -1816, 10000, 8959, 10000, 2541, 10000, 1324, 10000, 604, 10000, 7472, 10000),
        ),
        "as_shot_neutral": value("AsShotNeutral", (10000, 10000, 10000, 10000, 10000, 10000)),
    }


def _read_raw_tile_minimal(path: Path) -> RawTile:
    data = path.read_bytes()
    if data[:2] != b"II" or struct.unpack_from("<H", data, 2)[0] != 42:
        raise RuntimeError(f"Unsupported DNG/TIFF byte order: {path}")
    ifd_offset = struct.unpack_from("<I", data, 4)[0]
    entries = _read_ifd_entries(data, ifd_offset)
    width = int(_tag_value(data, entries[256])[0])
    height = int(_tag_value(data, entries[257])[0])
    bits = int(_tag_value(data, entries[258])[0])
    compression = int(_tag_value(data, entries.get(259), default=(1,))[0])
    samples_per_pixel = int(_tag_value(data, entries.get(277), default=(1,))[0])
    if compression != 1:
        raise RuntimeError(f"Unsupported compressed DNG tile: {path}")
    if samples_per_pixel != 1:
        raise RuntimeError(f"Unsupported multi-sample DNG tile: {path}")
    offsets = _tag_value(data, entries[273])
    counts = _tag_value(data, entries[279])
    if len(offsets) != len(counts):
        raise RuntimeError(f"Strip offset/count mismatch in DNG tile: {path}")
    raw_parts = []
    for offset, count in zip(offsets, counts):
        start = int(offset)
        end = start + int(count)
        if start < 0 or end > len(data):
            raise RuntimeError(f"Invalid strip bounds in DNG tile: {path}")
        raw_parts.append(data[start:end])
    raw = b"".join(raw_parts)
    pixels = _unpack_raw_pixels(raw, width, height, bits)
    metadata = {
        "make": _tag_ascii(data, entries.get(271)) or "RaspberryPi",
        "model": _tag_ascii(data, entries.get(272)) or "imx477",
        "dng_version": bytes(_tag_value(data, entries.get(50706), default=(1, 4, 0, 0))),
        "cfa_repeat_pattern_dim": tuple(_tag_value(data, entries.get(33421), default=(2, 2))),
        "cfa_pattern": bytes(_tag_value(data, entries.get(33422), default=(2, 1, 1, 0))),
        "black_level": tuple(_tag_value(data, entries.get(50714), default=(256, 256, 256, 256))),
        "white_level": _tag_value(data, entries.get(50717), default=(4095,))[0],
        "color_matrix_1": tuple(
            _tag_value(
                data,
                entries.get(50721),
                default=(14153, 10000, -3044, 10000, -375, 10000, -1816, 10000, 8959, 10000, 2541, 10000, 1324, 10000, 604, 10000, 7472, 10000),
            )
        ),
        "as_shot_neutral": tuple(_tag_value(data, entries.get(50728), default=(10000, 10000, 10000, 10000, 10000, 10000))),
    }
    return RawTile(pixels=pixels, width=width, height=height, metadata=metadata)


def _unpack_raw_pixels(raw: bytes, width: int, height: int, bits: int) -> Any:
    if bits == 16:
        return np.frombuffer(raw, dtype="<u2", count=width * height).reshape(height, width).copy()
    if bits != 12:
        raise RuntimeError(f"Unsupported DNG bit depth: {bits}")
    packed = np.frombuffer(raw, dtype=np.uint8)
    triplets = packed.reshape(-1, 3).astype(np.uint16)
    out = np.empty(triplets.shape[0] * 2, dtype=np.uint16)
    out[0::2] = triplets[:, 0] | ((triplets[:, 1] & 0x0F) << 8)
    out[1::2] = (triplets[:, 1] >> 4) | (triplets[:, 2] << 4)
    return out[: width * height].reshape(height, width).copy()


_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _read_ifd_entries(data: bytes, offset: int) -> dict[int, tuple[int, int, int]]:
    count = struct.unpack_from("<H", data, offset)[0]
    entries: dict[int, tuple[int, int, int]] = {}
    cursor = offset + 2
    for _ in range(count):
        tag, typ, n, value = struct.unpack_from("<HHII", data, cursor)
        entries[int(tag)] = (int(typ), int(n), int(value))
        cursor += 12
    return entries


def _tag_value(
    data: bytes,
    entry: tuple[int, int, int] | None,
    default: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    if entry is None:
        return default
    typ, count, value = entry
    size = _TYPE_SIZES[typ] * count
    raw = struct.pack("<I", value)[:size] if size <= 4 else data[value : value + size]
    if typ in (1, 7):
        return tuple(raw)
    if typ == 2:
        return (raw.rstrip(b"\x00").decode("ascii", errors="replace"),)
    if typ == 3:
        return struct.unpack("<" + "H" * count, raw)
    if typ == 4:
        return struct.unpack("<" + "I" * count, raw)
    if typ == 9:
        return struct.unpack("<" + "i" * count, raw)
    if typ == 5:
        vals = struct.unpack("<" + "I" * (count * 2), raw)
        return vals
    if typ == 10:
        vals = struct.unpack("<" + "i" * (count * 2), raw)
        return vals
    return default


def _tag_ascii(data: bytes, entry: tuple[int, int, int] | None) -> str | None:
    if entry is None:
        return None
    value = _tag_value(data, entry)
    return str(value[0]) if value else None


def _write_linear_dng16(path: Path, pixels: Any, metadata: dict[str, Any]) -> None:
    height, width = int(pixels.shape[0]), int(pixels.shape[1])
    raw_bytes = pixels.astype("<u2", copy=False).tobytes()
    software = metadata.get("software", "ece_445 scanner")
    entries = [
        _ifd_entry(254, 4, (0,)),
        _ifd_entry(256, 4, (width,)),
        _ifd_entry(257, 4, (height,)),
        _ifd_entry(258, 3, (16,)),
        _ifd_entry(259, 3, (1,)),
        _ifd_entry(262, 3, (32803,)),
        _ifd_entry(271, 2, _ascii_bytes(str(metadata.get("make", "RaspberryPi")))),
        _ifd_entry(272, 2, _ascii_bytes(str(metadata.get("model", "imx477")))),
        _ifd_entry(273, 4, (0,)),  # patched after layout
        _ifd_entry(277, 3, (1,)),
        _ifd_entry(278, 4, (height,)),
        _ifd_entry(279, 4, (len(raw_bytes),)),
        _ifd_entry(284, 3, (1,)),
        _ifd_entry(305, 2, _ascii_bytes(str(software))),
        _ifd_entry(33421, 3, tuple(int(v) for v in metadata.get("cfa_repeat_pattern_dim", (2, 2)))),
        _ifd_entry(33422, 1, tuple(int(v) for v in bytes(metadata.get("cfa_pattern", b"\x02\x01\x01\x00")))),
        _ifd_entry(50706, 1, (1, 4, 0, 0)),
        _ifd_entry(50707, 1, (1, 0, 0, 0)),
        _ifd_entry(50708, 2, _ascii_bytes(str(metadata.get("unique_camera_model", "Raspberry Pi imx477")))),
        _ifd_entry(50710, 1, (0, 1, 2)),
        _ifd_entry(50711, 3, (1,)),
        _ifd_entry(50713, 3, (2, 2)),
        _ifd_entry(50714, 3, tuple(int(v) for v in metadata.get("black_level", (256, 256, 256, 256)))),
        _ifd_entry(50717, 3, (int(metadata.get("white_level", 4095)),)),
        _ifd_entry(50721, 10, tuple(int(v) for v in metadata.get("color_matrix_1", ()))),
        _ifd_entry(50728, 5, tuple(int(v) for v in metadata.get("as_shot_neutral", (10000, 10000, 10000, 10000, 10000, 10000)))),
        _ifd_entry(50778, 3, (21,)),
        _ifd_entry(50781, 1, tuple(ord(ch) for ch in "ece445stitch1")),
    ]

    entries.sort(key=lambda item: item["tag"])
    ifd_size = 2 + 12 * len(entries) + 4
    extra_offset = 8 + ifd_size
    extra = bytearray()
    encoded_entries = []
    for entry in entries:
        if entry["tag"] == 273:
            encoded_entries.append((entry, None))
            continue
        payload = entry["payload"]
        if len(payload) <= 4:
            encoded_entries.append((entry, payload.ljust(4, b"\x00")))
        else:
            encoded_entries.append((entry, struct.pack("<I", extra_offset + len(extra))))
            extra.extend(payload)
            if len(extra) % 2:
                extra.append(0)

    data_offset = extra_offset + len(extra)
    if data_offset % 2:
        extra.append(0)
        data_offset += 1

    out = bytearray()
    out.extend(b"II")
    out.extend(struct.pack("<H", 42))
    out.extend(struct.pack("<I", 8))
    out.extend(struct.pack("<H", len(entries)))
    for entry, value_bytes in encoded_entries:
        if entry["tag"] == 273:
            value_bytes = struct.pack("<I", data_offset)
        out.extend(struct.pack("<HHI", entry["tag"], entry["type"], entry["count"]))
        out.extend(value_bytes)
    out.extend(struct.pack("<I", 0))
    out.extend(extra)
    out.extend(raw_bytes)
    path.write_bytes(out)


def _ifd_entry(tag: int, typ: int, values: tuple[Any, ...]) -> dict[str, Any]:
    if typ == 1:
        payload = bytes(int(v) & 0xFF for v in values)
        count = len(values)
    elif typ == 2:
        payload = bytes(values)
        count = len(payload)
    elif typ == 3:
        payload = struct.pack("<" + "H" * len(values), *(int(v) for v in values))
        count = len(values)
    elif typ == 4:
        payload = struct.pack("<" + "I" * len(values), *(int(v) for v in values))
        count = len(values)
    elif typ == 5:
        payload = struct.pack("<" + "I" * len(values), *(int(v) for v in values))
        count = len(values) // 2
    elif typ == 10:
        payload = struct.pack("<" + "i" * len(values), *(int(v) for v in values))
        count = len(values) // 2
    else:
        raise ValueError(f"Unsupported TIFF type: {typ}")
    return {"tag": tag, "type": typ, "count": count, "payload": payload}


def _ascii_bytes(value: str) -> tuple[int, ...]:
    return tuple(value.encode("ascii", errors="replace") + b"\x00")


def _tile_manifest(tiles: dict[tuple[int, int], Path]) -> list[dict[str, Any]]:
    entries = []
    for (row, col), path in sorted(tiles.items()):
        entries.append(
            {
                "row": row,
                "col": col,
                "path": path.name,
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            }
        )
    return entries


def _relative_or_none(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(CAPTURES_DIR))
    except ValueError:
        return str(path)
