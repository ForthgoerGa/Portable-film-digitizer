"""Visual and metric evaluator for per-frame flat-field correction tuning."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_PROMPT = """You are evaluating a film scan after Bayer-domain flat-field correction.
Focus only on residual illumination defects: visible light spots, vignetting,
uneven backlight, broad blobs, or over-correction. Ignore film grain, scratches,
and real scene content as much as possible.

Return ONLY valid JSON with this exact structure:
{
  "score": <float 0.0 to 1.0>,
  "passed": <true or false>,
  "visible_light_spot": <true or false>,
  "unevenness_level": "<none|subtle|moderate|strong>",
  "feedback": "<one concise sentence>",
  "suggested_flat_strength": <float>,
  "rationale": "<short reason>"
}

Scoring guide:
- 0.90-1.00: no obvious broad illumination artifact.
- 0.75-0.89: minor residual unevenness, acceptable.
- 0.50-0.74: visible light spot or gradient remains.
- below 0.50: strong light spot, vignetting, or over-correction.

Adjustment guide:
- If a bright center/blob remains, increase flat strength by 0.05 to 0.20.
- If the center is too dark or edges look artificially bright, decrease strength.
- Keep changes modest unless the artifact is strong.
"""


@dataclass(frozen=True)
class EvaluationResult:
    score: float
    passed: bool
    visible_light_spot: bool
    unevenness_level: str
    feedback: str
    suggested_flat_strength: float
    rationale: str
    mode: str
    metrics: dict[str, float]


class FlatFieldEvaluatorAgent:
    """Evaluate residual flat-field artifacts and suggest a new strength."""

    def __init__(
        self,
        env_path: str | Path | None,
        threshold: float,
        min_strength: float,
        max_strength: float,
        enabled: bool = True,
    ) -> None:
        self.env_path = Path(env_path) if env_path else None
        self.threshold = float(threshold)
        self.min_strength = float(min_strength)
        self.max_strength = float(max_strength)
        self.enabled = enabled
        if self.env_path:
            load_env_file(self.env_path)

    def evaluate(
        self,
        image_path: str | Path,
        rgb_linear: np.ndarray,
        current_strength: float,
        rois: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        metrics = compute_uniformity_metrics(rgb_linear, rois=rois)
        if self.enabled:
            try:
                return self._evaluate_with_model(
                    image_path=Path(image_path),
                    metrics=metrics,
                    current_strength=float(current_strength),
                )
            except Exception as exc:
                fallback = heuristic_evaluate(
                    metrics=metrics,
                    current_strength=float(current_strength),
                    threshold=self.threshold,
                    min_strength=self.min_strength,
                    max_strength=self.max_strength,
                )
                return EvaluationResult(
                    **{
                        **asdict(fallback),
                        "mode": f"heuristic_after_model_error: {type(exc).__name__}",
                    }
                )

        return heuristic_evaluate(
            metrics=metrics,
            current_strength=float(current_strength),
            threshold=self.threshold,
            min_strength=self.min_strength,
            max_strength=self.max_strength,
        )

    def _evaluate_with_model(
        self,
        image_path: Path,
        metrics: dict[str, float],
        current_strength: float,
    ) -> EvaluationResult:
        provider = os.getenv("MODEL_PROVIDER", "gemini").strip().lower()
        prompt = self._build_prompt(metrics, current_strength)
        errors: list[str] = []
        if provider == "openai_compatible":
            payload = _call_openai_compatible(image_path, prompt)
            mode = "openai_compatible"
        else:
            try:
                payload = _call_gemini(image_path, prompt)
                mode = "gemini"
            except Exception as exc:
                errors.append(f"gemini:{type(exc).__name__}")
                if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_BASE_URL"):
                    payload = _call_openai_compatible(image_path, prompt)
                    mode = "openai_compatible_after_gemini_error"
                else:
                    raise RuntimeError("; ".join(errors)) from exc

        score = _clamp(float(payload.get("score", 0.0)), 0.0, 1.0)
        suggested = _clamp(
            float(payload.get("suggested_flat_strength", current_strength)),
            self.min_strength,
            self.max_strength,
        )
        return EvaluationResult(
            score=score,
            passed=bool(payload.get("passed", score >= self.threshold)) and score >= self.threshold,
            visible_light_spot=bool(payload.get("visible_light_spot", score < self.threshold)),
            unevenness_level=str(payload.get("unevenness_level", "unknown")),
            feedback=str(payload.get("feedback", "")).strip(),
            suggested_flat_strength=suggested,
            rationale=str(payload.get("rationale", "")).strip(),
            mode=mode,
            metrics=metrics,
        )

    def _build_prompt(self, metrics: dict[str, float], current_strength: float) -> str:
        context = {
            "current_flat_strength": current_strength,
            "allowed_strength_range": [self.min_strength, self.max_strength],
            "pass_threshold": self.threshold,
            "uniformity_metrics": metrics,
        }
        return f"{_PROMPT}\nContext:\n{json.dumps(context, indent=2)}"


def compute_uniformity_metrics(
    rgb_linear: np.ndarray,
    rois: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Compute low-frequency residual illumination metrics from linear RGB."""

    rgb = np.clip(rgb_linear.astype(np.float32), 0.0, 1.0)
    luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    low = _low_frequency_luma(luma)
    median = max(float(np.median(low)), 1e-6)
    p2 = float(np.percentile(low, 2.0))
    p5 = float(np.percentile(low, 5.0))
    p95 = float(np.percentile(low, 95.0))
    p98 = float(np.percentile(low, 98.0))

    h, w = low.shape
    cy0, cy1 = int(h * 0.30), int(h * 0.70)
    cx0, cx1 = int(w * 0.30), int(w * 0.70)
    center = low[cy0:cy1, cx0:cx1]
    edge_mask = np.ones((h, w), dtype=bool)
    edge_mask[cy0:cy1, cx0:cx1] = False
    edge = low[edge_mask]
    center_edge_ratio = float(center.mean() / max(float(edge.mean()), 1e-6))

    metrics: dict[str, float] = {
        "low_frequency_cv": float(low.std() / max(float(low.mean()), 1e-6)),
        "low_frequency_p95_p5_span": float((p95 - p5) / median),
        "hotspot_contrast": float(max(abs(p98 - median), abs(median - p2)) / median),
        "center_edge_ratio": center_edge_ratio,
        "low_frequency_p2": p2,
        "low_frequency_p98": p98,
        "mean_luma": float(luma.mean()),
        "clipped_high_fraction": float((rgb >= 1.0).mean()),
        "clipped_low_fraction": float((rgb <= 0.0).mean()),
    }
    metrics.update(_roi_uniformity_metrics(luma, rois or []))
    return metrics


