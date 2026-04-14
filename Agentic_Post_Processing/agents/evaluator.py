from __future__ import annotations

import json
import logging
import re
from typing import Any

import cv2
import numpy as np

import config
from models import PipelineParams, QualityResult

logger = logging.getLogger(__name__)

_PROMPT = """You are evaluating a processed film scan image.
Return ONLY valid JSON with this exact structure:
{
  "score": <float 0.0–1.0>,
  "feedback": "<one concise sentence>",
  "suggested_params": {
    "wb_clip_percent":        <float 0.2–1.5>,
    "black_point":            <float 0.5–2.0>,
    "white_point":            <float 97.0–99.5>,
    "clahe_clip":             <float 0.5–4.0>,
    "clahe_grid":             <int 4, 8, or 16>,
    "unsharp_amount":         <float 0.0–1.0>,
    "unsharp_sigma":          <float 0.5–3.0>,
    "desat_strength":         <float 0.0–0.6>,
    "desat_sigma":            <float 2.0–20.0>,
    "shadow_lift":            <float 0.0–40.0>,
    "gamma":                  <float 0.4–2.2>,
    "lab_strength":           <float 0.0–1.5>,
    "highlight_compression":  <float 0.0–0.5>,
    "vibrance":               <float -0.3–0.6>
  }
}

Scoring guide (use measured metrics below):
- Exposure (gray_mean): ideal range 100–165. Score near 1.0 within range; penalise heavily outside.
- Clipping: clipped_black_ratio and clipped_white_ratio should each be < 0.005 for a good score. Some stretching artifact is expected (< 0.01 total is acceptable).
- Color balance: channel_spread < 0.10 is excellent; > 0.20 is a significant cast. blue_red_delta and green_magenta_delta near 0 is ideal.
- Sharpness (laplacian_variance): > 150 is sharp; < 80 is soft.
- Saturation (mean_saturation): should be 0.08–0.45 for normal film. < 0.04 suggests a demasking failure (overly gray output).

Parameter guidance:
- gamma < 1.0 brightens (output = input^gamma, so 0.75 lifts midtones); > 1.0 darkens
- lab_strength > 1.0 for aggressive cast removal; 0.0 to disable
- highlight_compression 0.2–0.4 if clipped_white_ratio > 0.01
- vibrance 0.1–0.4 to restore colour if mean_saturation < 0.10
- clahe_grid=4 for fine-grained contrast; 16 for broad tonal correction
- unsharp_sigma 0.5–1.0 for fine detail; 1.5–2.5 for broad sharpening

Use the exact numeric ranges. Do not normalize values to 0-1 unless the range explicitly uses 0-1.
"""


class EvaluatorAgent:
    def __init__(
        self,
        model: Any | None = None,
        threshold: float | None = None,
        allow_heuristic_fallback: bool | None = None,
    ) -> None:
        self._model = model if model is not None else _build_default_model()
        self._threshold = threshold if threshold is not None else config.QUALITY_THRESHOLD
        self._allow_heuristic_fallback = (
            allow_heuristic_fallback if allow_heuristic_fallback is not None else model is None
        )
        self.last_mode = "uninitialized"
        self.last_metrics: dict[str, float] = {}

    def evaluate(self, image_bgr: np.ndarray) -> QualityResult:
        metrics = compute_image_metrics(image_bgr)
        self.last_metrics = metrics

        if self._model is None:
            self.last_mode = "heuristic"
            return _heuristic_evaluate(metrics, self._threshold)

        try:
            metrics_prompt = f"{_PROMPT}\nMeasured metrics:\n{json.dumps(metrics, indent=2)}"
            response = self._model.generate_content([metrics_prompt, _array_part(image_bgr)])
            payload = json.loads(_response_text(response).strip())
            score = float(payload["score"])
            feedback = str(payload.get("feedback", "")).strip()
            params = _params_from_payload(payload.get("suggested_params", {}))
            self.last_mode = config.MODEL_PROVIDER
            return QualityResult(
                score=score,
                passed=score >= self._threshold,
                feedback=feedback,
                suggested_params=params,
            )
        except Exception as exc:
            logger.warning("Evaluator failed: %s", exc)
            if self._allow_heuristic_fallback:
                self._model = None
                self.last_mode = "heuristic"
                return _heuristic_evaluate(metrics, self._threshold)
            self.last_mode = "default_failure"
            return QualityResult(
                score=0.0,
                passed=False,
                feedback=f"Parse error: {exc}",
                suggested_params=PipelineParams(),
            )


