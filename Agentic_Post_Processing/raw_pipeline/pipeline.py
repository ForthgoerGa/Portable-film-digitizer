"""pipeline.py — Integration entrypoint for the raw-aware negative pipeline.

Public API:

    result = process_raw_negative(
        frame_raw_path="frame.dng",
        flat_raw_path="flat.dng",
        leader_raw_path=None,          # optional
        output_dir="/tmp/out",         # optional; saves intermediate images
        render_params=None,            # optional render overrides
    )

Returns a serialisable dict with stage metrics, evaluator output, and paths to
any saved intermediate images.

Design:
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  TECHNICAL INVERSION                                                    │
  │  raw_ingest → sensor_normalization → flat_field →                       │
  │  border_detection → film_characterization → density_inversion           │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  AESTHETIC RENDERING                                                    │
  │  rendering.render_positive                                              │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  DIAGNOSTICS                                                            │
  │  diagnostics.build_evaluator_output                                     │
  └─────────────────────────────────────────────────────────────────────────┘

This module does not modify any existing pipelines in the project.
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


def process_raw_negative(
    frame_raw_path: str,
    flat_raw_path: str,
    leader_raw_path: str | None = None,
    output_dir: str | None = None,
    render_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the complete raw-aware negative inversion pipeline.

    Parameters
    ----------
    frame_raw_path:
        Path to the negative frame RAW file (DNG / ARW / etc.).
        JPEG/PNG inputs are accepted as a fallback for testing.
    flat_raw_path:
        Path to the flat-field RAW file (same setup, no film).
    leader_raw_path:
        Optional path to a leader/calibration RAW frame.
    output_dir:
        If provided, intermediate and final images are saved here as PNG files.
    render_params:
        Optional dict overriding default rendering parameters
        (see rendering._DEFAULT_PARAMS for keys).

    Returns
    -------
    dict
        Serialisable dictionary with keys:
        - 'status':            'ok' | 'error'
        - 'error':             str (only when status='error')
        - 'stage_metrics':     per-stage scalar diagnostics
        - 'evaluator_output':  full structured evaluator dict
        - 'output_paths':      dict mapping name→path (only if output_dir given)
        - 'calibration_mode':  str
        - 'border_confidence': float
    """
    # ---- late imports to keep the module importable even without rawpy ----
    from .raw_ingest import load_raw_frame, load_image_as_raw_frame
    from .sensor_normalization import prepare_linear_rgb
    from .flat_field import build_flat_field_model, apply_flat_field
    from .border_detection import detect_border_regions
    from .film_characterization import characterize_film
    from .density_inversion import invert_negative
    from .rendering import render_positive
    from .diagnostics import build_evaluator_output

    output_paths: dict[str, str] = {}

    try:
        # ------------------------------------------------------------------
        # 1. RAW ingest — frame + flat (+ optional leader)
        # ------------------------------------------------------------------
        log.info("=== RAW ingest ===")
        frame_raw = _load_any(frame_raw_path, load_raw_frame, load_image_as_raw_frame)
        flat_raw  = _load_any(flat_raw_path,  load_raw_frame, load_image_as_raw_frame)
        leader_raw = None
        if leader_raw_path:
            leader_raw = _load_any(leader_raw_path, load_raw_frame, load_image_as_raw_frame)

        # ------------------------------------------------------------------
        # 2. Sensor normalisation
        # ------------------------------------------------------------------
        log.info("=== Sensor normalisation ===")
        frame_linear = prepare_linear_rgb(frame_raw)
        flat_linear  = prepare_linear_rgb(flat_raw)
        leader_linear: np.ndarray | None = None
        if leader_raw is not None:
            leader_linear = prepare_linear_rgb(leader_raw)

        # ------------------------------------------------------------------
        # 3. Flat-field model + correction  [TECHNICAL]
        # ------------------------------------------------------------------
        log.info("=== Flat-field correction ===")
        flat_model = build_flat_field_model(flat_linear)
        frame_corrected = apply_flat_field(frame_linear, flat_model)

        if output_dir:
            _save_rgb(frame_corrected, output_dir, "01_flat_corrected.png", output_paths)

        # ------------------------------------------------------------------
        # 4. Border detection  [TECHNICAL]
        # ------------------------------------------------------------------
        log.info("=== Border detection ===")
        border_result = detect_border_regions(frame_corrected)
        log.info("Border confidence: %.3f", border_result.confidence)

        if output_dir:
            _save_mask(border_result.border_mask, output_dir, "02_border_mask.png", output_paths)

        # ------------------------------------------------------------------
        # 5. Film characterisation  [TECHNICAL]
        # ------------------------------------------------------------------
        log.info("=== Film characterisation ===")
        film_char = characterize_film(
            frame_corrected,
            border_result,
            leader_rgb=leader_linear,
            flat_model=flat_model,
        )
        log.info(
            "Film base: R=%.4f G=%.4f B=%.4f  mode=%s",
            *film_char.base_rgb, film_char.calibration_mode,
        )

        # ------------------------------------------------------------------
        # 6. Density inversion  [TECHNICAL]
        # ------------------------------------------------------------------
        log.info("=== Density inversion ===")
        inversion_result = invert_negative(frame_corrected, film_char)

        if output_dir:
            _save_rgb(inversion_result.positive_linear_rgb,
                      output_dir, "03_technical_positive.png", output_paths)
            _save_density(inversion_result.density_norm_rgb,
                          output_dir, "04_density_norm.png", output_paths)

        # ------------------------------------------------------------------
        # 7. Aesthetic rendering  [RENDERING]
        # ------------------------------------------------------------------
        log.info("=== Rendering ===")
        render_result = render_positive(inversion_result.positive_linear_rgb, render_params)

        if output_dir:
            _save_rgb(render_result.rendered_rgb,
                      output_dir, "05_rendered.png", output_paths)

        # ------------------------------------------------------------------
        # 8. Diagnostics
        # ------------------------------------------------------------------
        log.info("=== Diagnostics ===")
        evaluator_output = build_evaluator_output(
            border_result, film_char, inversion_result, render_result
        )

        # ------------------------------------------------------------------
        # Assemble serialisable return value
        # ------------------------------------------------------------------
        stage_metrics: dict[str, Any] = {
            "flat_field": {
                "channel_means": list(flat_model.channel_means),
            },
            "border_detection": {
                "confidence": round(border_result.confidence, 4),
                "border_frac": round(float(border_result.border_mask.mean()), 4),
                "method_votes": {k: round(v, 4) for k, v in border_result.method_votes.items()},
                "frame_bbox": list(border_result.frame_bbox) if border_result.frame_bbox else None,
            },
            "film_characterization": {
                "base_rgb": [round(float(v), 5) for v in film_char.base_rgb],
                "base_sample_count": int(film_char.base_sample_count),
                "base_rel_spread": round(float(film_char.base_rel_spread), 5),
                "side_consistency_score": round(float(film_char.side_consistency_score), 5),
                "side_base_rgbs": {
                    k: [round(float(v), 5) for v in vals]
                    for k, vals in film_char.side_base_rgbs.items()
                },
                "upper_density_rgb": (
                    [round(float(v), 5) for v in film_char.upper_density_rgb]
                    if film_char.upper_density_rgb is not None else None
                ),
                "dmin_density_rgb": (
                    [round(float(v), 5) for v in film_char.dmin_density_rgb]
                    if film_char.dmin_density_rgb is not None else None
                ),
                "dmax_density_rgb": (
                    [round(float(v), 5) for v in film_char.dmax_density_rgb]
                    if film_char.dmax_density_rgb is not None else None
                ),
                "calibration_mode": film_char.calibration_mode,
                "border_confidence": round(film_char.border_confidence, 4),
                "upper_ref_confidence": round(film_char.upper_ref_confidence, 4),
                "notes": film_char.notes,
            },
            "density_inversion": {
                "channel_spread": round(inversion_result.channel_spread, 5),
                "clipping_ratio": round(inversion_result.clipping_ratio, 5),
                **{k: round(v, 5) for k, v in inversion_result.diagnostics.items()},
            },
            "rendering": {
                k: round(float(v), 5) for k, v in render_result.render_metrics.items()
            },
        }

        return {
            "status": "ok",
            "calibration_mode": film_char.calibration_mode,
            "border_confidence": round(border_result.confidence, 4),
            "stage_metrics": stage_metrics,
            "evaluator_output": evaluator_output,
            "output_paths": output_paths,
        }

    except Exception:
        tb = traceback.format_exc()
        log.error("process_raw_negative failed:\n%s", tb)
        return {
            "status": "error",
            "error": tb,
            "stage_metrics": {},
            "evaluator_output": {},
            "output_paths": output_paths,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_any(path: str, raw_loader, fallback_loader):
    """Try to load with raw_loader; fall back to fallback_loader for JPEGs."""
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
        return fallback_loader(path)
    try:
        return raw_loader(path)
    except ImportError:
        log.warning("rawpy not available; falling back to cv2 loader for %s", path)
        return fallback_loader(path)


def _save_rgb(
    img_rgb: np.ndarray,
    output_dir: str,
    name: str,
    paths: dict[str, str],
) -> None:
    """Save a float32 RGB [0,1] image as uint8 PNG (BGR for cv2)."""
    os.makedirs(output_dir, exist_ok=True)
    path = str(Path(output_dir) / name)
    bgr = cv2.cvtColor(
        np.clip(img_rgb * 255.0, 0, 255).astype(np.uint8),
        cv2.COLOR_RGB2BGR,
    )
    cv2.imwrite(path, bgr)
    stem = Path(name).stem
    paths[stem] = path
    log.debug("Saved %s", path)


def _save_mask(
    mask: np.ndarray,
    output_dir: str,
    name: str,
    paths: dict[str, str],
) -> None:
    """Save a bool/float mask as a grayscale PNG."""
    os.makedirs(output_dir, exist_ok=True)
    path = str(Path(output_dir) / name)
    u8 = np.clip(mask.astype(np.float32) * 255.0, 0, 255).astype(np.uint8)
    cv2.imwrite(path, u8)
    paths[Path(name).stem] = path


def _save_density(
    density_norm: np.ndarray | None,
    output_dir: str,
    name: str,
    paths: dict[str, str],
) -> None:
    """Save a normalised density image (float32 [0,1]) as a false-colour PNG."""
    if density_norm is None:
        return
    _save_rgb(density_norm, output_dir, name, paths)
