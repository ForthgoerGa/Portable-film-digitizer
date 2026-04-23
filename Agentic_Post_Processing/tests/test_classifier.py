from __future__ import annotations

from unittest.mock import MagicMock

from agents.classifier import ClassifierAgent
from models import FilmType


def _make_mock_model(response_text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    return mock_model


def test_classifier_returns_negative_35mm(sample_negative_image_path):
    agent = ClassifierAgent(model=_make_mock_model('{"film_type": "negative_35mm"}'))
    assert agent.classify(sample_negative_image_path) == FilmType.NEGATIVE_35MM


def test_classifier_returns_positive_35mm(sample_positive_image_path):
    agent = ClassifierAgent(model=_make_mock_model('{"film_type": "positive_35mm"}'))
    assert agent.classify(sample_positive_image_path) == FilmType.POSITIVE_35MM


def test_classifier_returns_negative_120(sample_negative_image_path):
    agent = ClassifierAgent(model=_make_mock_model('{"film_type": "negative_120"}'))
    assert agent.classify(sample_negative_image_path) == FilmType.NEGATIVE_120


def test_classifier_defaults_on_bad_json(sample_negative_image_path):
    agent = ClassifierAgent(model=_make_mock_model("sorry"), allow_heuristic_fallback=False)
    assert agent.classify(sample_negative_image_path) == FilmType.NEGATIVE_35MM


def test_classifier_calls_model_once(sample_negative_image_path):
    mock_model = _make_mock_model('{"film_type": "positive_120"}')
    agent = ClassifierAgent(model=mock_model)
    agent.classify(sample_negative_image_path)
    assert mock_model.generate_content.call_count == 1
