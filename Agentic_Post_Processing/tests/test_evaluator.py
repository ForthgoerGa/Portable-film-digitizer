from __future__ import annotations

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from agents.evaluator import EvaluatorAgent, compute_image_metrics
from models import PipelineParams, QualityResult


def _make_mock_model(response_text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    return mock_model


@pytest.fixture
def processed_image() -> np.ndarray:
    return np.full((100, 150, 3), 128, dtype=np.uint8)


_GOOD_RESPONSE = json.dumps(
    {
        "score": 0.88,
        "feedback": "Good exposure and color balance.",
        "suggested_params": {
            "wb_clip_percent": 0.5,
            "black_point": 0.8,
            "white_point": 99.2,
            "clahe_clip": 1.4,
            "unsharp_amount": 0.35,
            "desat_strength": 0.20,
            "desat_sigma": 7.0,
            "shadow_lift": 0.0,
        },
    }
)

_TUNE_RESPONSE = json.dumps(
    {
        "score": 0.55,
        "feedback": "Shadows too dark, needs lift.",
        "suggested_params": {
            "wb_clip_percent": 0.6,
            "black_point": 1.0,
            "white_point": 98.5,
            "clahe_clip": 1.8,
            "unsharp_amount": 0.4,
            "desat_strength": 0.25,
            "desat_sigma": 8.0,
            "shadow_lift": 12.0,
        },
    }
)


def test_evaluator_passes_high_score(processed_image: np.ndarray):
    agent = EvaluatorAgent(model=_make_mock_model(_GOOD_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert isinstance(result, QualityResult)
    assert result.score == 0.88
    assert result.passed is True
    assert result.feedback == "Good exposure and color balance."


def test_evaluator_fails_low_score(processed_image: np.ndarray):
    agent = EvaluatorAgent(model=_make_mock_model(_TUNE_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert result.passed is False
    assert result.score == 0.55


def test_evaluator_suggested_params_populated(processed_image: np.ndarray):
    agent = EvaluatorAgent(model=_make_mock_model(_TUNE_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert result.suggested_params.shadow_lift == 12.0
    assert result.suggested_params.clahe_clip == 1.8


def test_evaluator_fallback_on_bad_json(processed_image: np.ndarray):
    agent = EvaluatorAgent(model=_make_mock_model("not json"), threshold=0.75, allow_heuristic_fallback=False)
    result = agent.evaluate(processed_image)
    assert result.passed is False
    assert result.score == 0.0
    assert isinstance(result.suggested_params, PipelineParams)


def test_evaluator_calls_model_once(processed_image: np.ndarray):
    mock_model = _make_mock_model(_GOOD_RESPONSE)
    agent = EvaluatorAgent(model=mock_model, threshold=0.75)
    agent.evaluate(processed_image)
    assert mock_model.generate_content.call_count == 1


def test_compute_image_metrics_detects_blue_cast():
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    image[:, :, 0] = 180
    image[:, :, 1] = 110
    image[:, :, 2] = 60
    metrics = compute_image_metrics(image)
    assert metrics["blue_red_delta"] > 0.4
    assert metrics["channel_spread"] > 0.4
