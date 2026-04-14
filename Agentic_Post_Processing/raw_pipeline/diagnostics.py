"""diagnostics.py — Stage-aware diagnostics for agentic orchestration.

Produces a structured evaluator output dict that covers each pipeline stage
independently so an agent can reason about *which* stage failed and choose an
appropriate recovery strategy.

Output schema (from pipeline_update.md §12):
{
  "overall_score": float,
  "stage_scores": {
    "border_detection":    float,
    "film_characterization": float,
    "technical_inversion":  float,
    "rendering":            float,
  },
  "cast_type":               str | None,
  "suspected_failure_stage": str | None,
  "recommended_strategy":    str | None,
  "recommended_params":      dict,
  "confidence":              float,
  "notes":                   list[str],
}
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .models import (
    BorderDetectionResult,
    FilmCharacterization,
    InversionResult,
    RenderResult,
)

log = logging.getLogger(__name__)

# Thresholds for stage health scoring.
_GOOD_BORDER_CONF = 0.55
_POOR_BORDER_CONF = 0.25

_GOOD_CHAR_CONF = 0.50
_POOR_CHAR_CONF = 0.20

_GOOD_CHANNEL_SPREAD = 0.05
_BAD_CHANNEL_SPREAD = 0.20
_GOOD_CLIP_RATIO = 0.01
_BAD_CLIP_RATIO = 0.10

_GOOD_GRAY_MEAN_LOW = 0.30    # [0,1] scale
_GOOD_GRAY_MEAN_HIGH = 0.70
_GOOD_SHARPNESS = 150.0       # laplacian_variance in render_metrics
_POOR_SHARPNESS = 50.0

# Cast detection: deviation of per-channel means from their average.
_CAST_THRESHOLD = 0.05


def evaluate_border_result(border: BorderDetectionResult) -> dict[str, float]:
    """Score the border-detection stage.

    Returns
    -------
    dict with keys: 'confidence', 'border_frac', 'agreement', 'score'.
    """
    conf = float(border.confidence)
    border_frac = float(border.border_mask.mean())
    agreement = float(np.mean(list(border.method_votes.values())) if border.method_votes else 0.0)

    # Normalised score: confidence is the primary signal.
    score = float(np.clip(
        (conf + agreement) / 2.0, 0.0, 1.0
    ))

    return {
        "confidence": conf,
        "border_frac": border_frac,
        "agreement": agreement,
        "score": score,
    }


def evaluate_characterization(
    characterization: FilmCharacterization,
) -> dict[str, float]:
    """Score the film-characterisation stage.

    Returns
    -------
    dict with keys: border confidence, upper-ref confidence, base neutralness,
    density span, and score.
    """
    bc = float(characterization.border_confidence)
    uc = float(characterization.upper_ref_confidence)

    base_rgb = characterization.base_rgb.astype(np.float32)
    base_mean = float(base_rgb.mean())
    base_rel_spread = float(characterization.base_rel_spread)
    side_consistency_score = float(characterization.side_consistency_score)
    base_neutrality_score = float(np.clip(1.0 - base_rel_spread, 0.0, 1.0))

    if characterization.dmax_density_rgb is not None:
        upper_ref = characterization.dmax_density_rgb.astype(np.float32)
    elif characterization.upper_density_rgb is not None:
        upper_ref = characterization.upper_density_rgb.astype(np.float32)
    else:
        upper_ref = None

    if characterization.dmin_density_rgb is not None:
        lower_ref = characterization.dmin_density_rgb.astype(np.float32)
    else:
        lower_ref = np.zeros(3, dtype=np.float32)

    if upper_ref is not None:
        density_span = float(np.mean(np.clip(upper_ref - lower_ref, 0.0, None)))
        density_span_score = float(np.clip(density_span / 1.5, 0.0, 1.0))
    else:
        density_span = 0.0
        density_span_score = 0.0

    score = float(np.clip(
        bc * 0.38 +
        uc * 0.22 +
        base_neutrality_score * 0.15 +
        density_span_score * 0.10 +
        side_consistency_score * 0.15,
        0.0,
        1.0,
    ))

    return {
        "border_confidence": bc,
        "upper_ref_confidence": uc,
        "base_mean": base_mean,
        "base_sample_count": float(characterization.base_sample_count),
        "base_rel_spread": base_rel_spread,
        "base_neutrality_score": base_neutrality_score,
        "side_consistency_score": side_consistency_score,
        "side_estimate_count": float(len(characterization.side_base_rgbs)),
        "density_span": density_span,
        "density_span_score": density_span_score,
        "score": score,
    }


def evaluate_inversion(inversion: InversionResult) -> dict[str, float]:
    """Score the technical-inversion stage.

    Returns
    -------
    dict with keys from InversionResult.diagnostics plus score terms.
    """
    d = inversion.diagnostics
    spread = float(inversion.channel_spread)
    clip = float(inversion.clipping_ratio)
    density_p99 = float(d.get("density_p99_mean", 0.0))
    density_p50 = float(d.get("density_p50_mean", 0.0))
    density_headroom = max(density_p99 - density_p50, 0.0)

    spread_penalty = float(np.clip((spread - _GOOD_CHANNEL_SPREAD) /
                                   (_BAD_CHANNEL_SPREAD - _GOOD_CHANNEL_SPREAD), 0.0, 1.0))
    clip_penalty = float(np.clip((clip - _GOOD_CLIP_RATIO) /
                                 (_BAD_CLIP_RATIO - _GOOD_CLIP_RATIO), 0.0, 1.0))
    headroom_score = float(np.clip(density_headroom / 0.8, 0.0, 1.0))

    score = float(np.clip(
        (1.0 - spread_penalty) * 0.4 +
        (1.0 - clip_penalty) * 0.3 +
        headroom_score * 0.3,
        0.0,
        1.0,
    ))

    result = dict(d)
    result["score"] = score
    result["spread_penalty"] = spread_penalty
    result["clip_penalty"] = clip_penalty
    result["density_headroom"] = density_headroom
    result["headroom_score"] = headroom_score
    return result


def evaluate_render(render: RenderResult) -> dict[str, float]:
    """Score the aesthetic rendering stage.

    Returns
    -------
    dict with render_metrics fields plus 'score'.
    """
    m = render.render_metrics

    gray_mean = float(m.get("gray_mean", 0.5))
    sharpness = float(m.get("laplacian_variance", 0.0))
    clip_black = float(m.get("clipped_black_ratio", 0.0))
    clip_white = float(m.get("clipped_white_ratio", 0.0))

    # Exposure score: peak at mid-grey range.
    in_range = _GOOD_GRAY_MEAN_LOW <= gray_mean <= _GOOD_GRAY_MEAN_HIGH
    exposure_score = 1.0 if in_range else float(
        max(0.0, 1.0 - abs(gray_mean - 0.5) * 3.0)
    )

    # Sharpness score.
    sharp_score = float(np.clip(
        (sharpness - _POOR_SHARPNESS) / (_GOOD_SHARPNESS - _POOR_SHARPNESS), 0.0, 1.0
    ))

    # Clipping penalty.
    clip_penalty = float(np.clip((clip_black + clip_white) / 0.1, 0.0, 1.0))

    score = float(np.clip(
        exposure_score * 0.4 + sharp_score * 0.4 + (1.0 - clip_penalty) * 0.2,
        0.0, 1.0,
    ))

    result = dict(m)
    result["score"] = score
    result["exposure_score"] = exposure_score
    result["sharpness_score"] = sharp_score
    return result


def build_evaluator_output(
    border: BorderDetectionResult,
    characterization: FilmCharacterization,
    inversion: InversionResult,
    render: RenderResult,
) -> dict[str, Any]:
    """Build the full structured evaluator output for agentic orchestration.

    Returns
    -------
    dict matching the schema in pipeline_update.md §12.
    """
    border_scores = evaluate_border_result(border)
    char_scores = evaluate_characterization(characterization)
    inv_scores = evaluate_inversion(inversion)
    render_scores = evaluate_render(render)

    stage_scores = {
        "border_detection": border_scores["score"],
        "film_characterization": char_scores["score"],
        "technical_inversion": inv_scores["score"],
        "rendering": render_scores["score"],
    }

    # Overall score: weighted average of stage scores.
    weights = {"border_detection": 0.25, "film_characterization": 0.30,
               "technical_inversion": 0.30, "rendering": 0.15}
    overall_score = float(sum(stage_scores[k] * weights[k] for k in weights))

    # Cast detection from inversion diagnostics.
    cast_type = _detect_cast(inversion)

    # Identify suspected failure stage: lowest stage score below 0.4.
    min_stage = min(stage_scores, key=stage_scores.__getitem__)
    suspected_failure: str | None = min_stage if stage_scores[min_stage] < 0.4 else None

    # Recommend a recovery strategy.
    recommended_strategy, recommended_params = _recommend_strategy(
        border, characterization, inversion, stage_scores, cast_type
    )

    # Aggregate notes.
    notes: list[str] = list(characterization.notes)
    if cast_type:
        notes.append(f"Detected colour cast: {cast_type}.")
    if suspected_failure:
        notes.append(f"Suspected failure stage: {suspected_failure}.")
    if overall_score < 0.4:
        notes.append("Overall quality is low; consider recapture or leader calibration.")

    # Aggregate confidence from all stages.
    confidence = float(np.mean([
        border.confidence,
        characterization.border_confidence,
        characterization.upper_ref_confidence,
        inv_scores["score"],
    ]))

    output: dict[str, Any] = {
        "overall_score": round(overall_score, 4),
        "stage_scores": {k: round(v, 4) for k, v in stage_scores.items()},
        "cast_type": cast_type,
        "suspected_failure_stage": suspected_failure,
        "recommended_strategy": recommended_strategy,
        "recommended_params": recommended_params,
        "confidence": round(confidence, 4),
        "notes": notes,
        # Per-stage detail for inspection.
        "border_detail": {k: round(float(v), 5) for k, v in border_scores.items()},
        "characterization_detail": {k: round(float(v), 5) for k, v in char_scores.items()},
        "inversion_detail": {k: round(float(v), 5) for k, v in inv_scores.items()},
        "render_detail": {k: round(float(v), 5) for k, v in render_scores.items()},
    }

    log.info(
        "Evaluator: overall=%.3f  stages=%s  failure=%s  strategy=%s",
        overall_score, stage_scores, suspected_failure, recommended_strategy,
    )

    return output


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_cast(inversion: InversionResult) -> str | None:
    """Detect the dominant colour cast from per-channel means."""
    d = inversion.diagnostics
    mr = d.get("mean_r", 0.5)
    mg = d.get("mean_g", 0.5)
    mb = d.get("mean_b", 0.5)
    avg = (mr + mg + mb) / 3.0

    def _dev(ch: float) -> float:
        return ch - avg

    dominant = max(("red", _dev(mr)), ("green", _dev(mg)), ("blue", _dev(mb)),
                   key=lambda x: abs(x[1]))
    if abs(dominant[1]) > _CAST_THRESHOLD:
        return dominant[0]
    return None


def _recommend_strategy(
    border: BorderDetectionResult,
    characterization: FilmCharacterization,
    inversion: InversionResult,
    stage_scores: dict[str, float],
    cast_type: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """Choose a recovery strategy and parameter suggestions."""
    params: dict[str, Any] = {}

    # Border detection failure → try a different method.
    if stage_scores["border_detection"] < 0.4:
        if border.method_votes.get("low_variance", 0.0) > border.method_votes.get("projection", 0.0):
            return "retry_border_detection_projection", params
        return "retry_border_detection_low_variance", params

    # Film characterisation failure.
    if stage_scores["film_characterization"] < 0.4:
        if characterization.calibration_mode == "border_plus_scene_estimate":
            return "use_leader_calibration", params
        if characterization.dmax_density_rgb is None:
            return "use_conservative_upper_density", {"upper_density_percentile": 98.0}
        return "inspect_base_estimation", {"target": "border_clearbase_cluster"}

    # Inversion failure.
    if stage_scores["technical_inversion"] < 0.4:
        if inversion.channel_spread > _BAD_CHANNEL_SPREAD:
            return "inspect_base_estimation", {"target": "base_rgb_channel_balance"}
        return "use_conservative_upper_density", {"upper_density_percentile": 97.0}

    # Rendering failure.
    if stage_scores["rendering"] < 0.4:
        return "reduce_render_strength", {"saturation": 0.9, "unsharp_amount": 0.2}

    # Cast correction.
    if cast_type == "blue":
        params = {"wb_gains": [1.05, 1.0, 0.95]}
        return "reduce_render_strength", params
    if cast_type == "red":
        params = {"wb_gains": [0.95, 1.0, 1.05]}
        return "reduce_render_strength", params

    return None, params
