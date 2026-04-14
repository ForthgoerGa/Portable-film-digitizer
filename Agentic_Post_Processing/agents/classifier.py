from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import config
from models import FilmType

logger = logging.getLogger(__name__)

_VALID_TYPES = {film_type.value for film_type in FilmType}
_DEFAULT_FILM_TYPE = FilmType.NEGATIVE_35MM

_PROMPT = """You are classifying a scanned film image.
Return ONLY valid JSON:
{"film_type": "negative_35mm"|"negative_120"|"positive_35mm"|"positive_120"}

Classification rules:
- Orange or brown base with inverted-looking tones suggests negative film.
- Dark film borders with normal-looking tones suggest positive film.
- Wider aspect ratios near 3:2 suggest 35mm.
- Squarer aspect ratios suggest 120.
"""


class ClassifierAgent:
    def __init__(
        self,
        model: Any | None = None,
        allow_heuristic_fallback: bool | None = None,
    ) -> None:
        self._model = model if model is not None else _build_default_model()
        self._allow_heuristic_fallback = (
            allow_heuristic_fallback if allow_heuristic_fallback is not None else model is None
        )
        self.last_mode = "uninitialized"

    def classify(self, image_path: Path) -> FilmType:
        image_path = Path(image_path)

        if self._model is None:
            self.last_mode = "heuristic"
            return _heuristic_classify(image_path)

        try:
            response = self._model.generate_content([_PROMPT, _image_part(image_path)])
            payload = json.loads(_response_text(response).strip())
            value = str(payload.get("film_type", "")).strip().lower()
            if value in _VALID_TYPES:
                self.last_mode = config.MODEL_PROVIDER
                return FilmType(value)
            raise ValueError(f"Unsupported film_type: {value}")
        except Exception as exc:
            logger.warning("Classifier failed for %s: %s", image_path.name, exc)
            if self._allow_heuristic_fallback:
                self._model = None
                self.last_mode = "heuristic"
                return _heuristic_classify(image_path)
            self.last_mode = "default"
            return _DEFAULT_FILM_TYPE


def _build_default_model() -> Any | None:
    if config.MODEL_PROVIDER == "openai_compatible" and config.OPENAI_API_KEY and config.OPENAI_BASE_URL:
        try:
            from agents.gateway_model import GatewayModelAdapter
            return GatewayModelAdapter(model_name=config.VLM_MODEL, group=config.MODEL_GROUP)
        except Exception as exc:
            logger.warning("Gateway model adapter unavailable for classifier: %s", exc)

    if not config.GEMINI_API_KEY:
        return None

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError:
        logger.warning("google.genai is unavailable; classifier will use heuristic fallback.")
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
                    max_output_tokens=256,
                    response_mime_type="application/json",
                    thinking_config=self._types.ThinkingConfig(thinking_budget=0),
                ),
            )

    return _ModelAdapter()


def _image_part(image_path: Path) -> Any:
    from google.genai import types  # type: ignore

    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(image_path.suffix.lower(), "image/jpeg")
    return types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type)


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


def _heuristic_classify(image_path: Path) -> FilmType:
    image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        logger.warning("Could not read %s; defaulting to %s", image_path, _DEFAULT_FILM_TYPE.value)
        return _DEFAULT_FILM_TYPE

    height, width = image_bgr.shape[:2]
    ratio = width / max(height, 1)
    negative_format = FilmType.NEGATIVE_35MM if ratio >= 1.25 else FilmType.NEGATIVE_120

    means = image_bgr.mean(axis=(0, 1))
    blue_mean, green_mean, red_mean = [float(v) for v in means]

    border_size = max(4, min(height, width) // 20)
    border = np.concatenate(
        [
            image_bgr[:border_size, :, :].reshape(-1, 3),
            image_bgr[-border_size:, :, :].reshape(-1, 3),
            image_bgr[:, :border_size, :].reshape(-1, 3),
            image_bgr[:, -border_size:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    inner = image_bgr[border_size:-border_size, border_size:-border_size, :]
    if inner.size == 0:
        inner = image_bgr

    border_luma = float(border.mean())
    inner_luma = float(inner.mean())
    dark_border = border_luma < 55.0 and border_luma + 18.0 < inner_luma
    strong_orange_cast = red_mean > green_mean + 12.0 and green_mean > blue_mean + 10.0

    if dark_border and not strong_orange_cast:
        return FilmType.POSITIVE_35MM if ratio >= 1.25 else FilmType.POSITIVE_120
    if strong_orange_cast:
        return negative_format
    return FilmType.POSITIVE_35MM if ratio >= 1.25 else FilmType.POSITIVE_120
