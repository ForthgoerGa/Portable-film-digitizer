from __future__ import annotations

import json
import logging
from typing import Any

import config
from models import PipelineParams

logger = logging.getLogger(__name__)

_DECISION_PROMPT = """You are a high-level strategy model for an agentic film post-processing pipeline.
You are not the default fast path. You are called only for difficult cases.

Given the current film type, iteration history, current params, evaluator metrics, and feedback,
return ONLY valid JSON with this exact structure:
{
  "use_escalated_strategy": true,
  "reason": "<one concise sentence>",
  "suggested_params": {
    "wb_clip_percent": <float>,
    "black_point": <float>,
    "white_point": <float>,
    "clahe_clip": <float>,
    "clahe_grid": <int>,
    "unsharp_amount": <float>,
    "unsharp_sigma": <float>,
    "desat_strength": <float>,
    "desat_sigma": <float>,
    "shadow_lift": <float>,
    "gamma": <float>,
    "lab_strength": <float>,
    "highlight_compression": <float>,
    "vibrance": <float>
  }
}

Guidance:
- Prefer conservative but meaningful corrections.
- Do not suggest random drift. Suggest only changes justified by the metrics.
- If the image is heavily clipped in highlights, prioritize highlight compression and tone shaping.
- If there is residual cast, prioritize wb_clip_percent, lab_strength, and desat_strength.
- If detail is soft, adjust unsharp settings carefully.
"""


class DecisionAgent:
    def __init__(self, model: Any | None = None) -> None:
        self._model = model if model is not None else _build_default_model()
        self.last_mode = "disabled" if self._model is None else "gateway"

    def enabled(self) -> bool:
        return self._model is not None

    def suggest(
        self,
        *,
        film_type: str,
        iteration: int,
        score: float,
        feedback: str,
        current_params: dict[str, Any],
        evaluation_metrics: dict[str, Any],
    ) -> tuple[str, PipelineParams] | None:
        if self._model is None:
            return None

        prompt = {
            "film_type": film_type,
            "iteration": iteration,
            "score": score,
            "feedback": feedback,
            "current_params": current_params,
            "evaluation_metrics": evaluation_metrics,
        }
        try:
            response = self._model.generate_content([_DECISION_PROMPT, json.dumps(prompt, indent=2)])
            payload = json.loads(str(getattr(response, 'text', '')).strip())
            reason = str(payload.get("reason", "Escalated strategy applied.")).strip()
            suggested = payload.get("suggested_params", {})
            return reason, _params_from_payload(suggested)
        except Exception as exc:
            logger.warning("DecisionAgent failed: %s", exc)
            return None


def _build_default_model() -> Any | None:
    if not config.DECISION_MODEL_ENABLED:
        return None
    if not config.OPENAI_API_KEY or not config.OPENAI_BASE_URL:
        return None
    try:
        from agents.gateway_model import GatewayModelAdapter
        return GatewayModelAdapter(model_name=config.DECISION_MODEL, group=config.DECISION_MODEL_GROUP)
    except Exception as exc:
        logger.warning("Gateway model adapter unavailable for decision model: %s", exc)
        return None


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