def heuristic_evaluate(
    metrics: dict[str, float],
    current_strength: float,
    threshold: float,
    min_strength: float,
    max_strength: float,
) -> EvaluationResult:
    cv = metrics["low_frequency_cv"]
    span = metrics["low_frequency_p95_p5_span"]
    hotspot = metrics["hotspot_contrast"]
    center_ratio = metrics["center_edge_ratio"]
    clipping_penalty = min(metrics["clipped_high_fraction"] * 6.0, 0.25)

    penalty = min(cv * 1.7 + span * 0.7 + hotspot * 0.55, 1.0) + clipping_penalty
    score = _clamp(1.0 - penalty, 0.0, 1.0)

    if center_ratio < 0.94:
        suggested = current_strength - 0.10
        rationale = "center is darker than edges; reduce flat strength"
    elif score < threshold:
        step = 0.20 if hotspot > 0.45 or span > 0.75 else 0.10
        suggested = current_strength + step
        rationale = "residual broad illumination remains; increase flat strength"
    else:
        suggested = current_strength
        rationale = "uniformity metrics pass"

    suggested = _clamp(suggested, min_strength, max_strength)
    level = "none"
    if score < 0.5:
        level = "strong"
    elif score < threshold:
        level = "moderate"
    elif score < 0.9:
        level = "subtle"

    return EvaluationResult(
        score=score,
        passed=score >= threshold,
        visible_light_spot=score < threshold,
        unevenness_level=level,
        feedback=(
            f"low-frequency CV={cv:.3f}, span={span:.3f}, "
            f"hotspot={hotspot:.3f}, center/edge={center_ratio:.3f}"
        ),
        suggested_flat_strength=suggested,
        rationale=rationale,
        mode="heuristic",
        metrics=metrics,
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _call_gemini(image_path: Path, prompt: str) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed") from exc

    model = os.getenv("VLM_MODEL") or os.getenv("GEMINI_VLM_MODEL") or "gemini-2.5-flash"
    client = genai.Client(api_key=api_key)
    image_bytes, mime_type = _model_image_bytes(image_path)
    image = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=768,
            response_mime_type="application/json",
        ),
    )
    return _json_from_text(getattr(response, "text", "") or "")


def _call_openai_compatible(image_path: Path, prompt: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    model = os.getenv("EVALUATOR_MODEL") or os.getenv("VLM_MODEL") or os.getenv("DECISION_MODEL") or "gpt-4o-mini"
    if model.lower().startswith("gemini"):
        model = os.getenv("DECISION_MODEL") or os.getenv("DECISION_MODEL_FALLBACK") or "gpt-4o-mini"
    if not api_key or not base_url:
        raise RuntimeError("OPENAI_API_KEY or OPENAI_BASE_URL is not configured")

    image_bytes, mime_type = _model_image_bytes(image_path)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 768,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                    },
                ],
            }
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = payload["choices"][0]["message"]["content"]
    return _json_from_text(text)


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in model response: {text[:120]}")
    return json.loads(match.group(0))


def _model_image_bytes(image_path: Path, max_side: int = 1280) -> tuple[bytes, str]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return image_path.read_bytes(), "image/png"
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return image_path.read_bytes(), "image/png"
    return encoded.tobytes(), "image/jpeg"


def _low_frequency_luma(luma: np.ndarray, max_side: int = 512) -> np.ndarray:
    h, w = luma.shape
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        small_w = max(1, int(round(w * scale)))
        small_h = max(1, int(round(h * scale)))
        small = cv2.resize(luma.astype(np.float32), (small_w, small_h), interpolation=cv2.INTER_AREA)
    else:
        small = luma.astype(np.float32)
        small_h, small_w = h, w
    sigma = max(float(np.sqrt(small_h**2 + small_w**2)) * 0.055, 5.0)
    return cv2.GaussianBlur(small, (0, 0), sigmaX=sigma, sigmaY=sigma)


def _roi_uniformity_metrics(luma: np.ndarray, rois: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    h, w = luma.shape
    for roi in rois:
        name = str(roi.get("name", ""))
        bbox = roi.get("bbox")
        if not name or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = [int(v) for v in bbox]
        x0, x1 = max(0, min(x0, w)), max(0, min(x1, w))
        y0, y1 = max(0, min(y0, h)), max(0, min(y1, h))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = luma[y0:y1, x0:x1]
        if crop.size < 16:
            continue
        mean = max(float(crop.mean()), 1e-6)
        safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", name.lower())
        out[f"roi_{safe_name}_cv"] = float(crop.std() / mean)
        out[f"roi_{safe_name}_mean"] = float(crop.mean())
    return out


def _clamp(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))