def _build_default_model() -> Any | None:
    if config.MODEL_PROVIDER == "openai_compatible" and config.OPENAI_API_KEY and config.OPENAI_BASE_URL:
        try:
            from agents.gateway_model import GatewayModelAdapter
            return GatewayModelAdapter(model_name=config.VLM_MODEL, group=config.MODEL_GROUP)
        except Exception as exc:
            logger.warning("Gateway model adapter unavailable for evaluator: %s", exc)

    if not config.GEMINI_API_KEY:
        return None

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError:
        logger.warning("google.genai is unavailable; evaluator will use heuristic fallback.")
        return None

    class _ModelAdapter:
        def __init__(self) -> None:
            self._client = genai.Client(api_key=config.GEMINI_API_KEY)
            self._types = types

        def generate_content(self, contents: list[Any]) -> Any:
            prompt, image_part = contents
            return self._client.models.generate_content(
                model=config.VLM_MODEL,
                contents=[prompt, image_part],
                config=self._types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=768,
                    response_mime_type="application/json",
                    thinking_config=self._types.ThinkingConfig(thinking_budget=0),
                ),
            )

    return _ModelAdapter()


def _array_part(image_bgr: np.ndarray) -> Any:
    from google.genai import types  # type: ignore

    ok, encoded = cv2.imencode(".jpg", image_bgr)
    if not ok:
        raise ValueError("Could not encode image for evaluation")
    return types.Part.from_bytes(data=encoded.tobytes(), mime_type="image/jpeg")


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return _extract_json_text(text)
    raise ValueError("Empty model response")


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def _params_from_payload(payload: dict[str, Any]) -> PipelineParams:
    def _cf(key: str, low: float, high: float, default: float) -> float:
        try:
            return max(low, min(high, float(payload.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _ci(key: str, choices: list[int], default: int) -> int:
        try:
            v = int(payload.get(key, default))
            return min(choices, key=lambda c: abs(c - v))
        except (TypeError, ValueError):
            return default

    return PipelineParams(
        wb_clip_percent=_cf("wb_clip_percent", 0.2, 1.5, 0.5),
        black_point=_cf("black_point", 0.5, 2.0, 0.8),
        white_point=_cf("white_point", 97.0, 99.5, 99.2),
        clahe_clip=_cf("clahe_clip", 0.5, 4.0, 1.4),
        clahe_grid=_ci("clahe_grid", [4, 8, 16], 8),
        unsharp_amount=_cf("unsharp_amount", 0.0, 1.0, 0.35),
        unsharp_sigma=_cf("unsharp_sigma", 0.5, 3.0, 1.2),
        desat_strength=_cf("desat_strength", 0.0, 0.6, 0.20),
        desat_sigma=_cf("desat_sigma", 2.0, 20.0, 7.0),
        shadow_lift=_cf("shadow_lift", 0.0, 40.0, 0.0),
        gamma=_cf("gamma", 0.4, 2.2, 1.0),
        lab_strength=_cf("lab_strength", 0.0, 1.5, 0.85),
        highlight_compression=_cf("highlight_compression", 0.0, 0.5, 0.0),
        vibrance=_cf("vibrance", -0.3, 0.6, 0.0),
    )


def compute_image_metrics(image_bgr: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    channel_means = image_bgr.mean(axis=(0, 1)).astype(float)
    b_mean, g_mean, r_mean = [float(v) for v in channel_means]

    return {
        "gray_mean": float(gray.mean()),
        "gray_std": float(gray.std()),
        # Truly clipped: <= 2 / >= 253 (not just near-black/white)
        "clipped_black_ratio": float((gray <= 2).mean()),
        "clipped_white_ratio": float((gray >= 253).mean()),
        "blue_red_delta": float((b_mean - r_mean) / 255.0),
        "green_magenta_delta": float(((2.0 * g_mean) - b_mean - r_mean) / 255.0),
        "channel_spread": float((channel_means.max() - channel_means.min()) / 255.0),
        "mean_saturation": float(hsv[..., 1].mean() / 255.0),
        "laplacian_variance": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
    }


def _heuristic_evaluate(metrics: dict[str, float], threshold: float) -> QualityResult:
    mean = metrics["gray_mean"]
    clipped_black = metrics["clipped_black_ratio"]
    clipped_white = metrics["clipped_white_ratio"]
    color_spread = metrics["channel_spread"]
    blue_red_delta = metrics["blue_red_delta"]
    green_mag_delta = metrics["green_magenta_delta"]
    lap_var = metrics["laplacian_variance"]
    saturation = metrics["mean_saturation"]

    # ── Sharpness: normalise to 200 (achievable for a Pi HQ camera JPEG) ─────
    sharpness_score = min(lap_var / 200.0, 1.0)

    # ── Exposure: zero penalty inside [100, 165]; ramp up outside ─────────────
    if 100.0 <= mean <= 165.0:
        exposure_penalty = 0.0
    elif mean < 100.0:
        exposure_penalty = min((100.0 - mean) / 100.0, 1.0) * 0.22
    else:
        exposure_penalty = min((mean - 165.0) / 90.0, 1.0) * 0.22

    # ── Clipping: use truly-clipped threshold (<=2 / >=253) ──────────────────
    clipping_penalty = min(clipped_black + clipped_white, 1.0) * 0.12

    # ── Colour cast ───────────────────────────────────────────────────────────
    color_penalty = min(
        color_spread * 1.2 + abs(blue_red_delta) * 1.8 + abs(green_mag_delta) * 0.8,
        1.0,
    ) * 0.28

    # ── Softness ──────────────────────────────────────────────────────────────
    softness_penalty = (1.0 - sharpness_score) * 0.20

    # ── Saturation floor: very low saturation = demasking failure ─────────────
    if saturation < 0.04:
        saturation_penalty = 0.18
    elif saturation < 0.08:
        saturation_penalty = ((0.08 - saturation) / 0.04) * 0.18
    else:
        saturation_penalty = 0.0

    score = 1.0 - exposure_penalty - clipping_penalty - color_penalty - softness_penalty - saturation_penalty
    score = float(np.clip(score, 0.0, 1.0))

    # ── Feedback ──────────────────────────────────────────────────────────────
    feedback_parts: list[str] = []
    if mean < 100.0:
        feedback_parts.append("image is too dark")
    elif mean > 165.0:
        feedback_parts.append("image is too bright")
    if blue_red_delta > 0.08:
        feedback_parts.append("blue/cyan cast remains")
    elif blue_red_delta < -0.08:
        feedback_parts.append("red/yellow cast remains")
    elif color_spread > 0.18:
        feedback_parts.append("visible channel imbalance")
    if sharpness_score < 0.40:
        feedback_parts.append("output appears soft")
    if saturation < 0.04:
        feedback_parts.append("output is desaturated — possible demasking failure")
    elif saturation < 0.08:
        feedback_parts.append("colours are muted — consider vibrance boost")
    if not feedback_parts:
        feedback_parts.append("good exposure, colour balance, and detail")

    # ── Suggested params ──────────────────────────────────────────────────────
    params = PipelineParams()

    if mean < 100.0:
        params.gamma = 0.75
        params.shadow_lift = 8.0
        params.clahe_clip = 1.8
    elif mean > 165.0:
        params.gamma = 1.3
        params.black_point = 1.2
        params.white_point = 98.7

    if blue_red_delta > 0.08:
        params.wb_clip_percent = 0.9
        params.lab_strength = 1.2
        params.desat_strength = 0.35
        params.desat_sigma = 5.0
    elif blue_red_delta < -0.08:
        params.wb_clip_percent = 0.9
        params.lab_strength = 1.2
    elif color_spread > 0.18:
        params.desat_strength = 0.30
        params.lab_strength = 1.1
        params.wb_clip_percent = 0.7

    if sharpness_score < 0.40:
        params.unsharp_amount = 0.50
        params.unsharp_sigma = 1.0

    if saturation < 0.08:
        params.vibrance = 0.25
        params.lab_strength = 1.0

    if clipped_white > 0.01:
        params.highlight_compression = 0.25

    return QualityResult(
        score=score,
        passed=score >= threshold,
        feedback="; ".join(feedback_parts).capitalize() + ".",
        suggested_params=params,
    )
