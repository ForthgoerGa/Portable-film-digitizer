"""Data models (dataclasses) shared across all raw_pipeline stages.

All pixel arrays use float32, RGB channel order, values in [0, 1] unless
explicitly documented otherwise (e.g. density values which are always >= 0
and may exceed 1 for heavily exposed regions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class RawFrame:
    """A single RAW image loaded into linear float32 RGB."""

    path: str
    # H x W x 3 float32, RGB, linear, normalised to [0, 1] after sensor prep.
    # May be uint16 immediately after rawpy.postprocess(); call
    # sensor_normalization.prepare_linear_rgb() to convert.
    linear_rgb: np.ndarray
    # Metadata dict from rawpy (keys: 'camera_whitebalance', 'daylight_whitebalance', etc.)
    metadata: dict[str, Any]
    # Per-channel black level; scalar or shape (3,). None if unavailable.
    black_level: float | np.ndarray | None
    # Per-channel white/saturation level; scalar or shape (3,). None if unavailable.
    white_level: float | np.ndarray | None
    # Camera white-balance multipliers (R, G, B). None if unavailable.
    wb_multipliers: tuple[float, float, float] | None
    # Sensor / camera model name string from metadata, or None.
    sensor_name: str | None
    width: int
    height: int


@dataclass
class FlatFieldModel:
    """Illumination correction model derived from a flat-field RAW capture."""

    # Original flat frame in linear float32 RGB, normalised to [0, 1].
    flat_linear_rgb: np.ndarray
    # Flat divided by its own global mean — values near 1 everywhere indicate
    # uniform illumination.
    normalized_flat: np.ndarray
    # Low-frequency illumination map estimated via heavy Gaussian blur.
    # Shape H x W x 3 or H x W (grayscale); used for per-pixel correction.
    illumination_map: np.ndarray
    # Per-channel means of the flat frame before normalisation.
    channel_means: tuple[float, float, float]
    # Optional mask of valid (non-saturated, non-dead) flat pixels.
    valid_mask: np.ndarray | None = None


@dataclass
class BorderDetectionResult:
    """Output of the automatic border / clear-base detector."""

    # Tight bounding box of detected film frame content: (x0, y0, x1, y1) in
    # pixel coordinates.  None if detection failed.
    frame_bbox: tuple[int, int, int, int] | None
    # Bool mask (H x W) — True where the detector believes this pixel belongs
    # to the film border / clear base region.
    border_mask: np.ndarray
    # Bool mask (H x W) — True where the detector believes this is frame content.
    content_mask: np.ndarray
    # Bool mask (H x W) — best clear-base candidate from any single method.
    clearbase_candidate_mask: np.ndarray
    # Per-method confidence votes in [0, 1]: keys match method names
    # ('projection', 'edge', 'low_variance').
    method_votes: dict[str, float]
    # Aggregated confidence in [0, 1].
    confidence: float


@dataclass
class FilmCharacterization:
    """Film base colour and density reference estimates for a single frame."""

    # Estimated film base / orange-mask colour in linear float32 RGB [0, 1].
    base_rgb: np.ndarray
    # Number of pixels used in the final clear-base estimate.
    base_sample_count: int
    # Relative spread of the selected clear-base cluster. Lower is better.
    base_rel_spread: float
    # How consistent the side-wise base estimates are with each other.
    side_consistency_score: float
    # Side-wise base RGB estimates that participated in the final fusion.
    side_base_rgbs: dict[str, list[float]]
    # Conservative upper relative-density reference estimated from scene content
    # or leader content. Relative density is defined after base cancellation,
    # so clear base should map near 0.
    upper_density_rgb: np.ndarray | None
    # Optional lower relative-density reference, usually 0 for clear base. Kept
    # explicit so future calibrations can model a non-zero floor.
    dmin_density_rgb: np.ndarray | None
    # Optional upper relative-density reference from leader calibration.
    dmax_density_rgb: np.ndarray | None
    # Confidence in the border-derived base estimate; 0–1.
    border_confidence: float
    # Confidence in the upper-density reference; 0–1.
    upper_ref_confidence: float
    # One of: 'border_plus_scene_estimate', 'border_plus_leader'.
    calibration_mode: str
    # Human-readable notes about estimation quality or fallback decisions.
    notes: list[str] = field(default_factory=list)


@dataclass
class InversionResult:
    """Output of the technical density-inversion stage."""

    # Technical positive in linear float32 RGB [0, 1] before aesthetic rendering.
    positive_linear_rgb: np.ndarray
    # Density image (per-channel) after base subtraction; float32, values >= 0.
    density_rgb: np.ndarray
    # Density normalised to [0, 1] per channel using reference endpoints.
    # None if normalisation was not possible.
    density_norm_rgb: np.ndarray | None
    # Spread of per-channel means in the positive output; near 0 = balanced.
    channel_spread: float
    # Fraction of pixels clipped to 0 or 1 after normalisation; 0–1.
    clipping_ratio: float
    # Additional scalar diagnostics: 'mean_r', 'mean_g', 'mean_b', etc.
    diagnostics: dict[str, float]


@dataclass
class RenderResult:
    """Output of the aesthetic rendering stage."""

    # Final rendered image in float32 RGB [0, 1].
    rendered_rgb: np.ndarray
    # The technical positive before rendering (preserved for comparison).
    technical_positive_rgb: np.ndarray
    # Scalar metrics computed on the rendered output.
    render_metrics: dict[str, float]


@dataclass
class PipelineResult:
    """Aggregated result from a complete raw_pipeline run."""

    flat_model: FlatFieldModel
    border_result: BorderDetectionResult
    film_characterization: FilmCharacterization
    inversion_result: InversionResult
    render_result: RenderResult
    # Structured evaluator output matching the schema in diagnostics.py.
    evaluator_output: dict[str, Any]
