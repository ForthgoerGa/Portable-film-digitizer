from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

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
    def __init__(self, model: Any | None = None) -> None:
        self._model = model if model is not None else _build_default_model()
        self.last_mode = "uninitialized"

    def classify(self, image_path: Path) -> FilmType:
        image_path = Path(image_path)

        if self._model is None:
            logger.warning("No model available for classifier; defaulting to %s", _DEFAULT_FILM_TYPE.value)
            self.last_mode = "default"
            return _DEFAULT_FILM_TYPE

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
        logger.warning("google.genai is unavailable; classifier will return default film type.")
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
